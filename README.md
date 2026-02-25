# NetTraffic-Sentinel

[![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?style=flat-square&logo=docker)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9+-blue?style=flat-square&logo=python)](https://www.python.org/)


---

> 为Linux设计的轻量级docker公网流量监控程序，精准统计物理网卡的纯公网上下行流量，自动过滤局域网内部流量。

---

## 目录

- [项目简介](#项目简介)
- [软件截图](#软件截图)
- [功能特性](#功能特性)
- [系统架构](#系统架构)
- [快速开始](#快速开始)
  - [前置要求](#前置要求)
  - [方式一：Docker Compose（推荐）](#方式一docker-compose推荐)
  - [方式二：手动 Docker 命令](#方式二手动-docker-命令)
- [配置说明](#配置说明)
  - [环境变量](#环境变量)
  - [查找网卡名称](#查找网卡名称)
  - [配置 IPv6 过滤](#配置-ipv6-过滤)
- [Web 界面使用指南](#web-界面使用指南)
  - [流量总览](#流量总览)
  - [自定义日期查询](#自定义日期查询)
  - [历史图表](#历史图表)
  - [今日小时分布与 IP 排行](#今日小时分布与-ip-排行)
- [API 接口文档](#api-接口文档)
- [数据存储说明](#数据存储说明)
- [流量过滤规则](#流量过滤规则)
- [常见问题](#常见问题)
- [项目结构](#项目结构)

---

## 项目简介

家用 NAS 通常既要承担局域网内部的文件传输，也要对外提供远程访问、云同步等服务。大多数路由器或系统自带的流量统计工具无法区分"内网到内网"和"内网到公网"的流量，导致统计结果虚高，难以真实反映宽带消耗情况。

**NetTraffic-Sentinel** 通过直接监听物理网卡的原始数据包，逐包判断 IP 归属，只统计至少一端为公网 IP 的流量，彻底排除局域网内部互传产生的干扰数据。所有统计结果持久化到 SQLite 数据库，通过内置的 Web 仪表盘随时查看任意时间段的公网流量详情。

---
## 软件截图
<img width="2502" height="1255" alt="image" src="https://github.com/user-attachments/assets/a6e2257b-c67c-4a15-ae96-6515518038f0" />
---


## 功能特性

**精准过滤**
- 自动识别并排除 IPv4 私有网段（10.x、172.16–31.x、192.168.x）
- 内置排除 IPv6 链路本地（fe80::/10）、ULA（fc00::/7）、组播（ff00::/8）
- 支持通过环境变量配置运营商动态分配的 IPv6 前缀（如 240e::/12）

**多维度统计**
- 按小时粒度存储原始数据，自动聚合为天、月视图
- 今日 / 本月 / 本年累计流量，以及上下行分别统计
- 数据写入采用幂等 Upsert，重启或断电不会造成重复计数

**数据持久化**
- SQLite WAL 模式，写入延迟低，读写互不阻塞
- 内存数据每 5 分钟（可配置）自动刷写到数据库
- 数据文件通过 Docker Volume 挂载，容器删除重建不丢数据

**Web 可视化**
- 暗色工业风仪表盘，无需安装任何客户端，浏览器直接访问
- 自定义日期范围查询，支持小时 / 天 / 月三种统计粒度
- 七个快捷按钮：今天、昨天、近 7 天、近 30 天、本月、上月、今年
- 最近 30 天每日流量柱状图 + 近 12 个月月度柱状图
- 今日 24 小时流量折线图
- 公网 IP 流量排行榜（TOP 10）
- 页头实时显示当前上下行速率

---

## 系统架构

```
                        ┌─────────────────────────────────┐
                        │       Docker Container           │
                        │                                  │
  物理网卡(eth0)  ──────▶│  Scapy (libpcap)                │
                        │       │                          │
                        │       ▼                          │
                        │  capture.py                      │
                        │  · BPF 过滤: ip or ip6           │
                        │  · IP 归属判断（公/私）           │
                        │  · 线程安全内存统计               │
                        │       │                          │
                        │       ├──────── 每5分钟 ──────────▶│
                        │       │                          │
                        │       ▼                          │
                        │  database.py                     │
                        │  · SQLite WAL                    │
                        │  · 小时粒度主表                   │
                        │  · 天/月聚合视图                  │
                        │       │                          │
                        │       ▼                          │
                        │  api.py (Flask)                  │
                        │  · /api/summary                  │
                        │  · /api/query   ◀── 日期筛选     │
                        │  · /api/history/...              │
                        │  · /api/top_ips                  │
                        │       │                          │
                        └───────┼─────────────────────────┘
                                │
                                ▼
                        浏览器 Web 仪表盘
                        (ECharts 可视化)
```

程序以三个后台线程 + 一个主线程运行：

| 线程 | 职责 |
|------|------|
| `capture` | 调用 Scapy sniff，持续监听网卡原始数据包 |
| `tick` | 每秒采样一次瞬时速率，维护最近 120 秒的速率窗口 |
| `persistence` | 按 `SAVE_INTERVAL` 周期将内存数据刷写到 SQLite |
| 主线程 | 运行 Flask HTTP 服务，提供 Web 界面和 API |

---

## 快速开始

### 前置要求

- 已安装 Docker（≥ 20.10）和 Docker Compose（≥ 2.0）
- 运行在 Linux 宿主机上（容器需要 host 网络模式）
- 拥有 root 或具备 `NET_RAW`、`NET_ADMIN` 权限的用户

### 方式一：Docker Compose（推荐）

**第一步：下载项目文件**

将项目所有文件放入同一目录，例如 `~/nettraffic-sentinel/`。

**第二步：查找你的网卡名**

```bash
ip link show
# 或
ifconfig -a
```

常见名称：`eth0`、`enp3s0`、`ens18`、`bond0`（群晖等 NAS 可能使用 `ovs_eth0`）。

**第三步：修改配置**

编辑 `docker-compose.yml`，将 `MONITOR_IFACE` 改为实际网卡名：

```yaml
environment:
  - MONITOR_IFACE=enp3s0          # ← 改成你的网卡名
  - EXCLUDE_IPV6_PREFIX=fe80::/10 # ← 如有运营商IPv6前缀一并填入
  - WEB_PORT=8080
  - SAVE_INTERVAL=300
```

**第四步：构建并启动**

```bash
cd ~/nettraffic-sentinel
docker compose up -d --build
```

**第五步：访问仪表盘**

打开浏览器，访问 `http://<NAS的IP地址>:8080`

查看容器运行日志：

```bash
docker compose logs -f
```

---

### 方式二：手动 Docker 命令

```bash
# 1. 构建镜像
docker build -t nettraffic-sentinel .

# 2. 创建数据目录
mkdir -p ~/nettraffic-data

# 3. 运行容器（必须使用 host 网络）
docker run -d \
  --name nettraffic-sentinel \
  --network host \
  --cap-add NET_RAW \
  --cap-add NET_ADMIN \
  --restart unless-stopped \
  -e MONITOR_IFACE=eth0 \
  -e EXCLUDE_IPV6_PREFIX="fe80::/10,240e:33e:2f08:d600::/56" \
  -e WEB_PORT=8080 \
  -e SAVE_INTERVAL=300 \
  -e DB_PATH=/data/traffic.db \
  -v ~/nettraffic-data:/data \
  nettraffic-sentinel

# 4. 查看日志
docker logs -f nettraffic-sentinel
```

---

## 配置说明

### 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `MONITOR_IFACE` | `eth0` | 要监听的物理网卡名称，**必须修改为实际网卡名** |
| `EXCLUDE_IPV6_PREFIX` | `""` | 需额外排除的 IPv6 前缀，多个用英文逗号分隔。适用于运营商分配的动态 IPv6 前缀 |
| `WEB_PORT` | `8080` | Web 仪表盘监听端口 |
| `SAVE_INTERVAL` | `300` | 内存数据写入数据库的间隔秒数，最小建议 60 秒 |
| `DB_PATH` | `/data/traffic.db` | SQLite 数据库文件路径，建议保持默认并通过 Volume 映射 |

### 查找网卡名称

不同 NAS 系统的网卡命名方式不同：

```bash
# 通用 Linux
ip -o link show | awk '{print $2}' | tr -d ':'

# 查看有流量的网卡（推荐）
cat /proc/net/dev

# 群晖 DSM
ip link show | grep -E "^[0-9]"
```

**常见场景对应网卡名：**

| 设备类型 | 常见网卡名 |
|----------|-----------|
| 普通 Linux / x86 NAS | `eth0`、`enp2s0`、`ens3` |
| 群晖 DSM | `eth0`、`ovs_eth0`（开启虚拟化时） |
| 威联通 QTS | `eth0`、`bond0` |
| 虚拟机（PVE/ESXi） | `ens18`、`ens192` |

### 配置 IPv6 过滤

程序内置了对常见私有 IPv6 网段的过滤，但**运营商分配给你的 IPv6 前缀属于公网地址**，如果你的 NAS 和其他家庭设备都使用了同一运营商 IPv6 前缀，局域网内设备之间通过 IPv6 通信的流量就会被错误地计入公网流量。

**如何查找你的运营商 IPv6 前缀：**

```bash
# 查看网卡的全局 IPv6 地址
ip -6 addr show eth0 | grep "global"
# 示例输出: inet6 240e:33e:2f08:d601::1/64 scope global
# 前缀即为: 240e:33e:2f08:d600::/56（保留前3-4段，后面补零）
```

将查到的前缀填入环境变量：

```yaml
- EXCLUDE_IPV6_PREFIX=240e:33e:2f08:d600::/56
```

多个前缀：

```yaml
- EXCLUDE_IPV6_PREFIX=240e:33e:2f08:d600::/56,2408:8756::/32
```

---

## Web 界面使用指南

### 流量总览

页面顶部展示四张核心指标卡片，数据实时叠加内存中未持久化的增量，无需等待写入周期即可看到最新数值：

| 卡片 | 说明 |
|------|------|
| **今日总流量** | 从今天 00:00 至当前时刻的公网总流量，附上行/下行明细 |
| **本月总流量** | 本自然月累计公网流量 |
| **本年总流量** | 本自然年累计公网流量 |
| **今日上行 / 下行** | 今日上行与下行分开展示 |

页头右侧持续显示当前实时上下行速率，每 3 秒刷新一次。

---

### 自定义日期查询

这是本程序的核心功能，支持查询任意时间段的公网流量统计。

**操作方式：**

1. 在「开始日期」和「结束日期」输入框中选择日期范围
2. 选择统计粒度：**小时**（适合单日）/ **按天**（适合数周）/ **按月**（适合跨月）
3. 点击 **▶ 查询** 按钮

结果区域将显示：
- 汇总栏：该范围内的总流量、上行、下行
- 图表：按选定粒度展示的流量趋势（柱状图或折线图）

**快捷按钮说明：**

| 按钮 | 范围 | 自动粒度 |
|------|------|---------|
| 今天 | 今日 00:00 ~ 现在 | 小时 |
| 昨天 | 昨日全天 | 小时 |
| 近 7 天 | 最近 7 天 | 按天 |
| 近 30 天 | 最近 30 天 | 按天 |
| 本月 | 本月 1 日 ~ 今天 | 按天 |
| 上月 | 上月完整一个月 | 按天 |
| 今年 | 今年 1 月 1 日 ~ 今天 | 按月 |

> **提示：** 点击快捷按钮时，系统会根据所选时间跨度自动推荐最合适的统计粒度，也可以手动切换。

---

### 历史图表

页面中部并排展示两张历史图：

- **最近 30 天每日流量**：橙色代表上行，绿色代表下行，堆叠柱状图，悬浮显示该日的精确数值
- **近 12 个月月度流量**：全年维度的流量分布，适合评估宽带使用趋势

两张图表每 2 分钟自动刷新一次。

---

### 今日小时分布与 IP 排行

页面底部两列并排：

**今日 24 小时分布**
以折线面积图展示今天每个整点小时的上行（橙）和下行（绿）流量，直观呈现一天中的流量高峰时段。

**公网 IP 流量排行 TOP 10**
展示自程序启动以来（内存数据，重启后重置）与本机通信流量最多的 10 个公网 IP 地址，附带流量大小和占比进度条。金/银/铜三色标注前三名。

> **注意：** IP 排行数据保存在内存中，容器重启后会重置为空。如需持久化 IP 排行，可结合日期查询和外部日志工具实现。

---

## API 接口文档

所有接口返回 JSON 格式，可供脚本或第三方工具（如 Grafana）直接调用。

### `GET /api/summary`
获取今日、本月、本年流量汇总，数值包含内存中未持久化的实时增量。

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
  "month": { ... },
  "year":  { ... }
}
```

---

### `GET /api/query`
**核心查询接口**，支持任意日期范围和统计粒度。

**参数：**

| 参数 | 必填 | 说明 |
|------|------|------|
| `start` | 是 | 开始日期，格式 `YYYY-MM-DD` |
| `end` | 是 | 结束日期，格式 `YYYY-MM-DD` |
| `granularity` | 否 | 统计粒度：`hour`、`day`（默认）、`month` |

**示例请求：**
```bash
# 查询2024年8月份的流量，按天统计
curl "http://nas-ip:8080/api/query?start=2024-08-01&end=2024-08-31&granularity=day"

# 查询今天的小时流量
curl "http://nas-ip:8080/api/query?start=2024-09-15&end=2024-09-15&granularity=hour"
```

**响应示例：**
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
    { "day": "2024-08-01", "up_bytes": 356515840, "down_bytes": 1782579200, "total_bytes": 2139095040 },
    { "day": "2024-08-02", "up_bytes": 0, "down_bytes": 0, "total_bytes": 0 },
    ...
  ]
}
```

> 无数据的日期会自动补零返回，确保图表连续不断档。

---

### `GET /api/history/30days`
获取最近 30 天每日流量数据，响应格式同 `/api/query`（granularity=day）的 series 部分。

### `GET /api/history/12months`
获取最近 12 个月月度流量数据。

### `GET /api/history/today_hours`
获取今日各整点小时的流量数据（仅返回有数据的小时）。

### `GET /api/date_range`
获取数据库中有记录的最早和最晚日期，用于限制日期选择器范围。

```json
{ "min": "2024-01-10", "max": "2024-09-15" }
```

### `GET /api/top_ips`
获取当前累计流量最高的 10 个公网 IP（内存统计，重启重置）。

### `GET /api/realtime`
获取最近 30 秒的每秒采样速率数据及当前上下行速率。

### `GET /api/health`
健康检查接口，返回 `{"status": "ok"}`，供 Docker healthcheck 使用。

---

## 数据存储说明

### 表结构

```sql
-- 主存储表：以小时为粒度记录流量
CREATE TABLE traffic_hourly (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    hour_ts    TEXT NOT NULL UNIQUE,   -- 格式: '2024-08-15 13:00:00'
    up_bytes   INTEGER NOT NULL DEFAULT 0,
    down_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

-- 天粒度聚合视图（由小时表自动聚合，无需手动维护）
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

### 数据写入机制

流量数据首先在内存中按小时累加，每隔 `SAVE_INTERVAL` 秒批量写入 SQLite。写入使用 `INSERT ... ON CONFLICT DO UPDATE SET ... = ... + excluded.` 语句，即使因断电或异常导致同一小时数据被写入多次，也不会产生重复统计——只会在已有数值上继续累加。

### 数据备份

数据库文件位于容器内的 `/data/traffic.db`，通过 Volume 挂载到宿主机的 `./data/` 目录。直接复制该文件即可完成备份：

```bash
cp ~/nettraffic-sentinel/data/traffic.db ~/backup/traffic-$(date +%Y%m%d).db
```

---

## 流量过滤规则

只有符合以下条件的数据包才会被计入统计：**源 IP 或目的 IP 中，至少有一方属于公网地址。**

### 被排除的 IPv4 私有网段（始终过滤）

| 网段 | 说明 |
|------|------|
| `10.0.0.0/8` | A 类私有地址 |
| `172.16.0.0/12` | B 类私有地址（172.16.x.x ~ 172.31.x.x）|
| `192.168.0.0/16` | C 类私有地址 |
| `127.0.0.0/8` | 本机回环地址 |
| `169.254.0.0/16` | 链路本地地址（APIPA）|
| `0.0.0.0/8` | 保留地址 |
| `255.255.255.255/32` | 广播地址 |

### 被排除的 IPv6 网段（始终过滤）

| 网段 | 说明 |
|------|------|
| `fe80::/10` | 链路本地地址 |
| `fc00::/7` | 唯一本地地址（ULA，类似 IPv4 私有地址）|
| `ff00::/8` | 组播地址 |
| `::1/128` | 本机回环地址 |
| `EXCLUDE_IPV6_PREFIX` 配置项 | 运营商分配的动态 IPv6 前缀（用户自定义）|

### 上行与下行的判定

| 情况 | 判定 |
|------|------|
| 源 IP 私有，目的 IP 公网 | **上行**，远端 IP 记为目的 IP |
| 源 IP 公网，目的 IP 私有 | **下行**，远端 IP 记为源 IP |
| 源 IP 公网，目的 IP 公网 | 记为**下行**（视为外部发起的穿透流量）|
| 源 IP 私有，目的 IP 私有 | **忽略**，不计入统计 |

---

## 常见问题

**Q：容器启动后没有流量数据，页面全是零？**

首先确认网卡名是否正确，然后查看容器日志：

```bash
docker logs nettraffic-sentinel | head -20
```

如果看到 `Falling back to simulation mode`，说明抓包权限不足。检查是否添加了 `--cap-add NET_RAW --cap-add NET_ADMIN`，以及是否使用了 `--network host`。

---

**Q：群晖 NAS 上如何运行？**

群晖的 Docker 容器管理器（Container Manager）默认不支持 `network_mode: host`，需要通过 SSH 使用命令行方式启动：

```bash
# SSH 进入群晖
ssh admin@nas-ip

# 切换到 root
sudo -i

# 运行容器
docker run -d \
  --name nettraffic-sentinel \
  --network host \
  --cap-add NET_RAW \
  --cap-add NET_ADMIN \
  -e MONITOR_IFACE=eth0 \
  -v /volume1/docker/nettraffic/data:/data \
  nettraffic-sentinel
```

---

**Q：流量统计数据是否准确？**

程序在网卡层面抓取原始数据包，统计的是以太网帧中的 IP 层字节数（含 IP 头），与运营商计费口径（通常在链路层）存在约 5–10% 的误差，适合用于趋势观测和流量分析，不建议用于精确计费。

---

**Q：`SAVE_INTERVAL` 设置多少合适？**

- `60`：每分钟写一次，数据最精确，适合 SSD
- `300`（默认）：每 5 分钟写一次，适合大多数情况
- `900`：每 15 分钟写一次，减少对 HDD 的写入次数，适合机械硬盘 NAS

容器在两次写入之间意外停止，最多丢失一个写入周期内的流量数据。

---

**Q：如何修改端口？**

修改 `docker-compose.yml` 中的 `WEB_PORT` 环境变量，然后重启容器：

```bash
docker compose down && docker compose up -d
```

> 因为使用 host 网络模式，不需要 `-p` 端口映射，`WEB_PORT` 直接控制 Flask 监听的端口号。

---

**Q：能不能同时监控多块网卡？**

当前版本仅支持监听单块网卡。如果 NAS 有多块网卡同时接入公网，可以启动多个容器实例，分别指定不同的 `MONITOR_IFACE` 和 `WEB_PORT`，使用不同的数据目录。

---

## 项目结构

```
nettraffic-sentinel/
│
├── app.py              # 主入口，启动各线程并运行 Flask
├── capture.py          # 抓包核心：Scapy 监听、IP 过滤、内存统计
├── database.py         # 数据持久化：SQLite 读写、日期范围查询
├── api.py              # HTTP API：Flask 路由定义
│
├── static/
│   └── index.html      # Web 仪表盘（单页应用，含 ECharts 图表）
│
├── requirements.txt    # Python 依赖：flask、scapy
├── Dockerfile          # 镜像构建文件（基于 python:3.11-slim）
├── docker-compose.yml  # 一键部署配置
└── README.md           # 本文档
```

---

## 技术栈

| 层次 | 技术 |
|------|------|
| 抓包 | Scapy 2.5 / libpcap |
| 后端 | Python 3.11 / Flask 3.0 |
| 存储 | SQLite 3（WAL 模式）|
| 前端 | 原生 HTML/CSS/JS + ECharts 5.4 |
| 部署 | Docker / docker-compose |
