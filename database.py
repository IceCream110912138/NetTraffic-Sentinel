"""
database.py - SQLite 数据持久化模块
按小时、天、月三个维度存储上下行流量，支持任意日期范围查询
"""

import sqlite3
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, date
from typing import Dict, List


def _local_now_str() -> str:
    """返回当前本地时间字符串（格式与 SQLite datetime 一致），
    完全基于 Python datetime.now()，忠实反映 TZ 环境变量所指定的时区，
    避免依赖 SQLite 内建 localtime 修饰符的实现差异。"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

logger = logging.getLogger('sentinel.database')

SCHEMA = """
CREATE TABLE IF NOT EXISTS traffic_hourly (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    hour_ts    TEXT NOT NULL UNIQUE,
    up_bytes   INTEGER NOT NULL DEFAULT 0,
    down_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE VIEW IF NOT EXISTS traffic_daily AS
SELECT
    substr(hour_ts, 1, 10)     AS day,
    SUM(up_bytes)              AS up_bytes,
    SUM(down_bytes)            AS down_bytes,
    SUM(up_bytes + down_bytes) AS total_bytes
FROM traffic_hourly
GROUP BY substr(hour_ts, 1, 10);

CREATE VIEW IF NOT EXISTS traffic_monthly AS
SELECT
    substr(hour_ts, 1, 7)      AS month,
    SUM(up_bytes)              AS up_bytes,
    SUM(down_bytes)            AS down_bytes,
    SUM(up_bytes + down_bytes) AS total_bytes
FROM traffic_hourly
GROUP BY substr(hour_ts, 1, 7);

CREATE INDEX IF NOT EXISTS idx_hourly_hour_ts ON traffic_hourly(hour_ts);
"""


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def _connect(self):
        conn = self._get_conn()
        try:
            yield conn
        finally:
            conn.close()

    def init_schema(self):
        with self._lock:
            with self._connect() as conn:
                conn.executescript(SCHEMA)
        logger.info(f"Database initialized: {self.db_path}")

    def commit_stats(self, hourly_data: Dict[str, Dict]):
        if not hourly_data:
            return
        now_str = _local_now_str()          # 统一用 Python 本地时间，严格跟随 TZ 变量
        with self._lock:
            with self._connect() as conn:
                for hour_ts, stats in hourly_data.items():
                    conn.execute("""
                        INSERT INTO traffic_hourly (hour_ts, up_bytes, down_bytes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(hour_ts) DO UPDATE SET
                            up_bytes   = up_bytes   + excluded.up_bytes,
                            down_bytes = down_bytes + excluded.down_bytes,
                            updated_at = excluded.updated_at
                    """, (hour_ts, stats.get('up', 0), stats.get('down', 0), now_str, now_str))
                conn.commit()

    # ── 固定范围快捷查询 ──────────────────────────────────────────────────────

    def get_today_stats(self) -> Dict:
        return self._day_stats(datetime.now().strftime('%Y-%m-%d'))

    def get_month_stats(self) -> Dict:
        month = datetime.now().strftime('%Y-%m')
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT up_bytes,down_bytes,total_bytes FROM traffic_monthly WHERE month=?",
                    (month,)).fetchone()
        return dict(row) if row else {'up_bytes': 0, 'down_bytes': 0, 'total_bytes': 0}

    def get_year_stats(self) -> Dict:
        year = datetime.now().strftime('%Y')
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("""
                    SELECT SUM(up_bytes) AS up_bytes,
                           SUM(down_bytes) AS down_bytes,
                           SUM(up_bytes+down_bytes) AS total_bytes
                    FROM traffic_hourly WHERE hour_ts LIKE ?
                """, (year + '%',)).fetchone()
        return dict(row) if row and row['total_bytes'] else {'up_bytes': 0, 'down_bytes': 0, 'total_bytes': 0}

    def get_last_30days(self) -> List[Dict]:
        today = date.today()
        start = (today - timedelta(days=29)).strftime('%Y-%m-%d')
        end   = today.strftime('%Y-%m-%d')
        return self._daily_range(start, end, fill=True)

    def get_last_12months(self) -> List[Dict]:
        now = datetime.now()
        months = []
        for i in range(11, -1, -1):
            total_months = now.month - 1 - i
            year  = now.year + total_months // 12
            month = total_months % 12 + 1
            months.append(f"{year:04d}-{month:02d}")
        placeholders = ','.join(['?' for _ in months])
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT month,up_bytes,down_bytes,total_bytes FROM traffic_monthly "
                    f"WHERE month IN ({placeholders})", months).fetchall()
        row_map = {r['month']: dict(r) for r in rows}
        return [row_map.get(m, {'month': m, 'up_bytes': 0, 'down_bytes': 0, 'total_bytes': 0})
                for m in months]

    # ── 核心：日期范围查询 ─────────────────────────────────────────────────────

    def query_range(self, start: str, end: str, granularity: str = 'day') -> Dict:
        """
        start/end: 'YYYY-MM-DD'
        granularity: 'hour' | 'day' | 'month'
        """
        if granularity == 'hour':
            series = self._hourly_range(start, end)
        elif granularity == 'month':
            series = self._monthly_range(start, end)
        else:
            series = self._daily_range(start, end, fill=True)

        total_up   = sum(r.get('up_bytes', 0)   for r in series)
        total_down = sum(r.get('down_bytes', 0) for r in series)
        return {
            'summary': {'up_bytes': total_up, 'down_bytes': total_down,
                        'total_bytes': total_up + total_down},
            'series': series,
        }

    def _day_stats(self, day: str) -> Dict:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT up_bytes,down_bytes,total_bytes FROM traffic_daily WHERE day=?",
                    (day,)).fetchone()
        return dict(row) if row else {'up_bytes': 0, 'down_bytes': 0, 'total_bytes': 0}

    def _hourly_range(self, start: str, end: str) -> List[Dict]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute("""
                    SELECT hour_ts, up_bytes, down_bytes, (up_bytes+down_bytes) AS total_bytes
                    FROM traffic_hourly
                    WHERE hour_ts >= ? AND hour_ts <= ?
                    ORDER BY hour_ts
                """, (start + ' 00:00:00', end + ' 23:59:59')).fetchall()
        return [dict(r) for r in rows]

    def _daily_range(self, start: str, end: str, fill: bool = False) -> List[Dict]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute("""
                    SELECT day, up_bytes, down_bytes, total_bytes
                    FROM traffic_daily WHERE day >= ? AND day <= ? ORDER BY day
                """, (start, end)).fetchall()
        row_map = {r['day']: dict(r) for r in rows}
        if not fill:
            return list(row_map.values())
        result = []
        cur   = datetime.strptime(start, '%Y-%m-%d').date()
        end_d = datetime.strptime(end,   '%Y-%m-%d').date()
        while cur <= end_d:
            key = cur.strftime('%Y-%m-%d')
            result.append(row_map.get(key, {'day': key, 'up_bytes': 0, 'down_bytes': 0, 'total_bytes': 0}))
            cur += timedelta(days=1)
        return result

    def _monthly_range(self, start: str, end: str) -> List[Dict]:
        start_m, end_m = start[:7], end[:7]
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute("""
                    SELECT month, up_bytes, down_bytes, total_bytes
                    FROM traffic_monthly WHERE month >= ? AND month <= ? ORDER BY month
                """, (start_m, end_m)).fetchall()
        return [dict(r) for r in rows]

    def get_available_date_range(self) -> Dict:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("""
                    SELECT MIN(substr(hour_ts,1,10)) AS min_day,
                           MAX(substr(hour_ts,1,10)) AS max_day
                    FROM traffic_hourly
                """).fetchone()
        if row and row['min_day']:
            return {'min': row['min_day'], 'max': row['max_day']}
        today = date.today().strftime('%Y-%m-%d')
        return {'min': today, 'max': today}

    def get_hourly_today(self) -> List[Dict]:
        today = datetime.now().strftime('%Y-%m-%d')
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT hour_ts,up_bytes,down_bytes FROM traffic_hourly "
                    "WHERE hour_ts LIKE ? ORDER BY hour_ts",
                    (today + '%',)).fetchall()
        return [dict(r) for r in rows]
