# NetTraffic-Sentinel

[English](README.en.md) | 简体中文

[![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?style=flat-square&logo=docker)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9+-blue?style=flat-square&logo=python)](https://www.python.org/)
[![Timezone](https://img.shields.io/badge/Timezone-TZ%20Dynamic-orange?style=flat-square)]()

NetTraffic-Sentinel 是一个面向 NAS 和 Linux 家庭服务器的公网流量监控工具。它直接监听物理网卡的原始以太网帧，按 IP 层长度统计真实公网流量，并自动过滤局域网通信、IPv4 私有地址和 IPv6 LAN 前缀流量。

适合用来回答一个很具体的问题：NAS 到底消耗了多少公网带宽，而不是局域网内文件复制、备份、串流产生了多少网卡总流量。

![Dashboard](https://github.com/user-attachments/assets/88614535-b9f1-42f7-8b49-5273cd305242)

## 核心特性

- **公网流量统计**：只统计至少一端为公网地址、且方向可归属到本机的 IPv4/IPv6 流量。
- **IPv6 LAN 自动过滤**：自动从网卡公网 IPv6 提取运营商 GUA `/56` 前缀，双端同属该前缀的包视为局域网流量。
- **高精度计费口径**：使用 IPv4 `total length` 和 IPv6 `payload length + 40`，避免以太网帧头、padding、offload 聚合造成偏差。
- **低开销抓包路径**：使用 `AF_PACKET/SOCK_RAW` 原始套接字手工解析帧，避免为每个包构建 Scapy 对象。
- **动态时区**：通过 `TZ` 环境变量控制统计日期归属，数据库时间戳由 Python 本地时间生成。
- **Web 仪表盘**：展示今日、本月、本年汇总，最近 30 天、近 12 个月、今日小时分布、Top IP 和任意日期范围查询。
- **SQLite 持久化**：小时粒度写入，天/月通过视图聚合；WAL 模式降低读写阻塞。
- **运行诊断**：暴露 socket 缓冲区、内核 RX drop、用户态队列丢包等健康信息。

## 工作方式

```text
Physical NIC
    |
    | AF_PACKET / SOCK_RAW
    v
capture.py
    | parse Ethernet, VLAN, IPv4, IPv6
    | classify local/private/public endpoints
    | aggregate unflushed counters in memory
    v
database.py
    | flush every SAVE_INTERVAL seconds
    | traffic_hourly + daily/monthly views
    v
api.py + static/index.html
    | Flask API
    | ECharts dashboard
```

核心线程：

| 线程 | 作用 |
| --- | --- |
| `capture` | 原始 socket 收包并投入解析队列 |
| `pkt-processor` | 批量解析帧并更新内存统计 |
| `tick` | 每秒采样实时速率 |
| `ip-refresh` | 定时刷新本机 IP 和 IPv6 `/56` LAN 前缀 |
| `drop-monitor` | 定时读取 `/proc/net/dev` 监控内核 RX drop |
| `persistence` | 按 `SAVE_INTERVAL` 写入 SQLite |

## 快速开始

### 1. 准备环境

- Linux 宿主机或 NAS
- Docker 20.10+
- Docker Compose 2.0+
- 容器需要 `network_mode: host`、`NET_RAW` 和 `NET_ADMIN`

### 2. 找到要监听的网卡

```bash
ip route | grep default
cat /proc/net/dev
ip link show
```

常见网卡名：

| 场景 | 常见名称 |
| --- | --- |
| 普通 Linux / x86 NAS | `eth0`, `enp2s0`, `ens3` |
| 群晖 DSM | `eth0`, `ovs_eth0` |
| 威联通 QTS | `eth0`, `bond0` |
| PVE / ESXi 虚拟机 | `ens18`, `ens192` |
| fnOS / Debian / Ubuntu | `eth0`, `enp3s0` |

### 3. 配置 `docker-compose.yml`

至少修改 `MONITOR_IFACE` 和 `TZ`：

```yaml
services:
  nettraffic-sentinel:
    build: .
    network_mode: host
    cap_add:
      - NET_RAW
      - NET_ADMIN
    environment:
      - MONITOR_IFACE=eth0
      - TZ=Asia/Shanghai
      - EXCLUDE_IPV6_PREFIX=
      - WEB_PORT=8080
      - SAVE_INTERVAL=300
      - DB_PATH=/data/traffic.db
    volumes:
      - ./data:/data
```

### 4. 启动

```bash
docker compose up -d --build
docker compose logs -f
```

访问：

```text
http://<NAS-IP>:8080
```

如果日志出现 `Simulation mode`，说明容器没有拿到原始 socket 权限，请检查 `network_mode`、`cap_add` 和宿主机权限。

## 配置项

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MONITOR_IFACE` | `eth0` | 监听的宿主机网卡名 |
| `TZ` | `UTC` | IANA 时区名，影响“今天/本月”的日期归属 |
| `EXCLUDE_IPV6_PREFIX` | 空 | 手动排除的 IPv6 LAN 前缀，多个用英文逗号分隔；留空则自动检测 GUA `/56` |
| `WEB_PORT` | `8080` | Flask/Web 仪表盘端口 |
| `SAVE_INTERVAL` | `300` | 内存统计写入 SQLite 的间隔秒数 |
| `DB_PATH` | `/data/traffic.db` | SQLite 数据库路径 |

`SAVE_INTERVAL` 建议：

| 值 | 适用场景 |
| --- | --- |
| `60` | SSD，优先减少断电丢失窗口 |
| `300` | 默认推荐，性能和可靠性均衡 |
| `900` | HDD，优先减少磁盘写入 |

## IPv6 过滤

家用宽带通常会给整个局域网分配一个公网 IPv6 前缀。手机、电脑、NAS 都可能拿到公网 IPv6，因此局域网内访问 NAS 时，源和目标地址看起来也都是公网地址。

NetTraffic-Sentinel 的默认策略：

1. 扫描 `MONITOR_IFACE` 上的 IPv6 GUA 地址。
2. 提取每个地址对应的 `/56` 前缀。
3. 如果一个 IPv6 包的源地址和目标地址同时落在该 LAN 前缀内，则不计入公网流量。
4. 每 10 分钟刷新本机 IP，每 1 小时专项刷新 GUA 前缀。

需要手动指定时：

```yaml
environment:
  - EXCLUDE_IPV6_PREFIX=240e:33e:2f08:d600::/56
```

多个前缀：

```yaml
environment:
  - EXCLUDE_IPV6_PREFIX=240e:xxxx::/56,2409:yyyy::/56
```

调试当前识别结果：

```bash
curl http://<NAS-IP>:8080/api/debug/local_ips
```

## API

| 接口 | 说明 |
| --- | --- |
| `GET /api/summary` | 今日、本月、本年汇总，包含未持久化内存增量 |
| `GET /api/query?start=YYYY-MM-DD&end=YYYY-MM-DD&granularity=day` | 任意日期范围查询，粒度为 `hour`、`day` 或 `month` |
| `GET /api/history/30days` | 最近 30 天 |
| `GET /api/history/12months` | 最近 12 个月 |
| `GET /api/history/today_hours` | 今日小时分布，包含未持久化内存增量 |
| `GET /api/date_range` | 数据库可用日期范围 |
| `GET /api/top_ips` | 当前内存窗口内的公网 IP 流量排行 |
| `GET /api/realtime` | 最近实时速率采样 |
| `GET /api/debug/local_ips` | 本机 IP 和 IPv6 LAN 过滤器状态 |
| `GET /api/health` | 服务健康和丢包诊断 |

查询示例：

```bash
curl "http://<NAS-IP>:8080/api/query?start=2026-06-01&end=2026-06-07&granularity=day"
```

## 数据库

主表：

```sql
CREATE TABLE traffic_hourly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hour_ts TEXT NOT NULL UNIQUE,
    up_bytes INTEGER NOT NULL DEFAULT 0,
    down_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
```

视图：

- `traffic_daily`
- `traffic_monthly`

备份：

```bash
docker compose stop
cp ./data/traffic.db ./traffic.db.bak
docker compose up -d
```

## 排错

### 页面没有数据

- 确认 `MONITOR_IFACE` 是真实承载流量的宿主机网卡。
- 确认容器使用 `network_mode: host`。
- 确认容器具备 `NET_RAW` 权限。
- 查看 `/api/debug/local_ips` 是否识别到本机地址。

### 统计明显偏低

- 查看 `/api/health` 中的 `kernel_drops_last_60s` 和 `queue_drops_total`。
- 确认 `entrypoint.sh` 成功关闭 GRO/LRO/TSO/GSO。
- 调大宿主机 `net.core.rmem_max`：

```bash
sudo sysctl -w net.core.rmem_max=134217728
sudo sysctl -w net.core.rmem_default=134217728
```

### 日期归属不对

- 检查 `TZ` 是否设置为本地时区。
- 重启容器后查看日志中的 `Current local time (post-tzset)`。

### IPv6 内网流量被计入

- 查看 `/api/debug/local_ips` 的 `ipv6_lan_filter.prefixes`。
- 如果自动检测不到前缀，手动配置 `EXCLUDE_IPV6_PREFIX`。

## 开发

运行测试：

```bash
python -m unittest discover -s tests
```

本地运行 Flask 服务通常不会具备 raw socket 权限；没有权限时程序会进入模拟模式。真实抓包建议使用 Docker 在 Linux 宿主机上运行。

## 技术栈

- Python 3.11
- Flask 3
- SQLite WAL
- Linux `AF_PACKET/SOCK_RAW`
- ECharts 5
- Docker / Docker Compose

## 许可证

MIT License. See [LICENSE](LICENSE).
