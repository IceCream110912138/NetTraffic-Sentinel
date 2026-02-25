# NetTraffic-Sentinel Dockerfile
FROM python:3.11-slim

LABEL maintainer="NetTraffic-Sentinel"
LABEL description="NAS Public Network Traffic Monitor"

# 系统依赖说明：
#   libpcap-dev / libpcap0.8 : 抓包库（备用，raw socket 模式不强依赖）
#   ethtool                  : 启动时禁用网卡 GRO/LRO/TSO，减少流量统计误差
#   iproute2                 : 提供 `ip addr show` 命令，用于检测本机 IP
#   gcc / python3-dev        : 编译 netifaces
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpcap-dev \
    libpcap0.8 \
    ethtool \
    iproute2 \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py capture.py database.py api.py ./
COPY static/ ./static/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN mkdir -p /data

ENV MONITOR_IFACE=eth0 \
    EXCLUDE_IPV6_PREFIX="" \
    WEB_PORT=8080 \
    SAVE_INTERVAL=300 \
    DB_PATH=/data/traffic.db \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# 使用 entrypoint 脚本：先禁用 offload，再启动主程序
CMD ["/entrypoint.sh"]
