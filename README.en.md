# NetTraffic-Sentinel

English | [简体中文](README.md)

[![Docker](https://img.shields.io/badge/Docker-Supported-2496ED?style=flat-square&logo=docker)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9+-blue?style=flat-square&logo=python)](https://www.python.org/)
[![Timezone](https://img.shields.io/badge/Timezone-TZ%20Dynamic-orange?style=flat-square)]()

NetTraffic-Sentinel is a public Internet traffic monitor for NAS boxes and Linux home servers. It listens directly on a physical network interface, parses raw Ethernet frames, counts IP-layer traffic, and filters out LAN traffic, IPv4 private ranges, and IPv6 LAN-prefix traffic.

The goal is simple: measure how much public bandwidth your NAS really consumes, without mixing in local file copies, backups, media streaming, or other LAN-only traffic.

![Dashboard](https://github.com/user-attachments/assets/88614535-b9f1-42f7-8b49-5273cd305242)

## Features

- **Public traffic accounting**: counts IPv4/IPv6 packets where at least one endpoint is public and the direction can be attributed to the local host.
- **Automatic IPv6 LAN filtering**: extracts the ISP-assigned GUA `/56` prefix from the monitored interface and drops packets where both endpoints are inside that prefix.
- **Accurate byte basis**: uses IPv4 `total length` and IPv6 `payload length + 40`, avoiding Ethernet headers, padding, and offload aggregation artifacts.
- **Low-overhead capture path**: uses Linux `AF_PACKET/SOCK_RAW` and manual frame parsing instead of building Scapy objects for every packet.
- **Dynamic timezone support**: the `TZ` environment variable controls local date boundaries for daily/monthly statistics.
- **Web dashboard**: today/month/year totals, 30-day history, 12-month history, today-by-hour chart, top IPs, realtime speed, and custom date-range queries.
- **SQLite persistence**: hourly rows with daily/monthly aggregate views; WAL mode reduces read/write contention.
- **Operational diagnostics**: exposes socket buffer size, kernel RX drops, and user-space queue drops through the health API.

## Architecture

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

Runtime threads:

| Thread | Purpose |
| --- | --- |
| `capture` | Receives raw socket frames and enqueues them |
| `pkt-processor` | Batch-parses frames and updates in-memory counters |
| `tick` | Samples realtime transfer speed once per second |
| `ip-refresh` | Refreshes local IPs and IPv6 `/56` LAN prefixes |
| `drop-monitor` | Reads `/proc/net/dev` to monitor kernel RX drops |
| `persistence` | Flushes memory counters to SQLite |

## Quick Start

### 1. Requirements

- Linux host or NAS
- Docker 20.10+
- Docker Compose 2.0+
- Container must use `network_mode: host`, `NET_RAW`, and `NET_ADMIN`

### 2. Find the interface to monitor

```bash
ip route | grep default
cat /proc/net/dev
ip link show
```

Common interface names:

| Environment | Common names |
| --- | --- |
| Linux / x86 NAS | `eth0`, `enp2s0`, `ens3` |
| Synology DSM | `eth0`, `ovs_eth0` |
| QNAP QTS | `eth0`, `bond0` |
| PVE / ESXi VM | `ens18`, `ens192` |
| fnOS / Debian / Ubuntu | `eth0`, `enp3s0` |

### 3. Configure `docker-compose.yml`

At minimum, change `MONITOR_IFACE` and `TZ`:

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

### 4. Start

```bash
docker compose up -d --build
docker compose logs -f
```

Open:

```text
http://<NAS-IP>:8080
```

If logs show `Simulation mode`, the container does not have raw socket permission. Check `network_mode`, `cap_add`, and host permissions.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `MONITOR_IFACE` | `eth0` | Host interface to monitor |
| `TZ` | `UTC` | IANA timezone used for local day/month boundaries |
| `EXCLUDE_IPV6_PREFIX` | empty | Manual IPv6 LAN prefixes to exclude, comma-separated; empty means auto-detect GUA `/56` |
| `WEB_PORT` | `8080` | Flask/dashboard port |
| `SAVE_INTERVAL` | `300` | Seconds between SQLite flushes |
| `DB_PATH` | `/data/traffic.db` | SQLite database path |

Recommended `SAVE_INTERVAL` values:

| Value | Use case |
| --- | --- |
| `60` | SSD, smaller loss window after power failure |
| `300` | Recommended default |
| `900` | HDD, fewer disk writes |

## IPv6 Filtering

Many home networks assign public IPv6 addresses to every device. A phone, PC, and NAS may all have public IPv6 addresses, so a LAN connection to the NAS can look like public-to-public traffic.

Default strategy:

1. Scan GUA IPv6 addresses on `MONITOR_IFACE`.
2. Extract a `/56` prefix from each GUA.
3. If both source and destination are inside the same LAN prefix, drop the packet from public traffic accounting.
4. Refresh local IPs every 10 minutes and GUA prefixes every hour.

Manual prefix:

```yaml
environment:
  - EXCLUDE_IPV6_PREFIX=240e:33e:2f08:d600::/56
```

Multiple prefixes:

```yaml
environment:
  - EXCLUDE_IPV6_PREFIX=240e:xxxx::/56,2409:yyyy::/56
```

Inspect detected state:

```bash
curl http://<NAS-IP>:8080/api/debug/local_ips
```

## API

| Endpoint | Description |
| --- | --- |
| `GET /api/summary` | Today, month, and year totals, including unflushed memory counters |
| `GET /api/query?start=YYYY-MM-DD&end=YYYY-MM-DD&granularity=day` | Custom date range with `hour`, `day`, or `month` granularity |
| `GET /api/history/30days` | Last 30 days |
| `GET /api/history/12months` | Last 12 months |
| `GET /api/history/today_hours` | Today's hourly distribution, including unflushed memory counters |
| `GET /api/date_range` | Available database date range |
| `GET /api/top_ips` | Top public IPs in the current memory window |
| `GET /api/realtime` | Recent realtime speed samples |
| `GET /api/debug/local_ips` | Local IPs and IPv6 LAN filter state |
| `GET /api/health` | Health and packet-drop diagnostics |

Example:

```bash
curl "http://<NAS-IP>:8080/api/query?start=2026-06-01&end=2026-06-07&granularity=day"
```

## Database

Main table:

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

Views:

- `traffic_daily`
- `traffic_monthly`

Backup:

```bash
docker compose stop
cp ./data/traffic.db ./traffic.db.bak
docker compose up -d
```

## Troubleshooting

### No data on the dashboard

- Confirm `MONITOR_IFACE` is the host interface that carries traffic.
- Confirm the container uses `network_mode: host`.
- Confirm the container has `NET_RAW`.
- Check `/api/debug/local_ips`.

### Traffic is significantly undercounted

- Check `/api/health` for `kernel_drops_last_60s` and `queue_drops_total`.
- Confirm `entrypoint.sh` disabled GRO/LRO/TSO/GSO.
- Increase host receive buffer limits:

```bash
sudo sysctl -w net.core.rmem_max=134217728
sudo sysctl -w net.core.rmem_default=134217728
```

### Daily totals roll over at the wrong time

- Set `TZ` to your local timezone.
- Restart the container and check the `Current local time (post-tzset)` log line.

### IPv6 LAN traffic is counted as public traffic

- Check `ipv6_lan_filter.prefixes` from `/api/debug/local_ips`.
- If auto-detection fails, configure `EXCLUDE_IPV6_PREFIX` manually.

## Development

Run tests:

```bash
python -m unittest discover -s tests
```

Local Flask runs usually do not have raw socket permissions. Without permissions the app enters simulation mode. Use Docker on Linux for real capture testing.

## Stack

- Python 3.11
- Flask 3
- SQLite WAL
- Linux `AF_PACKET/SOCK_RAW`
- ECharts 5
- Docker / Docker Compose

## License

MIT License. See [LICENSE](LICENSE).
