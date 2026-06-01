import ipaddress
import os
import socket
import struct
import threading
import unittest
import tempfile

from api import create_app
from capture import PacketCapture, TrafficStats, _compile_ipv6_network_matchers
from database import Database


def build_ipv4_frame(src_ip: str, dst_ip: str, payload_len: int = 100, vlan_tags=()) -> bytes:
    payload = b"\x00" * payload_len
    total_len = 20 + payload_len
    ip_header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        total_len,
        0,
        0,
        64,
        6,
        0,
        socket.inet_aton(src_ip),
        socket.inet_aton(dst_ip),
    )

    frame = b"\xaa\xbb\xcc\xdd\xee\xff" + b"\x11\x22\x33\x44\x55\x66"
    if vlan_tags:
        frame += struct.pack("!H", vlan_tags[0])
        for next_ethertype in vlan_tags:
            frame += struct.pack("!HH", 1, next_ethertype)
    else:
        frame += struct.pack("!H", 0x0800)

    return frame + ip_header + payload


def build_ipv6_frame(src_ip: str, dst_ip: str, payload_len: int = 100) -> bytes:
    payload = b"\x00" * payload_len
    ipv6_header = struct.pack(
        "!IHBB16s16s",
        6 << 28,
        payload_len,
        6,
        64,
        socket.inet_pton(socket.AF_INET6, src_ip),
        socket.inet_pton(socket.AF_INET6, dst_ip),
    )
    return (
        b"\xaa\xbb\xcc\xdd\xee\xff"
        + b"\x11\x22\x33\x44\x55\x66"
        + struct.pack("!H", 0x86DD)
        + ipv6_header
        + payload
    )


def make_capture() -> PacketCapture:
    capture = PacketCapture.__new__(PacketCapture)
    capture.stats = TrafficStats()
    capture._local_ips_lock = threading.RLock()
    capture._local_v4_ints = {int(ipaddress.ip_address("192.168.1.10"))}
    capture._local_v6_ints = {int(ipaddress.ip_address("240e:1111:2222::10"))}
    capture._lan_prefixes = [ipaddress.ip_network("240e:1111:2222::/56")]
    capture._lan_prefix_matchers = _compile_ipv6_network_matchers(capture._lan_prefixes)
    capture._queue_drop_count = 0
    capture._socket_buffer_actual_kb = 0
    capture._kernel_drops_last_60s = 0
    return capture


class PacketCaptureTests(unittest.TestCase):
    def test_ipv4_counts_public_upload_by_ip_total_length(self):
        capture = make_capture()
        frame = build_ipv4_frame("192.168.1.10", "8.8.8.8", payload_len=120)

        capture._parse_frame(frame, 1_700_000_000.0)

        hourly = capture.stats.get_hourly_snapshot()
        self.assertEqual(len(hourly), 1)
        stats = next(iter(hourly.values()))
        self.assertEqual(stats["up"], 140)
        self.assertEqual(stats["down"], 0)

    def test_ipv6_lan_traffic_is_excluded(self):
        capture = make_capture()
        frame = build_ipv6_frame(
            "240e:1111:2222::10",
            "240e:1111:2222::20",
            payload_len=80,
        )

        capture._parse_frame(frame, 1_700_000_000.0)

        self.assertEqual(capture.stats.get_hourly_snapshot(), {})

    def test_stacked_vlan_frames_are_parsed(self):
        capture = make_capture()
        frame = build_ipv4_frame(
            "8.8.8.8",
            "192.168.1.10",
            payload_len=64,
            vlan_tags=(0x88A8, 0x8100, 0x0800),
        )

        capture._parse_frame(frame, 1_700_000_000.0)

        hourly = capture.stats.get_hourly_snapshot()
        self.assertEqual(len(hourly), 1)
        stats = next(iter(hourly.values()))
        self.assertEqual(stats["down"], 84)
        self.assertEqual(stats["up"], 0)


class ApiAndDatabaseTests(unittest.TestCase):
    def test_database_accepts_relative_file_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                db = Database("traffic.db")
                db.init_schema()
                db.commit_stats({"2026-06-01 12:00:00": {"up": 10, "down": 20}})
                self.assertTrue(os.path.exists("traffic.db"))
                rows = db.query_range("2026-06-01", "2026-06-01", "hour")["series"]
                self.assertEqual(rows[0]["total_bytes"], 30)
            finally:
                os.chdir(cwd)

    def test_query_rejects_reversed_date_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(os.path.join(tmpdir, "traffic.db"))
            db.init_schema()
            capture = make_capture()
            client = create_app(db, capture).test_client()

            response = client.get("/api/query?start=2026-06-02&end=2026-06-01")

            self.assertEqual(response.status_code, 400)
            self.assertIn("start must be earlier", response.get_json()["error"])

    def test_query_merges_unflushed_memory_for_hour_granularity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Database(os.path.join(tmpdir, "traffic.db"))
            db.init_schema()
            db.commit_stats({"2026-06-01 12:00:00": {"up": 100, "down": 50}})
            capture = make_capture()
            capture.stats.hourly["2026-06-01 12:00:00"]["up"] = 25
            capture.stats.hourly["2026-06-01 13:00:00"]["down"] = 75
            client = create_app(db, capture).test_client()

            response = client.get(
                "/api/query?start=2026-06-01&end=2026-06-01&granularity=hour"
            )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["summary"]["up_bytes"], 125)
            self.assertEqual(payload["summary"]["down_bytes"], 125)
            rows = {row["hour_ts"]: row for row in payload["series"]}
            self.assertEqual(rows["2026-06-01 12:00:00"]["total_bytes"], 175)
            self.assertEqual(rows["2026-06-01 13:00:00"]["total_bytes"], 75)


if __name__ == "__main__":
    unittest.main()
