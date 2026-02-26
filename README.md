# NetTraffic-Sentinel

[![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?style=flat-square&logo=docker)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9+-blue?style=flat-square&logo=python)](https://www.python.org/)
[![Timezone](https://img.shields.io/badge/Timezone-TZ%20Dynamic-orange?style=flat-square)]()

**专为 NAS 设计的轻量级公网流量监控程序。** 直接监听物理网卡原始数据包，精准统计纯公网上下行流量，自动过滤局域网内部流量与 IPv6 私有地址，提供美观的 Web 可视化仪表盘与灵活的日期范围查询。

> **v1.0 新增：** 动态时区支持（`TZ` 环境变量）、运营商 IPv6 /56 前缀自动检测与 LAN 流量过滤。

---

## 目录

- [NetTraffic-Sentinel](#nettraffic-sentinel)
  - [目录](#目录)
  - [为什么需要这个工具](#为什么需要这个工具)
  - [软件截图](#软件截图)
  - [核心特性](#核心特性)
  - [系统架构](#系统架构)
  - [快速开始](#快速开始)
    - [前置要求](#前置要求)
    - [第一步：获取项目文件](#第一步获取项目文件)
    - [第二步：找到你的网卡名](#第二步找到你的网卡名)
    - [第三步：修改配置](#第三步修改配置)
    - [第四步：构建并启动](#第四步构建并启动)
    - [第五步：访问仪表盘](#第五步访问仪表盘)
  - [环境变量配置详解](#环境变量配置详解)
  - [时区配置](#时区配置)
    - [工作原理](#工作原理)
    - [配置方式](#配置方式)
    - [验证时区是否生效](#验证时区是否生效)
  - [IPv6 过滤配置](#ipv6-过滤配置)
    - [理解问题背景](#理解问题背景)
    - [自动模式（推荐）：/56 前缀动态检测](#自动模式推荐56-前缀动态检测)
    - [手动模式：指定 EXCLUDE\_IPV6\_PREFIX](#手动模式指定-exclude_ipv6_prefix)
    - [查找你的运营商 IPv6 前缀](#查找你的运营商-ipv6-前缀)
    - [验证过滤器是否生效](#验证过滤器是否生效)
  - [Web 仪表盘使用指南](#web-仪表盘使用指南)
    - [流量总览卡片](#流量总览卡片)
    - [自定义日期查询](#自定义日期查询)
    - [历史图表](#历史图表)
    - [今日小时分布与 IP 排行](#今日小时分布与-ip-排行)
  - [API 接口文档](#api-接口文档)
    - [`GET /api/summary`](#get-apisummary)
    - [`GET /api/query` ⭐ 核心查询接口](#get-apiquery--核心查询接口)
    - [`GET /api/history/30days`](#get-apihistory30days)
    - [`GET /api/history/12months`](#get-apihistory12months)
    - [`GET /api/history/today_hours`](#get-apihistorytoday_hours)
    - [`GET /api/date_range`](#get-apidate_range)
    - [`GET /api/top_ips`](#get-apitop_ips)
    - [`GET /api/realtime`](#get-apirealtime)
    - [`GET /api/debug/local_ips`](#get-apidebuglocal_ips)
    - [`GET /api/health`](#get-apihealth)
  - [数据库结构](#数据库结构)
    - [表结构](#表结构)
    - [写入机制](#写入机制)
    - [数据备份与迁移](#数据备份与迁移)
    - [直接查询数据库](#直接查询数据库)
  - [流量过滤与方向判定规则](#流量过滤与方向判定规则)
    - [IPv4 始终排除的私有网段](#ipv4-始终排除的私有网段)
    - [IPv6 始终排除的网段](#ipv6-始终排除的网段)
    - [上行与下行的判定矩阵](#上行与下行的判定矩阵)
  - [技术实现：统计精度保障](#技术实现统计精度保障)
    - [问题：原始实现存在 ~50% 统计误差](#问题原始实现存在-50-统计误差)
    - [解决方案](#解决方案)
  - [项目文件结构](#项目文件结构)
  - [常见问题](#常见问题)
  - [不同 NAS 系统部署说明](#不同-nas-系统部署说明)
    - [群晖 DSM](#群晖-dsm)
    - [威联通 QTS](#威联通-qts)
    - [飞牛 fnOS / 其他基于 Debian/Ubuntu 的 NAS](#飞牛-fnos--其他基于-debianubuntu-的-nas)
    - [PVE 虚拟机中的 NAS](#pve-虚拟机中的-nas)
  - [技术栈](#技术栈)

---

## 为什么需要这个工具

家用 NAS 同时承担着两类完全不同的流量：

- **局域网流量**：PC 访问 NAS 共享目录、手机内网播放视频、Time Machine 备份等，这些流量走内网，不消耗宽带流量计费额度
- **公网流量**：远程访问 NAS、从互联网下载文件到 NAS、向外同步数据等，这些才是真正消耗宽带的流量

路由器、系统自带的流量统计工具通常只能看到网卡总流量，无法区分这两类。**NetTraffic-Sentinel 通过逐包分析 IP 地址归属，只统计至少一端为公网 IP 的数据包**，精准呈现你的 NAS 真实宽带消耗情况。

---
## 软件截图

<img width="2507" height="1090" alt="image" src="https://github.com/user-attachments/assets/88614535-b9f1-42f7-8b49-5273cd305242" />


## 核心特性

**精准的公网流量过滤**
- 自动识别并排除全部 IPv4 私有网段（RFC 1918）
- 内置排除 IPv6 链路本地（fe80::/10）、ULA（fc00::/7）、组播（ff00::/8）
- **自动检测运营商分配的 GUA /56 前缀**，双端同属该前缀的 IPv6 包视为 LAN 内部流量直接丢弃，解决运营商动态拨号 IP 变动导致的内网流量污染问题
- 支持通过 `EXCLUDE_IPV6_PREFIX` 手动指定 IPv6 前缀，手动配置优先级高于自动检测
- 自动检测本机网卡绑定的所有 IPv6 地址，正确区分"NAS 向外发送文件"（上行）与"外部向 NAS 下载"（下行）
- 本机 IPv6 地址每 10 分钟自动刷新，GUA /56 前缀每 1 小时专项检测，双重定时应对 SLAAC 轮换和运营商重拨

**动态时区支持**
- 通过 `TZ` 环境变量自由设置容器时区，无任何硬编码时区（如 `Asia/Shanghai`）
- 程序启动时调用 `time.tzset()` 激活时区，`datetime.now()` 立即反映设定时区
- 数据库的 `created_at` / `updated_at` 字段和所有时间戳均通过 Python `datetime.now()` 生成，完全跟随 `TZ` 变量，不依赖 SQLite 内建 `localtime` 修饰符
- Dockerfile 已预装 `tzdata`，容器可正确解析任意 IANA 时区名

**高精度流量统计（解决 50% 误差问题）**
- 使用 `AF_PACKET/SOCK_RAW` 原始套接字替代 Scapy，单包处理时间从 ~50μs 降至 ~2μs，大幅减少高流量下的丢包
- 统计字节数取 IP 协议头声明的 `total length` 字段，而非以太网帧长度，排除链路层开销干扰
- 内核接收缓冲区设为 32MB（默认仅 2MB），减少高速传输时的环形缓冲区溢出
- 容器启动时自动通过 `ethtool` 禁用网卡 GRO/LRO/TSO，避免硬件聚合导致的包计数失真

**多维度数据存储**
- SQLite WAL 模式，读写互不阻塞，低延迟
- 以小时为粒度存储原始数据，天和月维度通过数据库视图自动聚合
- 内存统计每隔 `SAVE_INTERVAL` 秒幂等写入数据库（重启不丢数据、不重复计数）

**灵活的 Web 可视化**
- 暗色工业风仪表盘，无需安装任何插件，浏览器直接访问
- 首屏展示今日 / 本月 / 本年累计流量，数值实时叠加内存中未持久化的增量
- **自定义日期范围查询**：任意起止日期 + 小时 / 天 / 月三种粒度，7 个快捷按钮
- 最近 30 天每日流量柱状图、近 12 个月月度柱状图、今日 24 小时折线图
- 公网 IP 流量排行 TOP 10（含占比进度条）
- 页头实时显示当前上下行速率（每 3 秒刷新）

---

## 系统架构

```
物理网卡 (eth0)
      │
      │ AF_PACKET/SOCK_RAW（原始以太网帧）
      ▼
┌─────────────────────────────────────────────────────────┐
│                   Docker Container                       │
│                                                          │
│  app.py（主入口）                                         │
│  ├── setup_timezone()  读取 TZ 环境变量 → time.tzset()   │
│  └── 启动三个子线程：capture / persistence / tick        │
│                                                          │
│  capture.py                                              │
│  ├── 手工解析以太网帧（EtherType → IPv4/IPv6）            │
│  ├── 用 IP 头 total length 字段计费（非帧长度）           │
│  ├── 检测本机 IP（含公网 IPv6）→ 判定上行/下行方向        │
│  ├── 自动提取 GUA /56 前缀 → 双端 LAN 检测过滤           │
│  ├── TrafficStats（线程安全内存统计）                     │
│  └── 每秒采样实时速率                                    │
│            │                                             │
│            │ 每 SAVE_INTERVAL 秒                         │
│            ▼                                             │
│  database.py                                             │
│  ├── SQLite WAL 模式                                     │
│  ├── 时间戳完全由 Python datetime.now() 生成（跟随 TZ）  │
│  ├── traffic_hourly 主表（小时粒度）                      │
│  ├── traffic_daily 视图（天聚合）                         │
│  └── traffic_monthly 视图（月聚合）                       │
│            │                                             │
│            ▼                                             │
│  api.py（Flask HTTP 服务）                               │
│  ├── GET /api/summary        今日/本月/本年汇总            │
│  ├── GET /api/query          任意日期范围查询（核心）      │
│  ├── GET /api/history/*      30天/12月/今日小时           │
│  ├── GET /api/top_ips        公网 IP 排行                 │
│  ├── GET /api/realtime       实时速率                     │
│  └── GET /api/debug/local_ips 本机 IP + LAN 过滤器调试   │
│            │                                             │
└────────────┼────────────────────────────────────────────┘
             │ HTTP
             ▼
      浏览器 Web 仪表盘
      (ECharts 5 可视化)
```

程序以多线程方式运行，五个核心线程职责如下：

| 线程名 | 职责 |
|--------|------|
| `capture`（主线程） | 原始套接字收包循环，解析帧并统计流量 |
| `tick` | 每秒快照一次当前速率，维护最近 120 秒的速率窗口 |
| `ip-refresh` | 每 10 分钟重新检测网卡绑定 IP（应对 IPv6 SLAAC 轮换），IP 变化时联动刷新 /56 前缀 |
| `ip-refresh`（复用）| 每 1 小时额外执行一次 GUA /56 前缀专项检测（应对运营商重拨后前缀段变化） |
| `persistence` | 按 `SAVE_INTERVAL` 周期将内存数据刷写到 SQLite |

---

## 快速开始

### 前置要求

- Linux 宿主机（容器依赖 `AF_PACKET` 原始套接字，仅 Linux 支持）
- Docker ≥ 20.10，Docker Compose ≥ 2.0
- 以 root 或具备 `NET_RAW`、`NET_ADMIN` 权限的用户运行

### 第一步：获取项目文件

将以下文件放入同一目录，例如 `~/nettraffic-sentinel/`：

```
nettraffic-sentinel/
├── app.py
├── capture.py
├── database.py
├── api.py
├── entrypoint.sh
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── static/
    └── index.html
```

### 第二步：找到你的网卡名

```bash
# 方法一：查看所有网卡
ip link show

# 方法二：查看有流量的网卡（推荐，找到 RX/TX 字节数非零的那个）
cat /proc/net/dev

# 方法三：查看有默认路由的网卡（最直接）
ip route | grep default
# 示例输出：default via 192.168.1.1 dev eth0
#                                       ^^^^
#                                       这就是你要填的网卡名
```

**常见网卡名参考：**

| 设备类型 | 常见网卡名 |
|----------|-----------|
| 普通 Linux / x86 NAS | `eth0`、`enp2s0`、`ens3` |
| 群晖 DSM（未开虚拟化） | `eth0` |
| 群晖 DSM（开启 VMM） | `ovs_eth0` |
| 威联通 QTS | `eth0`、`bond0` |
| PVE/ESXi 虚拟机 | `ens18`、`ens192` |
| 飞牛 fnOS | `eth0`、`enp3s0` |

### 第三步：修改配置

编辑 `docker-compose.yml`，至少修改 `MONITOR_IFACE` 和 `TZ`：

```yaml
environment:
  - MONITOR_IFACE=enp2s0        # ← 改为你的实际网卡名
  - TZ=Asia/Shanghai            # ← 改为你所在的时区
  - EXCLUDE_IPV6_PREFIX=        # ← 留空则自动检测 /56 前缀；如需手动指定见下文
  - WEB_PORT=8080               # Web 界面端口，按需修改
  - SAVE_INTERVAL=300           # 数据库写入间隔（秒），建议 60~900
  - DB_PATH=/data/traffic.db    # 数据库路径，保持默认即可
```

### 第四步：构建并启动

```bash
cd ~/nettraffic-sentinel

# 构建镜像并在后台启动容器
docker compose up -d --build

# 查看启动日志，确认抓包正常
docker compose logs -f
```

正常启动后日志应包含类似输出：

```
[entrypoint] Disabling NIC offload features on eth0...
  GRO  -> off
  TSO  -> off
[entrypoint] net.core.rmem_max set to 64MB
[entrypoint] Starting NetTraffic-Sentinel...
sentinel: TZ environment variable detected: Asia/Shanghai
sentinel: Timezone applied via time.tzset(): Asia/Shanghai
sentinel: Current local time (post-tzset): 2026-02-26 10:30:00 CST
sentinel: Local IPs on eth0 -> IPv4: ['192.168.1.100'], IPv6 public: ['240e:xxxx::1']
sentinel: [IPv6-Filter] Auto GUA /56 prefixes updated: ['240e:33e:2f08:d600::/56']
sentinel: Raw socket ready, capturing packets...
```

如果看到 `Simulation mode`，说明权限不足，请检查 `cap_add` 和 `network_mode` 配置。

### 第五步：访问仪表盘

浏览器打开：`http://<NAS的IP地址>:8080`

---

## 环境变量配置详解

| 变量名 | 默认值 | 是否必填 | 说明 |
|--------|--------|----------|------|
| `MONITOR_IFACE` | `eth0` | **必填** | 要监听的物理网卡名称，必须与宿主机实际名称一致 |
| `TZ` | `UTC` | **建议填写** | 容器时区，使用 IANA 时区名（如 `Asia/Shanghai`、`Asia/Tokyo`），影响统计数据的日期归属 |
| `EXCLUDE_IPV6_PREFIX` | `""` | 可选 | 手动指定需排除的 IPv6 前缀，多个用英文逗号分隔；留空则自动检测 GUA /56 前缀 |
| `WEB_PORT` | `8080` | 可选 | Web 仪表盘监听端口 |
| `SAVE_INTERVAL` | `300` | 可选 | 内存数据写入数据库的间隔秒数 |
| `DB_PATH` | `/data/traffic.db` | 可选 | SQLite 数据库文件路径，配合 Volume 使用 |

**`SAVE_INTERVAL` 选择建议：**

| 值 | 适用场景 | 说明 |
|----|----------|------|
| `60` | SSD 存储 | 每分钟写一次，断电最多丢 1 分钟数据 |
| `300`（默认） | 大多数场景 | 每 5 分钟写一次，均衡性能与可靠性 |
| `900` | HDD 机械硬盘 NAS | 每 15 分钟写一次，减少磁盘写入次数 |

---

## 时区配置

统计数据的日期归属（"今天的流量"算在哪一天）完全由容器时区决定，必须与你所在的地理时区一致，否则每天的流量会在错误的日期上累加。

### 工作原理

程序启动时在所有 `datetime` 调用之前执行 `setup_timezone()`：

1. 读取 `TZ` 环境变量
2. 调用 `time.tzset()` 通知 C 运行时切换时区，Python 的 `datetime.now()` 立即生效
3. 打印当前本地时间用于日志验证

数据库的所有时间戳（`hour_ts`、`created_at`、`updated_at`）均由 Python `datetime.now()` 生成，完全跟随 `TZ` 变量，**不依赖** SQLite 内建的 `datetime('now','localtime')`（后者在容器环境下可能与 `TZ` 变量脱节）。

Dockerfile 已预装 `tzdata` 包，容器可正确解析任意 IANA 时区名。

### 配置方式

```yaml
# docker-compose.yml
environment:
  - TZ=Asia/Shanghai    # 中国标准时间（UTC+8）

# 其他常用时区示例：
# TZ=Asia/Tokyo         # 日本标准时间（UTC+9）
# TZ=America/New_York   # 美东时间
# TZ=Europe/London      # 英国时间
# TZ=UTC                # 协调世界时（默认）
```

### 验证时区是否生效

查看容器启动日志，确认时间与本地时间一致：

```bash
docker compose logs | grep "local time"
# 期望输出：Current local time (post-tzset): 2026-02-26 10:30:00 CST
```

> **注意：** 如果你是从旧版本升级，建议在修改 `TZ` 后重建镜像（`docker compose up -d --build`），因为 `tzdata` 是新增的系统依赖。

---

## IPv6 过滤配置

### 理解问题背景

大部分的家用 NAS 是没有公网 IPv4 的（在路由器 NAT 之后），但大多数运营商现在为每个家庭分配了公网 IPv6 地址段，家里的每台设备（NAS、电脑、手机）都会获得一个公网 IPv6 地址。

由于 IPv6 不存在 NAT，当**局域网内的手机通过 IPv6 访问 NAS** 时，数据包的源地址和目的地址都是公网 IPv6，如果不过滤，这类 LAN 内部通信会被错误地计入公网流量统计。

中国电信、联通等运营商通常将整个家庭局域网分配在同一个 **/56 前缀**下（如 `240e:33e:2f08:d600::/56`），家里所有设备的 IPv6 地址都属于这个前缀。程序利用这一特性，通过"**双端 /56 前缀检测**"规则自动识别并丢弃 LAN 内部流量：

> **过滤规则**：若数据包的 src 和 dst **同时属于同一 /56 前缀**，则视为 LAN 内部通信，不计入公网流量。

### 自动模式（推荐）：/56 前缀动态检测

默认情况下（`EXCLUDE_IPV6_PREFIX` 留空），程序会**自动**完成以下操作：

1. 程序启动时，读取 `MONITOR_IFACE` 网卡上所有 GUA（全球单播地址，以 `2` 开头的公网 IPv6）
2. 提取每个 GUA 的 /56 前缀，构建 LAN 过滤器
3. 每隔 **10 分钟**（本机 IP 变化时立即触发）和每隔 **1 小时**（专项检测），重新扫描并更新过滤器

**示例：**

若网卡IP为 `240e:33e:2f08:d601::1`，程序自动生成过滤规则：
```
not (src ∈ 240e:33e:2f08:d600::/56 AND dst ∈ 240e:33e:2f08:d600::/56)
```

容器日志将显示：
```
[IPv6-Filter] Auto GUA /56 prefixes updated: ['240e:33e:2f08:d600::/56']
[IPv6-Filter] Intra-LAN IPv6 traffic (both endpoints in /56) will be excluded.
```

**容错处理：**

- 网卡尚未获取到公网 IPv6 → `_lan_prefixes` 为空，仅靠内置规则排除 `fe80::/10`、`fc00::/7` 等，程序正常运行不崩溃
- 运营商重拨后前缀段变化 → 最迟 1 小时内自动更新；本机 IP 同时改变则立即触发

### 手动模式：指定 EXCLUDE_IPV6_PREFIX

如果你希望精确控制过滤前缀，可以手动设置。**手动配置优先级最高，设置后自动检测将被禁用。**

```yaml
# 单个前缀（/56 是常见的家宽前缀长度）
- EXCLUDE_IPV6_PREFIX=240e:33e:2f08:d600::/56

# 如果运营商分配了多个前缀段（多拨/双线环境）
- EXCLUDE_IPV6_PREFIX=240e:33e:2f08:d600::/56,2408:8756:abc::/48
```

### 查找你的运营商 IPv6 前缀

```bash
# 在 NAS 宿主机上执行
ip -6 addr show eth0 | grep "scope global"

# 示例输出：
# inet6 240e:33e:2f08:d601:xxxx:xxxx:xxxx:xxxx/64 scope global dynamic
#
# 对应的 /56 前缀（取前 56 位，末尾 3 位十六进制清零）：
# 240e:33e:2f08:d600::/56
#         ^^^^ 注意：d601 的末尾 01 清零 → d600
```

### 验证过滤器是否生效

容器运行后访问调试接口：

```bash
curl http://localhost:8080/api/debug/local_ips
```

返回示例（自动模式）：
```json
{
  "iface": "eth0",
  "ipv4": ["192.168.1.100"],
  "ipv6": ["240e:33e:2f08:d601::1", "240e:33e:2f08:d601:a1b2:c3d4:e5f6:0001"],
  "total": 3,
  "ipv6_lan_filter": {
    "mode": "auto-gua-/56",
    "prefixes": ["240e:33e:2f08:d600::/56"],
    "note": "自动模式：从网卡 GUA 提取 /56 前缀，每小时刷新一次。双端地址同属此前缀的 IPv6 包将被视为 LAN 内部流量并排除。"
  },
  "note": "上行/下行方向判断依赖 ipv6 列表中的本机地址。"
}
```

返回示例（手动模式）：
```json
{
  "ipv6_lan_filter": {
    "mode": "manual",
    "prefixes": ["240e:33e:2f08:d600::/56"],
    "note": "手动模式：由 EXCLUDE_IPV6_PREFIX 环境变量指定，优先级最高。"
  }
}
```

---

## Web 仪表盘使用指南

### 流量总览卡片

页面顶部展示四张核心指标卡片，数据**实时叠加内存中未持久化的增量**，无需等待写入周期即可看到最新数值，每 15 秒自动刷新。

| 卡片 | 说明 |
|------|------|
| **今日总流量** | 从今天 00:00 至当前时刻的公网总流量，附上行/下行明细 |
| **本月总流量** | 本自然月（从 1 日起）累计公网总流量 |
| **本年总流量** | 本自然年（从 1 月 1 日起）累计公网总流量 |
| **今日上行 / 下行** | 今日上行与下行独立展示，便于对比 |

页头右侧常驻实时网速（上行▲ / 下行▼），每 3 秒更新一次。

---

### 自定义日期查询

这是本程序的**核心功能**，支持查询任意时间段的公网流量统计详情。

**操作步骤：**

1. 在查询面板中选择「开始日期」和「结束日期」
2. 选择统计粒度：
   - **小时**：适合查看单日的流量分布（最细粒度）
   - **按天**：适合查看数天至数周的趋势
   - **按月**：适合查看跨月或年度视角
3. 点击 **▶ 查询** 按钮

查询结果将立即显示：
- **汇总栏**：所选日期范围内的总流量、上行合计、下行合计
- **趋势图**：按所选粒度绘制的流量柱状图（小时粒度时为折线图）

**快捷按钮说明（自动选择最合适的粒度）：**

| 按钮 | 日期范围 | 自动粒度 |
|------|----------|---------|
| 今天 | 今日 00:00 ~ 现在 | 小时 |
| 昨天 | 昨日完整一天 | 小时 |
| 近 7 天 | 最近 7 天 | 按天 |
| 近 30 天 | 最近 30 天 | 按天 |
| 本月 | 本月 1 日 ~ 今天 | 按天 |
| 上月 | 上月完整一个月 | 按天 |
| 今年 | 今年 1 月 1 日 ~ 今天 | 按月 |

> 点击快捷按钮后，系统会自动填入日期范围并推荐粒度，你也可以手动切换粒度再重新查询。

---

### 历史图表

页面中部并排展示两张长期历史图，每 2 分钟自动刷新：

**最近 30 天每日流量**（左图）
- 橙色：上行，绿色：下行，堆叠柱状图
- 鼠标悬浮显示该日的精确上行和下行字节数

**近 12 个月月度流量**（右图）
- 全年维度的流量走势，适合观察哪个月流量异常高
- 同样支持悬浮查看精确数值

---

### 今日小时分布与 IP 排行

页面底部并排展示两个面板：

**今日 24 小时分布**
- 折线面积图，展示今天 0 时到当前时刻每个整点小时的上行（橙）和下行（绿）流量
- 直观呈现一天中的流量高峰时段，每 30 秒自动刷新

**公网 IP 流量排行 TOP 10**
- 统计自程序启动以来，与 NAS 通信流量最大的 10 个公网 IP 地址
- 附带流量大小和相对占比进度条，金/银/铜三色标注前三名
- **注意**：IP 排行数据存储在内存中，容器重启后会重置

---

## API 接口文档

所有接口返回 JSON 格式，支持脚本调用或接入 Grafana 等监控平台。

### `GET /api/summary`

返回今日、本月、本年的流量汇总，数值包含内存中未持久化的实时增量。

**响应示例：**
```json
{
  "today": {
    "up_bytes": 1073741824,
    "down_bytes": 5368709120,
    "total_bytes": 6442450944,
    "up_fmt": "1.00 GB",
    "down_fmt": "5.00 GB",
    "total_fmt": "6.00 GB"
  },
  "month": {
    "up_bytes": 32212254720,
    "down_bytes": 161061273600,
    "total_bytes": 193273528320,
    "up_fmt": "30.00 GB",
    "down_fmt": "150.00 GB",
    "total_fmt": "180.00 GB"
  },
  "year": { ... }
}
```

---

### `GET /api/query` ⭐ 核心查询接口

支持任意日期范围和统计粒度的流量查询。

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `start` | string | 是 | 开始日期，格式 `YYYY-MM-DD` |
| `end` | string | 是 | 结束日期，格式 `YYYY-MM-DD` |
| `granularity` | string | 否 | 粒度：`hour`、`day`（默认）、`month` |

**示例请求：**
```bash
# 查询 8 月份按天统计
curl "http://nas-ip:8080/api/query?start=2024-08-01&end=2024-08-31&granularity=day"

# 查询今天按小时统计
curl "http://nas-ip:8080/api/query?start=2024-09-15&end=2024-09-15&granularity=hour"

# 查询全年按月统计
curl "http://nas-ip:8080/api/query?start=2024-01-01&end=2024-12-31&granularity=month"
```

**响应示例（granularity=day）：**
```json
{
  "summary": {
    "up_bytes": 10737418240,
    "down_bytes": 53687091200,
    "total_bytes": 64424509440,
    "up_fmt": "10.00 GB",
    "down_fmt": "50.00 GB",
    "total_fmt": "60.00 GB"
  },
  "series": [
    {
      "day": "2024-08-01",
      "up_bytes": 356515840,
      "down_bytes": 1782579200,
      "total_bytes": 2139095040
    },
    {
      "day": "2024-08-02",
      "up_bytes": 0,
      "down_bytes": 0,
      "total_bytes": 0
    }
  ]
}
```

> 无数据的日期自动补零，确保图表连续不断档。若查询范围包含今天，会自动叠加内存中未持久化的增量。

---

### `GET /api/history/30days`

返回最近 30 天每日流量数据，格式同 `/api/query?granularity=day` 的 `series` 部分。

---

### `GET /api/history/12months`

返回最近 12 个月月度流量数据。

**响应示例：**
```json
{
  "months": [
    { "month": "2023-10", "up_bytes": 5000000000, "down_bytes": 25000000000, "total_bytes": 30000000000 },
    { "month": "2023-11", "up_bytes": 0, "down_bytes": 0, "total_bytes": 0 },
    ...
  ]
}
```

---

### `GET /api/history/today_hours`

返回今日各整点小时的流量数据（只返回有数据的小时，前端自动补零）。

---

### `GET /api/date_range`

返回数据库中有记录的最早和最晚日期，用于限制日期选择器范围。

**响应示例：**
```json
{ "min": "2024-01-10", "max": "2024-09-15" }
```

---

### `GET /api/top_ips`

返回当前累计流量最高的 10 个公网 IP（内存统计，重启后重置）。

**响应示例：**
```json
{
  "top_ips": [
    { "ip": "203.0.113.1", "bytes": 5368709120, "bytes_fmt": "5.00 GB" },
    { "ip": "8.8.8.8",     "bytes": 1073741824, "bytes_fmt": "1.00 GB" }
  ]
}
```

---

### `GET /api/realtime`

返回最近 30 秒的每秒速率采样点及当前上下行速率。

**响应示例：**
```json
{
  "samples": [
    { "ts": 1694784000, "up": 12345, "down": 98765 },
    { "ts": 1694784001, "up": 13210, "down": 102400 }
  ],
  "current_up_bps": 104960,
  "current_down_bps": 819200,
  "current_up_Bps": 13120,
  "current_down_Bps": 102400
}
```

---

### `GET /api/debug/local_ips`

返回程序当前检测到的本机 IP 列表，以及当前生效的 IPv6 LAN 过滤器状态，用于诊断方向判断和过滤器是否正确工作。

**响应示例：**
```json
{
  "iface": "eth0",
  "ipv4": ["192.168.1.100"],
  "ipv6": ["240e:33e:2f08:d601::1", "240e:33e:2f08:d601:a1b2:c3d4:e5f6:0001"],
  "total": 3,
  "ipv6_lan_filter": {
    "mode": "auto-gua-/56",
    "prefixes": ["240e:33e:2f08:d600::/56"],
    "note": "自动模式：从网卡 GUA 提取 /56 前缀，每小时刷新一次。..."
  },
  "note": "上行/下行方向判断依赖 ipv6 列表中的本机地址。..."
}
```

---

### `GET /api/health`

健康检查接口，供 Docker healthcheck 使用。时间戳使用容器本地时间（跟随 `TZ` 环境变量）。

```json
{ "status": "ok", "ts": "2026-02-26T10:30:00.234567" }
```

---

## 数据库结构

### 表结构

```sql
-- 主存储表：以小时为粒度，所有聚合查询的基础
-- hour_ts 格式与容器时区严格对应（由 Python datetime.now() 生成，跟随 TZ 变量）
CREATE TABLE traffic_hourly (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    hour_ts    TEXT NOT NULL UNIQUE,   -- 'YYYY-MM-DD HH:00:00'（本地时间）
    up_bytes   INTEGER NOT NULL DEFAULT 0,
    down_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,                   -- 首次写入时间（本地时间）
    updated_at TEXT                    -- 最后更新时间（本地时间）
);

-- 天粒度聚合视图（自动从小时表 GROUP BY，无需手动维护）
CREATE VIEW traffic_daily AS
SELECT
    substr(hour_ts, 1, 10) AS day,
    SUM(up_bytes)              AS up_bytes,
    SUM(down_bytes)            AS down_bytes,
    SUM(up_bytes + down_bytes) AS total_bytes
FROM traffic_hourly
GROUP BY substr(hour_ts, 1, 10);

-- 月粒度聚合视图
CREATE VIEW traffic_monthly AS
SELECT
    substr(hour_ts, 1, 7) AS month,
    SUM(up_bytes)              AS up_bytes,
    SUM(down_bytes)            AS down_bytes,
    SUM(up_bytes + down_bytes) AS total_bytes
FROM traffic_hourly
GROUP BY substr(hour_ts, 1, 7);
```

### 写入机制

流量数据首先在内存中按小时粒度累加（`defaultdict`），每隔 `SAVE_INTERVAL` 秒批量写入。写入时由 Python `datetime.now()` 生成本地时间戳传入 SQL，采用 `INSERT ... ON CONFLICT DO UPDATE SET ... = ... + excluded.` 幂等语句：

```sql
-- now_str 由 Python datetime.now().strftime('%Y-%m-%d %H:%M:%S') 生成
-- 严格跟随 TZ 环境变量，不使用 SQLite 的 datetime('now','localtime')
INSERT INTO traffic_hourly (hour_ts, up_bytes, down_bytes, created_at, updated_at)
VALUES ('2026-02-26 14:00:00', 1234, 5678, '2026-02-26 14:05:00', '2026-02-26 14:05:00')
ON CONFLICT(hour_ts) DO UPDATE SET
    up_bytes   = up_bytes   + excluded.up_bytes,
    down_bytes = down_bytes + excluded.down_bytes,
    updated_at = excluded.updated_at;
```

**即使因断电或异常导致同一小时数据被写入多次，也只会在已有数值上继续累加，不会产生重复统计。** IPv6 过滤器的动态更新不影响内存累加逻辑，过滤器仅决定是否将某个数据包的字节数加入内存统计，已在内存中的数据不受影响。

### 数据备份与迁移

```bash
# 备份（容器运行时也可以操作，WAL 模式保证一致性）
cp ~/nettraffic-sentinel/data/traffic.db ~/backup/traffic-$(date +%Y%m%d).db

# 迁移到新 NAS
scp ~/backup/traffic-20240915.db newnas:~/nettraffic-sentinel/data/traffic.db
```

### 直接查询数据库

```bash
# 进入容器执行 SQL
docker exec -it nettraffic-sentinel sqlite3 /data/traffic.db

# 常用查询
SELECT day, up_bytes/1073741824.0 AS up_GB, down_bytes/1073741824.0 AS down_GB
FROM traffic_daily
ORDER BY day DESC
LIMIT 30;

SELECT month, total_bytes/1073741824.0 AS total_GB
FROM traffic_monthly
ORDER BY month DESC;
```

---

## 流量过滤与方向判定规则

### IPv4 始终排除的私有网段

| 网段 | 说明 |
|------|------|
| `10.0.0.0/8` | A 类私有地址 |
| `172.16.0.0/12` | B 类私有地址（172.16.x ~ 172.31.x） |
| `192.168.0.0/16` | C 类私有地址 |
| `127.0.0.0/8` | 本机回环 |
| `169.254.0.0/16` | 链路本地（APIPA） |
| `0.0.0.0/8` | 保留地址 |
| `255.255.255.255/32` | 广播地址 |

### IPv6 始终排除的网段

| 网段 | 说明 |
|------|------|
| `fe80::/10` | 链路本地地址 |
| `fc00::/7` | 唯一本地地址（ULA，类似 IPv4 私有地址） |
| `ff00::/8` | 组播地址 |
| `::1/128` | 本机回环 |
| 运营商 GUA /56 前缀（双端检测） | 自动检测或由 `EXCLUDE_IPV6_PREFIX` 手动指定；**双端均属该前缀时**视为 LAN 内部流量丢弃 |

### 上行与下行的判定矩阵

IPv6 数据包经过**两层过滤**后才进行方向判定：

**第一层：双端 LAN 前缀检测（优先执行）**

| src | dst | 结论 |
|-----|-----|------|
| 属于 /56 LAN 前缀 | 属于 /56 LAN 前缀 | **丢弃**（LAN 内部流量，不计入公网） |
| 其他情况 | 其他情况 | 进入第二层方向判定 |

**第二层：方向判定**

| src | dst | 结论 | 远端 IP（记入排行） |
|-----|-----|------|---------------------|
| 本机/内网 | 公网 | **上行**（NAS 发送数据） | dst |
| 公网 | 本机/内网 | **下行**（NAS 接收数据） | src |
| 本机/内网 | 本机/内网 | **忽略**（内网互传） | — |
| 公网 | 公网（非本机） | **忽略**（非本机流量） | — |

**"本机或内网"的判定逻辑：**
- IPv4：属于上表私有网段 → 内网侧；或在本机网卡绑定地址中 → 本机侧
- IPv6：属于 `fe80::/10`、`fc00::/7` 等内置排除段 → 内网侧；**在本机绑定的 IPv6 列表中 → 本机侧**（含属于 /56 LAN 前缀的本机地址）；否则为远端公网

---

## 技术实现：统计精度保障

### 问题：原始实现存在 ~50% 统计误差

早期版本使用 Scapy 的 `sniff(prn=callback)` 抓包，存在两个严重缺陷：

**缺陷一：字节数统计口径错误**

Scapy `len(pkt)` 返回的是以太网帧完整长度，包含 14 字节以太网头。流量统计应该使用 IP 层声明的报文长度，这才是运营商计费的参考口径。

**缺陷二（主因）：Scapy 纯 Python 处理导致大量丢包**

Scapy 每处理一个包需要构建多层 Python 对象（以太网→IP→TCP），耗时约 50~200μs/包。下载 800MB 文件时，网卡每秒接收约 8000~10000 个 TCP 包，Scapy 的处理速度完全跟不上，内核 libpcap 缓冲区（默认仅 2MB）迅速溢出，大量数据包被内核静默丢弃，完全不会进入回调函数，造成统计严重偏低。

**缺陷三：网卡 Offload 导致计数失真**

现代网卡支持 GRO/LRO（接收聚合）和 TSO/GSO（发送分片）。GRO 会将多个小 TCP 包聚合成一个超大帧再送给内核，这让 raw socket/libpcap 在某些场景下只看到聚合后的大帧而非真实的每个包，在另一些场景下又只看到分片前的单次提交。在不同内核/驱动组合下，误差可达 30%~70%。

### 解决方案

**修复一：改用 IP 协议头的 total length 字段**

```python
# IPv4：偏移 2-3 字节为 total length（含 IP 头+数据）
ip_len = struct.unpack_from('!H', data, 2)[0]

# IPv6：偏移 4-5 字节为 payload length，加上固定 40 字节头
ip_len = 40 + struct.unpack_from('!H', data, 4)[0]
```

这是协议层声明的权威字节数，不受以太网头、FCS、padding 干扰。

**修复二：用原始套接字替代 Scapy，手工解析字节**

```python
sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
sock.bind((self.iface, 0))
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024 * 1024)

while running:
    frame = sock.recv(65535)
    self._parse_frame(frame, time.time())  # 纯 struct.unpack，无 Python 对象构建
```

- 单包处理时间从 ~50μs 降至 ~2μs，性能提升约 **20 倍**
- 内核接收缓冲区从默认 2MB 扩大到 32MB，缓冲区溢出概率大幅下降
- 同时在内核层将 `net.core.rmem_max` 设为 64MB，解除操作系统限制

**修复三：容器启动时禁用网卡 Offload**

`entrypoint.sh` 启动时通过 `ethtool` 禁用 GRO/LRO/TSO/GSO：

```bash
ethtool -K eth0 gro off lro off tso off gso off
```

禁用后，每个 IP 报文独立经过协议栈和 raw socket，统计与实际传输字节数一致。对 NAS CPU 占用影响通常小于 5%。

---

## 项目文件结构

```
nettraffic-sentinel/
│
├── app.py              # 主入口：时区初始化、启动抓包/持久化/Flask 三个线程
├── capture.py          # 抓包核心：raw socket、IP 解析、/56 LAN 过滤、方向判定、内存统计
├── database.py         # 数据层：SQLite WAL、Python 时间戳、小时存储、多维度查询
├── api.py              # HTTP API：Flask 路由、实时汇总、日期范围查询、LAN 过滤器调试
│
├── static/
│   └── index.html      # 前端仪表盘：纯 HTML/CSS/JS + ECharts 5
│
├── entrypoint.sh       # 容器入口脚本：禁用 NIC offload → 启动主程序
├── requirements.txt    # Python 依赖：flask、scapy（备用）、netifaces
├── Dockerfile          # 镜像：python:3.11-slim + ethtool + iproute2 + tzdata
├── docker-compose.yml  # 一键部署配置
└── README.md           # 本文档
```

**各文件职责说明：**

`capture.py` 是整个系统最核心的文件，承担：原始帧接收 → 协议解析 → GUA /56 前缀自动检测 → 双端 LAN 过滤 → IP 归属判定 → 方向判定 → 线程安全统计 → 实时速率采样 → 本机 IP 定期刷新，所有统计精度相关的逻辑都在这里。

`app.py` 在启动任何统计逻辑之前，首先执行 `setup_timezone()` 读取 `TZ` 环境变量并调用 `time.tzset()` 激活时区，确保后续所有 `datetime.now()` 调用都返回正确的本地时间。

`database.py` 负责持久化，所有时间戳通过 `_local_now_str()`（即 `datetime.now()`）生成，完全跟随 `TZ` 变量。天和月级别的统计不单独存储，而是通过 SQL 视图从小时表实时聚合，避免数据不一致。

`entrypoint.sh` 是保障统计精度的"基础设施"，在主程序启动前调整内核和网卡参数，其效果相当于给统计数据加了一道硬件层面的保险。

---

## 常见问题

**Q：容器启动后页面显示全零，没有任何数据？**

1. 确认网卡名配置正确：`docker exec nettraffic-sentinel ip link show`
2. 查看日志是否有 `Simulation mode`：`docker compose logs | grep -i simul`
3. 如果是模拟模式，检查 `cap_add` 是否包含 `NET_RAW` 和 `NET_ADMIN`，以及 `network_mode: host` 是否设置

---

**Q：今日流量统计和实际不对，日期对不上？**

检查时区配置：

```bash
# 查看当前时区是否正确
docker compose logs | grep "local time"

# 期望输出（以 Asia/Shanghai 为例）：
# Current local time (post-tzset): 2026-02-26 10:30:00 CST
```

如果时间不对，在 `docker-compose.yml` 中添加/修改 `TZ` 环境变量，然后重建容器：
```bash
docker compose down && docker compose up -d --build
```

> **注意**：修改时区后，历史数据的 `hour_ts` 仍是旧时区生成的时间戳，新旧数据在同一数据库中会产生时区错位。建议在修改时区时同步清空或备份旧数据库。

---

**Q：局域网内手机/PC 通过 IPv6 访问 NAS 的流量还是被计入了公网流量？**

访问 `/api/debug/local_ips`，查看 `ipv6_lan_filter` 字段：

- 若 `mode` 为 `auto-gua-/56` 且 `prefixes` 为空，说明程序未检测到 GUA。这通常意味着容器启动时网卡还没获取到公网 IPv6，等待几分钟后过滤器会自动更新
- 若需要立即生效，可以重启容器，或使用手动模式指定 `EXCLUDE_IPV6_PREFIX`

---

**Q：统计数据和实际下载量还是有偏差（10% 以内）？**

这是正常现象，原因：

1. 程序统计的是 **IP 层字节数**，运营商计费通常在**链路层（以太网帧+FCS）**，约有 3~5% 的固定差异
2. TCP 的重传包会被统计两次（但实际传输的有效数据只有一份）
3. 握手、ACK 等控制包也会被计入，这部分在大文件传输中占比较小

若偏差超过 20%，请检查 `ethtool -k eth0 | grep offload` 确认 GRO/LRO 是否已成功禁用。

---

**Q：统计到了 IPv6 地址的流量，但方向反了？**

访问 `/api/debug/local_ips` 查看程序检测到的本机 IP 列表。如果 NAS 的公网 IPv6 不在 `ipv6` 列表中，说明 `MONITOR_IFACE` 配的网卡名与实际拥有该 IPv6 地址的网卡不一致。

---

**Q：`ethtool` 禁用 offload 报错"Operation not supported"？**

这是正常现象。虚拟机网卡、某些旧款网卡不支持所有 offload 特性。`entrypoint.sh` 已对每条命令单独捕获错误，不支持的特性会跳过，支持的会成功禁用。

---

**Q：如何在群晖 NAS 上部署？**

群晖 Container Manager（Docker 图形界面）不支持 `network_mode: host`，必须通过 SSH 命令行部署：

```bash
# SSH 进入群晖
ssh admin@your-nas-ip
sudo -i

# 构建镜像（在项目目录中）
cd /volume1/docker/nettraffic-sentinel
docker build -t nettraffic-sentinel .

# 启动容器
docker run -d \
  --name nettraffic-sentinel \
  --network host \
  --cap-add NET_RAW \
  --cap-add NET_ADMIN \
  --restart unless-stopped \
  -e MONITOR_IFACE=eth0 \
  -e TZ=Asia/Shanghai \
  -e WEB_PORT=8080 \
  -e SAVE_INTERVAL=300 \
  -v /volume1/docker/nettraffic-sentinel/data:/data \
  nettraffic-sentinel
```

---

**Q：如何修改 Web 端口？**

修改 `docker-compose.yml` 中的 `WEB_PORT` 环境变量，然后重启：

```bash
docker compose down && docker compose up -d
```

因为使用 host 网络模式，不需要 `-p` 端口映射，`WEB_PORT` 直接控制 Flask 监听的端口号。

---

**Q：数据库文件越来越大怎么办？**

每小时一条记录，一年约 8760 条，按 SQLite 存储效率估算全年数据库约 2~5MB，不需要担心磁盘占用。如果确实需要清理历史数据：

```bash
docker exec -it nettraffic-sentinel sqlite3 /data/traffic.db \
  "DELETE FROM traffic_hourly WHERE hour_ts < '2024-01-01 00:00:00';"
```

---

## 不同 NAS 系统部署说明

### 群晖 DSM

```bash
# 必须 SSH + 命令行，不能用 Container Manager 图形界面
docker run -d --name nettraffic-sentinel \
  --network host --cap-add NET_RAW --cap-add NET_ADMIN \
  --restart unless-stopped \
  -e MONITOR_IFACE=eth0 \
  -e TZ=Asia/Shanghai \
  -v /volume1/docker/nettraffic/data:/data \
  nettraffic-sentinel
```

### 威联通 QTS

```bash
# 威联通可能使用 bond0 做网卡聚合
docker run -d --name nettraffic-sentinel \
  --network host --cap-add NET_RAW --cap-add NET_ADMIN \
  --restart unless-stopped \
  -e MONITOR_IFACE=bond0 \
  -e TZ=Asia/Shanghai \
  -v /share/Container/nettraffic/data:/data \
  nettraffic-sentinel
```

### 飞牛 fnOS / 其他基于 Debian/Ubuntu 的 NAS

```bash
# 直接使用 docker compose，修改网卡名后一键部署
docker compose up -d --build
```

### PVE 虚拟机中的 NAS

如果 NAS 运行在 PVE 虚拟机里，监听的是虚拟网卡（通常是 `ens18` 或 `ens192`），注意虚拟网卡的 GRO/LRO 可能无法通过 `ethtool` 禁用（不影响正常使用，只是 offload 禁用步骤会跳过）。

---

## 技术栈

| 层次 | 技术选型 | 原因 |
|------|---------|------|
| 抓包 | Linux `AF_PACKET/SOCK_RAW` | 性能比 Scapy/libpcap 高约 20x，丢包率极低 |
| 协议解析 | Python `struct` 标准库 | 无第三方依赖，手工解析以太网/IP/IPv6 头部 |
| 本机 IP 检测 | `netifaces` + `ip addr` fallback | 可靠检测所有绑定 IPv6 地址 |
| GUA /56 检测 | 内置逻辑（首字节掩码判断 GUA） | 无额外依赖，实时提取运营商 LAN 前缀 |
| 时区支持 | `TZ` 环境变量 + `time.tzset()` + `tzdata` 包 | 动态切换，无硬编码时区，兼容 IANA 全时区 |
| 存储 | SQLite 3（WAL 模式） | 轻量、无服务进程、读写不互斥 |
| 时间戳生成 | Python `datetime.now()` | 严格跟随 `TZ` 变量，不依赖 SQLite 内建 localtime |
| Web 后端 | Python 3.11 + Flask 3.0 | 轻量，适合 NAS 资源受限环境 |
| 前端可视化 | 原生 HTML/CSS/JS + ECharts 5.4 | 零依赖，单文件，无需构建 |
| 容器化 | Docker + docker-compose | 隔离运行环境，一键部署 |
