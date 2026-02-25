#!/bin/bash
# entrypoint.sh
# 启动前禁用网卡 Offload 特性，确保 libpcap/raw socket 能抓到完整的每一个 IP 包。
#
# 【为什么需要禁用 Offload？】
# 现代网卡支持 TSO/GSO/GRO/LRO 等卸载特性：
#   - TSO（TX Segmentation Offload）：发送时让网卡把大块数据拆成小包，CPU 只提交一次
#   - GRO（Generic Receive Offload）：接收时内核把多个小包聚合成一个大包再送给应用
#   - LRO（Large Receive Offload）：类似 GRO，但在网卡硬件层面完成
#
# 这些特性会导致 raw socket/libpcap 抓到的"包"与实际网络上传输的包不一致：
#   - 收到 100 个 1460B 的 TCP 段 → GRO 聚合为 1 个 ~146KB 的超大帧
#   - 发送 1 个 10MB 数据块 → TSO 拆成 ~7000 个 1460B 包发出，但抓包只看到 1 个
#
# 在某些内核/网卡组合下，这会导致统计结果偏低 30%~70%。
# 禁用后，每个 IP 报文都会独立经过协议栈，统计更准确。
#
# 注意：禁用 Offload 会略微增加 CPU 占用（通常 < 5%），对 NAS 影响可忽略。

IFACE="${MONITOR_IFACE:-eth0}"

if command -v ethtool &> /dev/null; then
    echo "[entrypoint] Disabling NIC offload features on ${IFACE}..."
    # 逐项禁用，某项不支持时忽略错误继续
    ethtool -K "${IFACE}" gro off    2>/dev/null && echo "  GRO  -> off" || echo "  GRO  -> not supported (skip)"
    ethtool -K "${IFACE}" lro off    2>/dev/null && echo "  LRO  -> off" || echo "  LRO  -> not supported (skip)"
    ethtool -K "${IFACE}" tso off    2>/dev/null && echo "  TSO  -> off" || echo "  TSO  -> not supported (skip)"
    ethtool -K "${IFACE}" gso off    2>/dev/null && echo "  GSO  -> off" || echo "  GSO  -> not supported (skip)"
    ethtool -K "${IFACE}" rx-gro-hw off 2>/dev/null || true
    echo "[entrypoint] Offload settings applied."
else
    echo "[entrypoint] ethtool not found, skipping offload disable (may cause ~30-50% undercount)"
fi

# 尝试调大内核全局的 socket 接收缓冲区上限
# 允许单个 socket 申请最大 64MB 的接收缓冲区
if [ -w /proc/sys/net/core/rmem_max ]; then
    echo 67108864 > /proc/sys/net/core/rmem_max
    echo "[entrypoint] net.core.rmem_max set to 64MB"
fi

echo "[entrypoint] Starting NetTraffic-Sentinel..."
exec python app.py
