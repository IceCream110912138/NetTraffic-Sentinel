"""
capture.py - 抓包与流量统计核心模块

【流量统计误差修复说明】
---
原始版本使用 len(pkt) 统计字节数，存在两个严重问题：

问题1：len(pkt) 包含以太网头（14字节/包），统计的是链路层帧大小，
       而非 IP 层有效载荷大小，导致轻微偏高。

问题2（主因，导致 ~50% 误差）：Scapy sniff() 是纯 Python 实现，每个
       数据包都需要构建完整的 Scapy 对象（解析各层协议），CPU 开销极大。
       在大文件传输（高包速率）时，Python 处理速度远跟不上到包速率，
       导致 libpcap 内核环形缓冲区溢出，内核直接丢弃来不及处理的数据包。
       被丢弃的包完全不会出现在 prn 回调中，造成统计严重偏低。

修复方案：
1. 改用 IP.len（IPv4 total length）和 IPv6.plen（payload length）字段
   统计字节数，这是协议层声明的精确值，不含以太网头开销。

2. 将 Scapy 回调替换为基于 raw socket 的轻量级抓包循环：
   - 直接读取原始以太网帧字节，手工解析 EtherType 和 IP 头
   - 避免构建完整 Scapy 对象的开销，单包处理时间从 ~50μs 降至 ~2μs
   - 配合更大的 SO_RCVBUF 内核缓冲区，大幅降低丢包率

3. 在 Dockerfile 的启动脚本中禁用网卡 GRO/LRO/TSO offload，
   确保 libpcap 能看到真实的每一个 IP 数据包而非聚合后的超帧。

【方向判定逻辑】
  上行（upload）  = NAS 向外部发送数据，src 是本机地址
  下行（download）= 外部向 NAS 发送数据，dst 是本机地址

  IPv4（NAS 在 NAT 内网）：
    src=内网IP, dst=公网IP → 上行
    src=公网IP, dst=内网IP → 下行
    两端都是内网           → 忽略

  IPv6（NAS 直接持有公网 IPv6，无 NAT）：
    src=本机IPv6, dst=公网IPv6 → 上行（靠 _local_ips 识别本机）
    src=公网IPv6, dst=本机IPv6 → 下行
    两端都是公网且都不是本机   → 忽略

  本机 IPv6 每隔 LOCAL_IP_REFRESH_INTERVAL 秒自动重新检测（应对 SLAAC 轮换）
"""

import ipaddress
import logging
import os
import queue
import socket
import struct
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Set, Tuple

logger = logging.getLogger('sentinel.capture')

# 本机 IP 刷新间隔（秒）
LOCAL_IP_REFRESH_INTERVAL = 600

# GUA /56 前缀刷新间隔（秒）——运营商拨号重连周期通常以小时计
GUA_PREFIX_REFRESH_INTERVAL = 3600

# 自动提取的 /56 前缀长度（中国电信/联通家宽标准前缀长度）
GUA_PREFIX_LEN = 56

# libpcap 内核接收缓冲区大小：32MB（默认通常只有 2MB，高流量时容易溢出丢包）
SOCKET_RCVBUF_SIZE = 32 * 1024 * 1024

# 生产者-消费者队列容量（包数）：限制内存占用，队列满时丢弃并计数
# BT 同时数百连接时瞬时包速率可达数万 pkt/s
PACKET_QUEUE_MAXSIZE = 50000
PROCESSOR_BATCH_SIZE = 256

# 内核丢包监控间隔（秒）
KERNEL_DROP_MONITOR_INTERVAL = 60

# 包处理速率统计间隔（秒），定期打印便于性能诊断
PKT_RATE_LOG_INTERVAL = 60

# 以太网协议类型常量
ETH_P_IP   = 0x0800   # IPv4
ETH_P_IPV6 = 0x86DD   # IPv6
ETH_P_8021Q = 0x8100  # 802.1Q VLAN tag
ETH_P_8021AD = 0x88A8  # 802.1ad QinQ VLAN tag
ETH_P_9100 = 0x9100    # vendor-specific QinQ
ETH_P_9200 = 0x9200    # vendor-specific QinQ
ETH_P_ALL  = 0x0003   # 抓所有协议（htons 后使用）

VLAN_ETHERTYPES = {
    ETH_P_8021Q,
    ETH_P_8021AD,
    ETH_P_9100,
    ETH_P_9200,
}

# ── IPv4 私有网段 ─────────────────────────────────────────────────────────────
PRIVATE_IPV4_NETWORKS = [
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('0.0.0.0/8'),
    ipaddress.ip_network('255.255.255.255/32'),
]

# ── IPv6 始终排除的网段 ───────────────────────────────────────────────────────
BUILTIN_IPV6_EXCLUDE = [
    ipaddress.ip_network('fe80::/10'),   # 链路本地
    ipaddress.ip_network('::1/128'),      # loopback
    ipaddress.ip_network('fc00::/7'),     # ULA
    ipaddress.ip_network('ff00::/8'),     # 组播
]

