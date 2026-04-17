import ipaddress
import socket
import struct
import threading
import unittest

from capture import PacketCapture, TrafficStats, _compile_ipv6_network_matchers


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


if __name__ == "__main__":
    unittest.main()
