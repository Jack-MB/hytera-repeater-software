"""
Unit-Tests für udp_listener.py (AGENT 2: Backend Engineer)
Prüft das Parsen von Hytera HR1065 Binärpaketen (GPS & PTT) und den Mock-Generator.
"""

import os
import sys
import struct
import tempfile
import asyncio
import unittest

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.abspath(os.path.join(TEST_DIR, ".."))
ROOT_DIR = os.path.abspath(os.path.join(MODULE_DIR, ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from map_module.db_manager import DatabaseManager
from map_module.udp_listener import HyteraBinaryParser, MockTelemetryGenerator


class TestHyteraUDPListener(unittest.IsolatedAsyncioTestCase):

    def test_parse_gps_packet(self):
        """Testet das Dekodieren eines synthetischen Hytera HR1065 GNSS-UDP-Pakets."""
        # Paket-Aufbau:
        # [0]: msghdr (0x80)
        # [1:3]: opcode (z.B. 0x0001 LE)
        # [3:5]: n_bytes (16 Bytes Nutzdaten LE)
        # [5:9]: radio_ip (z.B. 1001 BE)
        # [9:13]: lat (51.234567 * 1e7 = 512345670 BE)
        # [13:17]: lon (6.789123 * 1e7 = 67891230 BE)
        # [17:19]: speed (150 = 15.0 km/h BE)
        # [19:21]: heading (180 Grad BE)
        msghdr = 0x80
        opcode = 0x0001
        n_bytes = 16
        radio_ip = 1001
        lat_raw = int(51.234567 * 1e7)
        lon_raw = int(6.789123 * 1e7)
        speed_raw = 150
        heading_raw = 180

        payload = bytearray()
        payload.append(msghdr)
        payload.extend(struct.pack('<H', opcode))
        payload.extend(struct.pack('<H', n_bytes))
        payload.extend(struct.pack('>I', radio_ip))
        payload.extend(struct.pack('>i', lat_raw))
        payload.extend(struct.pack('>i', lon_raw))
        payload.extend(struct.pack('>H', speed_raw))
        payload.extend(struct.pack('>H', heading_raw))

        parsed = HyteraBinaryParser.parse_gps_packet(bytes(payload))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["radio_id"], 1001)
        self.assertAlmostEqual(parsed["lat"], 51.234567, places=5)
        self.assertAlmostEqual(parsed["lon"], 6.789123, places=5)
        self.assertAlmostEqual(parsed["speed"], 15.0)
        self.assertEqual(parsed["heading"], 180)

    def test_parse_call_packet_start_and_end(self):
        """Testet das Dekodieren von HR1065 NAI RCP Call-Status Paketen (Opcode 0x0004)."""
        # 32 Byte Payload:
        # [0]: msghdr
        # [1:3]: opcode 0x0004 LE
        # [8]: ts_byte (0x01 = TS1)
        # [9]: ct_byte (0x02 = Gruppe)
        # [16]: st_byte (0x01 = aktiv TX)
        # [26:30]: sender_id (1005 LE)
        # [30]: qual_byte (8)
        pkt = bytearray(32)
        pkt[0] = 0x80
        struct.pack_into('<H', pkt, 1, 0x0004)
        pkt[8] = 0x01
        pkt[9] = 0x02
        pkt[16] = 0x01  # TX aktiv
        struct.pack_into('<I', pkt, 26, 1005)
        pkt[30] = 8

        res_start = HyteraBinaryParser.parse_call_packet(bytes(pkt))
        self.assertIsNotNone(res_start)
        self.assertEqual(res_start["type"], "ptt_start")
        self.assertEqual(res_start["radio_id"], 1005)
        self.assertEqual(res_start["slot"], "TS1")
        self.assertEqual(res_start["call_type"], "Gruppe")

        # Ende testen (st_byte = 5)
        pkt[16] = 5
        res_end = HyteraBinaryParser.parse_call_packet(bytes(pkt))
        self.assertIsNotNone(res_end)
        self.assertEqual(res_end["type"], "ptt_end")
        self.assertEqual(res_end["radio_id"], 1005)

    async def test_mock_telemetry_generator(self):
        """Testet, dass der Mock-Generator Events emittiert und in DB schreibt."""
        tmp_dir = tempfile.TemporaryDirectory()
        test_db_path = os.path.join(tmp_dir.name, "test_mock.db")
        db = DatabaseManager(test_db_path)
        await db.init_db()

        received_events = []

        async def _test_cb(ev):
            received_events.append(ev)

        mock_gen = MockTelemetryGenerator(_test_cb, db)
        await mock_gen.start()

        # Kurz laufen lassen, um mindestens 1 Zyklus abzufangen
        await asyncio.sleep(3.5)
        await mock_gen.stop()

        self.assertTrue(len(received_events) >= 5, f"Erwartet >= 5 Events, erhalten: {len(received_events)}")
        gps_events = [e for e in received_events if e.get("type") == "gps_update"]
        self.assertTrue(len(gps_events) >= 5)

        # Prüfen ob Daten in DB gelandet sind
        recent = await db.get_recent_gps()
        self.assertEqual(len(recent), 5)

        tmp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