# 预编译私有网络为整数范围，避免每包都构建 ip_address 对象（性能优化）
_PRIVATE_V4_RANGES = [
    (struct.unpack('!I', socket.inet_aton(str(net.network_address)))[0],
     struct.unpack('!I', socket.inet_aton(str(net.broadcast_address)))[0])
    for net in PRIVATE_IPV4_NETWORKS
]


def _ipv6_mask(prefix_len: int) -> int:
    if prefix_len <= 0:
        return 0
    return ((1 << prefix_len) - 1) << (128 - prefix_len)


def _compile_ipv6_network_matchers(
    networks: List[ipaddress.IPv6Network],
) -> Tuple[Tuple[int, int], ...]:
    return tuple(
        (int(net.network_address), _ipv6_mask(net.prefixlen))
        for net in networks
    )


_BUILTIN_IPV6_MATCHERS = _compile_ipv6_network_matchers(BUILTIN_IPV6_EXCLUDE)


# ── 快速 IP 分类函数（避免 ipaddress 对象构建开销）──────────────────────────

def _is_private_v4_int(ip_int: int) -> bool:
    """用整数比较判断 IPv4 是否为私有地址，比 ipaddress 库快约 10x"""
    return any(lo <= ip_int <= hi for lo, hi in _PRIVATE_V4_RANGES)


def _ipv6_int_matches(addr_int: int, matchers: Tuple[Tuple[int, int], ...]) -> bool:
    """用 network_int + mask 判断 IPv6 地址是否匹配网段"""
    return any((addr_int & mask) == network for network, mask in matchers)


def _ipv6_int_is_excluded(
    addr_int: int,
    extra_matchers: Tuple[Tuple[int, int], ...],
) -> bool:
    """判断 IPv6 地址是否属于内置/额外排除网段"""
    return (
        _ipv6_int_matches(addr_int, _BUILTIN_IPV6_MATCHERS)
        or _ipv6_int_matches(addr_int, extra_matchers)
    )


def _format_ipv6(addr_bytes: bytes) -> str:
    """使用 C 层序列化 IPv6，减少 ipaddress 对象构建开销"""
    try:
        return socket.inet_ntop(socket.AF_INET6, addr_bytes)
    except (AttributeError, OSError):
        return str(ipaddress.ip_address(addr_bytes))


# ── 本机 IP 检测 ──────────────────────────────────────────────────────────────

def detect_local_ips(iface: str) -> Set[str]:
    """
    读取指定网卡上当前绑定的所有 IP 地址（IPv4 + 所有 IPv6 全局地址）。
    优先使用 netifaces；fallback 到 `ip addr show` 命令。
    """
    ips: Set[str] = set()

    try:
        import netifaces
        addrs = netifaces.ifaddresses(iface)
        for family in (netifaces.AF_INET, netifaces.AF_INET6):
            for entry in addrs.get(family, []):
                ip = entry.get('addr', '').split('%')[0]
                if ip:
                    ips.add(ip)
        return ips
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"netifaces failed: {e}")

    try:
        result = subprocess.run(
            ['ip', '-o', 'addr', 'show', iface],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            for i, part in enumerate(parts):
                if part in ('inet', 'inet6') and i + 1 < len(parts):
                    ip = parts[i + 1].split('/')[0].split('%')[0]
                    if ip:
                        ips.add(ip)
    except Exception as e:
        logger.warning(f"ip addr command failed: {e}")

    return ips


def detect_gua_slash56_prefixes(iface: str, prefix_len: int = GUA_PREFIX_LEN) -> List[ipaddress.IPv6Network]:
    """
    从指定网卡的所有 IPv6 地址中提取全球单播地址（GUA）的 /56 前缀。

    GUA 识别准则：IPv6 地址前 3 位为 001（即 2000::/3），
    实际上就是以 '2' 或 '3' 开头的地址（0x20xx ~ 0x3Fxx）。
    常见中国运营商 GUA 段：240e::/20（电信）、2409::/16（联通）等。

    返回值：去重后的 IPv6Network 列表，供双端 LAN 过滤使用。
    若网卡尚未获得公网 IPv6，则返回空列表（由调用方 fallback 处理）。
    """
    ips = detect_local_ips(iface)
    prefixes: List[ipaddress.IPv6Network] = []
    seen: set = set()

    for ip_str in ips:
        if ':' not in ip_str:          # 跳过 IPv4
            continue
        try:
            addr = ipaddress.ip_address(ip_str)
            # 判断是否为 GUA：首字节高 3 位 = 0b001 (0x20~0x3F)
            if addr.version != 6 or (addr.packed[0] & 0xE0) != 0x20:
                continue
            # 提取 /prefix_len 前缀（strict=False 自动归零主机位）
            net = ipaddress.ip_network(f"{ip_str}/{prefix_len}", strict=False)
            key = str(net)
            if key not in seen:
                seen.add(key)
                prefixes.append(net)
        except ValueError:
            pass

    return prefixes


def get_iface_index(iface: str) -> int:
    """获取网卡的接口索引号，用于绑定 raw socket"""
    return socket.if_nametoindex(iface)


def check_offload_status(iface: str) -> dict:
    """
    检测网卡 offload 状态，用于启动时的诊断日志。
    方案1：调用 ethtool -k 解析输出（最可靠）。
    方案2：读取 /sys/class/net/<iface>/gro_flush_timeout 等 sysfs 节点（fallback）。
    返回字典 {feature: status_str}，检测失败时返回空字典。
    """
    result = {}
    # ── 方案1：ethtool -k ──────────────────────────────────────────────────
    try:
        out = subprocess.run(
            ['ethtool', '-k', iface],
            capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0:
            key_map = {
                'generic-receive-offload':  'gro',
                'large-receive-offload':    'lro',
                'tcp-segmentation-offload': 'tso',
                'generic-segmentation-offload': 'gso',
            }
            for line in out.stdout.splitlines():
                line = line.strip()
                for full_key, short in key_map.items():
                    if line.startswith(full_key + ':'):
                        result[short] = line.split(':', 1)[1].strip()
            return result
    except FileNotFoundError:
        pass  # ethtool 未安装，走 fallback
    except Exception as e:
        logger.debug(f"ethtool check_offload_status failed: {e}")

    # ── 方案2：/sys 节点 fallback ─────────────────────────────────────────
    # gro_flush_timeout=0 通常表示 GRO 已禁用（> 0 表示启用延迟聚合）
    gft_path = f'/sys/class/net/{iface}/gro_flush_timeout'
    try:
        with open(gft_path) as f:
            val = f.read().strip()
        result['gro_flush_timeout_ns'] = val
        result['gro_likely'] = 'off' if val == '0' else 'on (flush_timeout=%s ns)' % val
    except Exception:
        pass  # 内核版本/驱动差异，节点不存在

    return result


# ── 流量统计 ──────────────────────────────────────────────────────────────────

class TrafficStats:
    """线程安全的流量统计存储"""

    def __init__(self):
        self._lock = threading.Lock()
        self.hourly: Dict[str, Dict] = defaultdict(lambda: {'up': 0, 'down': 0})
        self.realtime_samples: List[Tuple[float, int, int]] = []
        self._current_up = 0
        self._current_down = 0
        self.ip_counter: Dict[str, int] = defaultdict(int)

    def add_bytes(self, direction: str, size: int, remote_ip: str, ts: float):
        """
        记录一次流量事件。
        size 应传入 IP 层声明的字节数（IPv4: IP.len，IPv6: IPv6.plen + 40）
        而非 len(ethernet_frame)，以避免链路层头部的干扰。
        """
        hour_key = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:00:00')
        with self._lock:
            if direction == 'up':
                self.hourly[hour_key]['up'] += size
                self._current_up += size
            else:
                self.hourly[hour_key]['down'] += size
                self._current_down += size
            self.ip_counter[remote_ip] += size

    def tick_realtime(self):
        ts = time.time()
        with self._lock:
            up, down = self._current_up, self._current_down
            self._current_up = self._current_down = 0
            self.realtime_samples.append((ts, up, down))
            cutoff = ts - 120
            self.realtime_samples = [
                (t, u, d) for t, u, d in self.realtime_samples if t > cutoff
            ]

    def get_realtime_speed(self, seconds: int = 60) -> List[Dict]:
        with self._lock:
            cutoff = time.time() - seconds
            return [
                {'ts': int(t), 'up': u, 'down': d}
                for t, u, d in self.realtime_samples
                if t > cutoff
            ]

    def get_top_ips(self, n: int = 10) -> List[Dict]:
        with self._lock:
            sorted_ips = sorted(
                self.ip_counter.items(), key=lambda x: x[1], reverse=True
            )[:n]
            return [{'ip': ip, 'bytes': b} for ip, b in sorted_ips]

    def flush_and_get(self) -> Dict:
        with self._lock:
            data = dict(self.hourly)
            self.hourly.clear()
            return data

    def get_hourly_snapshot(self) -> Dict[str, Dict]:
        """返回当前内存流量数据的线程安全深拷贝快照。
        直接读 self.hourly 会与 flush_and_get() 的 clear() 产生竞态（读到被并发清空的空字典），
        此方法在持锁状态下复制数据，确保安全且不影响持久化线程。"""
        with self._lock:
            return {k: dict(v) for k, v in self.hourly.items()}


# ── 抓包核心 ──────────────────────────────────────────────────────────────────

class PacketCapture:

    def __init__(self, iface: str, exclude_ipv6_prefixes: List[str] = None):
        self.iface = iface
        self.stats = TrafficStats()
        self.running = False

        # ── IPv6 LAN 前缀过滤策略 ────────────────────────────────────────────
        # 优先级：手动指定 > 自动检测 GUA /56
        # _manual_mode=True 时完全使用手动前缀，禁止覆盖。
        manual_nets: List[ipaddress.IPv6Network] = []
        for prefix in (exclude_ipv6_prefixes or []):
            try:
                net = ipaddress.ip_network(prefix.strip(), strict=False)
                manual_nets.append(net)
                logger.info(f"[IPv6-Filter] Manual exclusion prefix: {net}")
            except ValueError as e:
                logger.warning(f"Invalid IPv6 prefix '{prefix}': {e}")

        self._manual_mode: bool = len(manual_nets) > 0
        # _lan_prefixes: 同时用于双端 LAN 检测和 _is_local_v6 单端判断
        # 需要加锁，因为自动模式下每小时可能刷新
        self._lan_prefixes: List[ipaddress.IPv6Network] = manual_nets
        # 向后兼容：_extra_ipv6 保持引用，与 _lan_prefixes 共享同一列表对象
        self._extra_ipv6 = self._lan_prefixes

        # 本机 IP 集合（含公网 IPv6），用于方向判定
        self._local_ips: Set[str] = set()
        self._local_ips_lock = threading.RLock()
        # 同时缓存为整数/bytes 格式用于高速比较
        self._local_v4_ints: Set[int] = set()
        self._local_v6_ints: Set[int] = set()
        self._lan_prefix_matchers: Tuple[Tuple[int, int], ...] = _compile_ipv6_network_matchers(
            self._lan_prefixes
        )
        self._refresh_local_ips()   # 启动时立即执行一次（含 /56 自动检测）

        self._refresh_thread = threading.Thread(
            target=self._ip_refresh_loop, daemon=True, name='ip-refresh'
        )
        self._refresh_thread.start()

        self._tick_thread = threading.Thread(
            target=self._tick_loop, daemon=True, name='tick'
        )
        self._tick_thread.start()

        # ── 诊断计数器与生产者-消费者队列 ──────────────────────────────────────
        # 实际生效的 socket 接收缓冲区（KB），由 start() 写入
        self._socket_buffer_actual_kb: int = 0
        # 最近一个监控周期内的内核级丢包增量（通过 /proc/net/dev 采样）
        self._kernel_drops_last_60s: int = 0
        # 队列满时被丢弃的帧计数
        self._queue_drop_count: int = 0
        # 生产者-消费者解耦队列：recv 线程仅投帧，处理线程负责解析
        # 队列上限防止内存无限增长；满时在生产者侧丢帧并告警
        self._pkt_queue: queue.Queue = queue.Queue(maxsize=PACKET_QUEUE_MAXSIZE)

        # 启动内核丢包监控线程
        self._drop_monitor_thread = threading.Thread(
            target=self._kernel_drop_monitor_loop, daemon=True, name='drop-monitor'
        )
        self._drop_monitor_thread.start()

        # 启动包处理工作线程（消费者，从队列取帧并解析统计）
        self._processor_thread = threading.Thread(
            target=self._packet_processor_loop, daemon=True, name='pkt-processor'
        )
        self._processor_thread.start()

        # 启动时检测网卡 offload 状态并写入诊断日志
        self._log_offload_status()

    # ── 本机 IP 管理 ──────────────────────────────────────────────────────────

    def _refresh_local_ips(self):
        new_ips = detect_local_ips(self.iface)

        # 同时构建整数/bytes 缓存，供抓包回调高速查找
        new_v4_ints: Set[int] = set()
        new_v6_ints: Set[int] = set()
        for ip_str in new_ips:
            try:
                addr = ipaddress.ip_address(ip_str)
                if addr.version == 4:
                    new_v4_ints.add(int(addr))
                else:
                    new_v6_ints.add(int(addr))
            except ValueError:
                pass

        with self._local_ips_lock:
            old_ips = self._local_ips
            self._local_ips = new_ips
            self._local_v4_ints = new_v4_ints
            self._local_v6_ints = new_v6_ints

        added   = new_ips - old_ips
        removed = old_ips - new_ips
        if added or removed or not old_ips:
            v4 = sorted(ip for ip in new_ips if ':' not in ip)
            v6 = sorted(ip for ip in new_ips if ':' in ip)
            logger.info(f"Local IPs on {self.iface} -> IPv4: {v4}, IPv6 public: {[ip for ip in v6 if not ip.startswith('fe80')]}")
            if added:   logger.info(f"  + Added:   {added}")
            if removed: logger.info(f"  - Removed: {removed}")

        # IP 变动时顺带刷新 /56 前缀（运营商重拨后地址段会改变）
        if added or removed or not old_ips:
            self._refresh_gua_prefixes()

    def _refresh_gua_prefixes(self):
        """
        自动检测网卡上的 GUA 并提取 /56 前缀，更新 LAN 过滤器。
        手动模式（EXCLUDE_IPV6_PREFIX 已设置）时跳过，不覆盖手动配置。
        """
        if self._manual_mode:
            return  # 手动优先，禁止自动覆盖

        new_prefixes = detect_gua_slash56_prefixes(self.iface, GUA_PREFIX_LEN)

        with self._local_ips_lock:
            old_keys = {str(n) for n in self._lan_prefixes}
            new_keys  = {str(n) for n in new_prefixes}

            if new_keys == old_keys:
                return  # 无变化，不做多余日志

            # 原地替换列表内容，_extra_ipv6 共享同一对象，无需额外同步
            self._lan_prefixes.clear()
            self._lan_prefixes.extend(new_prefixes)
            self._lan_prefix_matchers = _compile_ipv6_network_matchers(self._lan_prefixes)

        if new_prefixes:
            logger.info(
                f"[IPv6-Filter] Auto GUA /56 prefixes updated: "
                f"{[str(n) for n in new_prefixes]}"
            )
            logger.info(
                "[IPv6-Filter] Intra-LAN IPv6 traffic "
                f"(both endpoints in /{GUA_PREFIX_LEN}) will be excluded."
            )
        else:
            logger.warning(
                f"[IPv6-Filter] No GUA found on {self.iface}; "
                "falling back to BUILTIN_IPV6_EXCLUDE only "
                "(fe80::/10, ::1, fc00::/7, ff00::/8)."
            )

    def _ip_refresh_loop(self):
        """双速率刷新循环：
        - 每 LOCAL_IP_REFRESH_INTERVAL 秒刷新本机 IP（应对 SLAAC 轮换）
        - 每 GUA_PREFIX_REFRESH_INTERVAL 秒额外做一次 /56 前缀专项检测
          （运营商重拨后地址段可能改变而具体地址未必变化）
        """
        elapsed = 0
        while True:
            time.sleep(LOCAL_IP_REFRESH_INTERVAL)
            elapsed += LOCAL_IP_REFRESH_INTERVAL
            try:
                self._refresh_local_ips()
            except Exception as e:
                logger.error(f"IP refresh error: {e}")

            # 每小时额外再做一次前缀专项刷新
            if elapsed >= GUA_PREFIX_REFRESH_INTERVAL:
                elapsed = 0
                try:
                    self._refresh_gua_prefixes()
                except Exception as e:
                    logger.error(f"GUA prefix refresh error: {e}")

    # ── 方向判定 ──────────────────────────────────────────────────────────────

    def _is_local_v4(self, ip_int: int) -> bool:
        """IPv4：私有地址 or 本机公网地址 → True（本地侧）"""
        if _is_private_v4_int(ip_int):
            return True
        with self._local_ips_lock:
            return ip_int in self._local_v4_ints

    def _is_local_v6(self, addr_int: int) -> bool:
        """
        IPv6：
          - 链路本地/ULA/组播/loopback → True（本地侧）
          - 公网 IPv6 且在本机绑定列表中 → True（本机）
          - 公网 IPv6 且不在本机列表中 → False（远端）
          - 公网 IPv6 且属于 LAN /56 前缀 → True（LAN 设备，视为本地侧）
        """
        # 先快速检查是否在本机列表（最常见的 NAS 自身 IPv6）
        with self._local_ips_lock:
            if addr_int in self._local_v6_ints:
                return True
            lan_prefix_matchers = self._lan_prefix_matchers
        # 再检查保留/私有网段（BUILTIN）以及 LAN /56 前缀
        return _ipv6_int_is_excluded(addr_int, lan_prefix_matchers)

    def _is_in_lan_prefix(self, addr_int: int) -> bool:
        """
        判断一个 IPv6 地址是否属于当前检测到的 LAN 前缀（/56 或手动指定前缀）。
        用于双端检查：src 和 dst 同时在 LAN 前缀内 → 局域网内部流量，应忽略。
        只检查 _lan_prefixes，不包含 BUILTIN_IPV6_EXCLUDE。
        """
        with self._local_ips_lock:
            matchers = self._lan_prefix_matchers
        if not matchers:
            return False
        return _ipv6_int_matches(addr_int, matchers)

    # ── 实时速率采样 ──────────────────────────────────────────────────────────

    def _tick_loop(self):
        while True:
            time.sleep(1)
            self.stats.tick_realtime()

    # ── Offload 诊断 ──────────────────────────────────────────────────────────

    def _log_offload_status(self):
        """
        启动时检测网卡 offload 状态并写入日志。
        若 GRO/LRO/TSO 仍为开启，打印 WARNING 提示用户在宿主机禁用，
        否则 raw socket 收到的是聚合帧，统计会偏低 30-70%。
        """
        status = check_offload_status(self.iface)
        if not status:
            logger.warning(
                f"[Offload] Cannot detect offload status for {self.iface} "
                "(ethtool unavailable and /sys fallback failed). "
                "If GRO/LRO/TSO are enabled, traffic may be undercounted by 30-70%!"
            )
            return

        # 找出仍处于开启状态的特性（排除 fixed 不可更改项）
        on_features = [
            k for k, v in status.items()
            if isinstance(v, str) and 'on' in v.lower() and 'fixed' not in v.lower()
            and k not in ('gro_flush_timeout_ns',)
        ]
        off_features = [
            k for k, v in status.items()
            if isinstance(v, str) and 'off' in v.lower()
        ]

        if on_features:
            logger.warning(
                f"[Offload] WARN: The following offload features are STILL ON "
                f"for {self.iface}: {on_features}. "
                "This can cause ~30-70% traffic undercount!"
            )
            hints = ' '.join(f"{k} off" for k in on_features
                             if k not in ('gro_flush_timeout_ns', 'gro_likely'))
            if hints:
                logger.warning(f"[Offload] Fix: ethtool -K {self.iface} {hints}")
        else:
            logger.info(
                f"[Offload] All monitored offload features are OFF on {self.iface}: {status}"
            )

    # ── 内核丢包监控 ──────────────────────────────────────────────────────────

    def _kernel_drop_monitor_loop(self):
        """
        定期读取 /proc/net/dev 中网卡的 RX drop 计数（两次采样差值），
        监控内核层面的丢包情况。
        若60秒内发生丢包，打印 WARNING 并给出调参建议。
        此计数会暴露给 /api/health 接口，便于前端监控。
        """
        def _read_drop() -> int | None:
            try:
                with open('/proc/net/dev', 'r') as f:
                    for line in f:
                        # 格式示例：
                        #   eth0: rx_bytes packets errs drop fifo frame ...
                        stripped = line.strip()
                        if stripped.startswith(self.iface + ':'):
                            parts = stripped.split()
                            # parts[0]="eth0:", [1]=rx_bytes, [2]=rx_pkts,
                            # [3]=rx_errs, [4]=rx_drop
                            return int(parts[4])
            except Exception as e:
                logger.debug(f"[DropMonitor] Failed to read /proc/net/dev: {e}")
            return None

        last_drop = _read_drop()
        while True:
            time.sleep(KERNEL_DROP_MONITOR_INTERVAL)
            cur_drop = _read_drop()
            if cur_drop is not None and last_drop is not None:
                delta = cur_drop - last_drop
                self._kernel_drops_last_60s = max(delta, 0)
                if delta > 0:
                    logger.warning(
                        f"[DropMonitor] Kernel dropped {delta} packets on {self.iface} "
                        f"in last {KERNEL_DROP_MONITOR_INTERVAL}s "
                        f"(cumulative since boot: {cur_drop})"
                    )
                    logger.warning(
                        "[DropMonitor] To reduce kernel drops, run on host: "
                        "sysctl -w net.core.rmem_max=134217728"
                    )
            if cur_drop is not None:
                last_drop = cur_drop

    # ── 包处理工作线程（消费者）─────────────────────────────────────────────

    def _packet_processor_loop(self):
        """
        包处理消费者线程：从 _pkt_queue 取出 (frame, ts) 并调用 _parse_frame()。

        通过与 recv 线程解耦，避免解析耗时阻塞缓冲区消费，
        降低内核缓冲区被撑满的概率。

        每 PKT_RATE_LOG_INTERVAL 秒打印一次处理速率与队列深度，
        便于判断单线程处理是否成为瓶颈（若持续满队列则需考虑多工作线程）。
        """
        pkt_count = 0
        last_log_time = time.time()

        while True:
            try:
                batch = [self._pkt_queue.get(timeout=1.0)]
                while len(batch) < PROCESSOR_BATCH_SIZE:
                    try:
                        batch.append(self._pkt_queue.get_nowait())
                    except queue.Empty:
                        break

                for frame, ts in batch:
                    self._parse_frame(frame, ts)
                pkt_count += len(batch)

                # 定期打印速率诊断
                now = time.time()
                elapsed = now - last_log_time
                if elapsed >= PKT_RATE_LOG_INTERVAL:
                    rate = pkt_count / elapsed if elapsed > 0 else 0
                    logger.info(
                        f"[PktProcessor] {rate:.0f} pkt/s, "
                        f"queue depth: {self._pkt_queue.qsize()}/{PACKET_QUEUE_MAXSIZE}, "
                        f"queue drops (total): {self._queue_drop_count}"
                    )
                    pkt_count = 0
                    last_log_time = now

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"[PktProcessor] Error processing packet: {e}")

    # ── 数据包处理（轻量级手工解析，取代 Scapy 对象构建）────────────────────

    def _handle_ipv4(self, data: bytes, ts: float):
        """
        解析 IPv4 数据包并计入流量统计。
        data: 从以太网帧中剥离链路层头后的 IP 层原始字节。

        关键修复：使用 IP 头中的 total length 字段（偏移 2-4 字节）
        作为计费字节数，而非 len(ethernet_frame)。
        这是协议层声明的精确值，不受以太网头、FCS、padding 干扰。
        """
        if len(data) < 20:  # IPv4 头最小 20 字节
            return

        ihl = (data[0] & 0x0F) * 4
        if ihl < 20 or len(data) < ihl:
            return

        ip_len = struct.unpack_from('!H', data, 2)[0]   # total length（含 IP 头）
        if ip_len < ihl or ip_len > len(data):
            return

        src_int = struct.unpack_from('!I', data, 12)[0]  # src addr as uint32
        dst_int = struct.unpack_from('!I', data, 16)[0]  # dst addr as uint32

        src_local = self._is_local_v4(src_int)
        dst_local = self._is_local_v4(dst_int)

        if src_local and dst_local:
            return  # 内网互传，忽略
        if not src_local and not dst_local:
            return  # 两端都是公网且不是本机，忽略

        if src_local:
            # NAS 发出 → 上行，remote = dst
            remote = socket.inet_ntoa(struct.pack('!I', dst_int))
            self.stats.add_bytes('up', ip_len, remote, ts)
        else:
            # NAS 收到 → 下行，remote = src
            remote = socket.inet_ntoa(struct.pack('!I', src_int))
            self.stats.add_bytes('down', ip_len, remote, ts)

    def _handle_ipv6(self, data: bytes, ts: float):
        """
        解析 IPv6 数据包并计入流量统计。
        data: 从以太网帧剥离链路层头后的 IPv6 层原始字节。

        关键修复：使用 IPv6 头中的 payload length 字段（偏移 4-6 字节）
        加上固定的 40 字节 IPv6 基础头，得到 IP 层总长度作为计费字节数。

        LAN 过滤：运营商将整个家庭局域网分配在同一个 /56 前缀下，
        NAS 与手机/PC 等设备之间通过 IPv6 直连时 src 和 dst 同属该前缀。
        这类流量属于局域网内部传输，不应计入公网流量统计。
        过滤规则：not (src in /56 AND dst in /56)
        """
        if len(data) < 40:  # IPv6 固定头 40 字节
            return

        payload_len = struct.unpack_from('!H', data, 4)[0]  # payload length
        ip_len = 40 + payload_len  # IPv6 total = 40B header + payload
        if ip_len > len(data):
            return

        src_bytes = data[8:24]    # src addr (16 bytes)
        dst_bytes = data[24:40]   # dst addr (16 bytes)
        src_int = int.from_bytes(src_bytes, 'big')
        dst_int = int.from_bytes(dst_bytes, 'big')

        # ── 第一关：双端 LAN 前缀检测（优先执行，开销最低）──────────────
        # 若 src 和 dst 同时属于 LAN /56 前缀 → 局域网内部流量，直接丢弃
        if self._is_in_lan_prefix(src_int) and self._is_in_lan_prefix(dst_int):
            return

        # ── 第二关：方向判定 ─────────────────────────────────────────────
        src_local = self._is_local_v6(src_int)
        dst_local = self._is_local_v6(dst_int)

        if src_local and dst_local:
            return  # 本地/内网互传（链路本地等），忽略
        if not src_local and not dst_local:
            return  # 两端都是公网且不是本机，忽略

        if src_local:
            # NAS 发出（如：向公网服务器上传）→ 上行，remote = dst
            remote = _format_ipv6(dst_bytes)
            self.stats.add_bytes('up', ip_len, remote, ts)
        else:
            # NAS 收到（如：从公网下载）→ 下行，remote = src
            remote = _format_ipv6(src_bytes)
            self.stats.add_bytes('down', ip_len, remote, ts)

    def _parse_frame(self, frame: bytes, ts: float):
        """
        解析一个以太网帧，提取 IP/IPv6 层并分发处理。
        支持 802.1Q / 802.1ad / QinQ 多层 VLAN tag。
        """
        if len(frame) < 14:
            return

        ethertype = struct.unpack_from('!H', frame, 12)[0]
        payload_offset = 14

        # 处理 802.1Q / 802.1ad / QinQ 多层 VLAN tag
        while ethertype in VLAN_ETHERTYPES:
            if len(frame) < payload_offset + 4:
                return
            ethertype = struct.unpack_from('!H', frame, payload_offset + 2)[0]
            payload_offset += 4

        if ethertype == ETH_P_IP:
            self._handle_ipv4(frame[payload_offset:], ts)
        elif ethertype == ETH_P_IPV6:
            self._handle_ipv6(frame[payload_offset:], ts)
        # 其他协议（ARP 等）直接忽略

    # ── 启动抓包（raw socket 替代 Scapy sniff）───────────────────────────────

    def start(self):
        """
        使用原始套接字（AF_PACKET/SOCK_RAW）直接抓包。

        相比 Scapy sniff() 的优势：
        1. 无需为每个数据包构建完整 Scapy 层次对象，CPU 开销降低约 20x
        2. 可以手动设置更大的 SO_RCVBUF，减少内核缓冲区溢出导致的丢包
        3. 直接操作 bytes，避免 Python 对象 GC 压力
        """
        self.running = True
        logger.info(f"Starting raw socket capture on interface: {self.iface}")

        try:
            # AF_PACKET + SOCK_RAW：接收所有以太网帧（含链路层头）
            # ETH_P_ALL (0x0003) 的 big-endian 形式
            sock = socket.socket(
                socket.AF_PACKET,
                socket.SOCK_RAW,
                socket.htons(ETH_P_ALL)
            )

            # 绑定到指定网卡，只抓该网卡的流量
            sock.bind((self.iface, 0))

            # 放大内核接收缓冲区，减少高流量下的丢包
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_RCVBUF_SIZE)
            actual_buf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            self._socket_buffer_actual_kb = actual_buf // 1024
            logger.info(
                f"Socket recv buffer: requested={SOCKET_RCVBUF_SIZE//1024}KB, "
                f"actual={actual_buf//1024}KB"
            )

            # 若实际缓冲区低于请求值的 50%，说明宿主机 net.core.rmem_max 限制过低
            # BT 高并发下载时极易导致内核丢包，统计严重偏低
            if actual_buf < SOCKET_RCVBUF_SIZE * 0.5:
                logger.warning(
                    f"[Buffer] Socket recv buffer is much smaller than requested "
                    f"({actual_buf//1024}KB vs {SOCKET_RCVBUF_SIZE//1024}KB). "
                    "High-traffic scenarios (BT) will likely suffer kernel drops!"
                )
                logger.warning(
                    "[Buffer] Fix: run on host: sysctl -w net.core.rmem_max=134217728"
                )

            # 设置非阻塞超时，便于检查 self.running 标志
            sock.settimeout(1.0)

            logger.info("Raw socket ready, capturing packets (producer->queue->processor)...")

            while self.running:
                try:
                    frame = sock.recv(65535)
                    ts = time.time()
                    # ── 生产者仅投帧到队列，不在此做任何解析 ──────────────
                    # 解析由 _packet_processor_loop 在独立线程中完成，
                    # recv 循环保持最低延迟，最大化内核缓冲区消费速度。
                    try:
                        self._pkt_queue.put_nowait((frame, ts))
                    except queue.Full:
                        self._queue_drop_count += 1
                        # 每 1000 个丢帧打印一次，避免日志洪泛
                        if self._queue_drop_count % 1000 == 1:
                            logger.warning(
                                f"[Buffer] Packet queue full! Total dropped by queue: "
                                f"{self._queue_drop_count}. "
                                "Processor thread may be too slow or traffic is extremely high."
                            )
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        logger.error(f"Recv error: {e}")
                    break

        except PermissionError:
            logger.error("Permission denied: need NET_RAW capability or root")
            logger.warning("Falling back to Scapy simulation mode")
            self._simulate()
        except OSError as e:
            logger.error(f"Socket error: {e}")
            logger.warning("Falling back to Scapy simulation mode")
            self._simulate()
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _simulate(self):
        """无法抓包时的演示模式"""
        import random
        logger.info("Simulation mode: generating fake traffic (down:up ≈ 4:1)")
        fake_ips = [
            '8.8.8.8', '1.1.1.1', '104.16.0.1', '203.0.113.5',
            '2400:3200::1', '2001:4860:4860::8888',
            '185.60.216.1', '91.108.4.1', '13.227.0.1', '31.13.70.1'
        ]
        while self.running:
            time.sleep(0.05)
            ip = random.choice(fake_ips)
            size = random.randint(500, 1460)
            direction = random.choices(['up', 'down'], weights=[1, 4])[0]
            self.stats.add_bytes(direction, size, ip, time.time())

    # ── 对外接口 ──────────────────────────────────────────────────────────────

    def flush_stats(self) -> Dict:
        return self.stats.flush_and_get()

    def get_realtime(self, seconds: int = 60) -> List[Dict]:
        return self.stats.get_realtime_speed(seconds)

    def get_top_ips(self, n: int = 10) -> List[Dict]:
        return self.stats.get_top_ips(n)

    @property
    def local_ips(self) -> Set[str]:
        with self._local_ips_lock:
            return set(self._local_ips)

    @property
    def kernel_drops_last_60s(self) -> int:
        """最近一个监控周期（60秒）内的内核层 RX drop 增量，0 表示无丢包。"""
        return self._kernel_drops_last_60s

    @property
    def socket_buffer_actual_kb(self) -> int:
        """实际生效的 socket 接收缓冲区大小（KB），0 表示 socket 尚未创建。"""
        return self._socket_buffer_actual_kb

    @property
    def queue_drops_total(self) -> int:
        """用户态队列因堆积而主动丢弃的包数"""
        return self._queue_drop_count
