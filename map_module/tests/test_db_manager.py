"""
Unit-Tests für db_manager.py (AGENT 1: Database Architect)
Prüft Initialisierung, CRUD, Replay-Funktion get_state_at_timestamp und Timeline-Formatierung.
"""

import os
import sys
import tempfile
import asyncio
import unittest

# Sicherstellen, dass das map_module importierbar ist
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.abspath(os.path.join(TEST_DIR, ".."))
ROOT_DIR = os.path.abspath(os.path.join(MODULE_DIR, ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from map_module.db_manager import DatabaseManager


class TestDatabaseManager(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Temporäre Datenbankdatei für isolierte Tests
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_db_path = os.path.join(self.tmp_dir.name, "test_tactical.db")
        self.db = DatabaseManager(self.test_db_path)
        await self.db.init_db()

    async def asyncTearDown(self):
        self.tmp_dir.cleanup()

    async def test_radios_upsert_and_get(self):
        # 1. Funkgerät einfügen
        await self.db.upsert_radio(1001, "Einsatzleiter", "RT81", "2026-09-08T12:00:00Z")
        await self.db.upsert_radio(1002, "Angriffstrupp 1", "HR1065 Mobile", "2026-09-08T12:01:00Z")

        radios = await self.db.get_radios()
        radios_by_id = {r["radio_id"]: r for r in radios}
        self.assertIn(1001, radios_by_id)
        self.assertIn(1002, radios_by_id)
        self.assertEqual(radios_by_id[1001]["alias"], "Einsatzleiter")
        self.assertEqual(radios_by_id[1002]["alias"], "Angriffstrupp 1")

        # Update testen
        await self.db.upsert_radio(1001, "Einsatzleiter NEU")
        updated = await self.db.get_radios()
        updated_by_id = {r["radio_id"]: r for r in updated}
        self.assertEqual(updated_by_id[1001]["alias"], "Einsatzleiter NEU")

    async def test_gps_insert_and_recent(self):
        await self.db.upsert_radio(1001, "Willi")
        await self.db.insert_gps(1001, 51.2300, 6.7800, speed=12.5, heading=90.0, accuracy=3.0, timestamp="2026-09-08T12:00:00Z")
        await self.db.insert_gps(1001, 51.2310, 6.7810, speed=14.0, heading=95.0, accuracy=2.5, timestamp="2026-09-08T12:00:30Z")

        recent = await self.db.get_recent_gps()
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["radio_id"], 1001)
        self.assertAlmostEqual(recent[0]["lat"], 51.2310)
        self.assertAlmostEqual(recent[0]["lon"], 6.7810)

    async def test_markers_crud(self):
        # Marker anlegen
        mid = await self.db.insert_marker(
            lat=51.2350,
            lon=6.7850,
            typ="hazard",
            beschreibung="Ölspur auf Zufahrtsstraße",
            prioritaet="high",
            author="Operator-1",
            timestamp="2026-09-08T12:05:00Z"
        )
        self.assertTrue(mid > 0)

        markers = await self.db.get_markers()
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["beschreibung"], "Ölspur auf Zufahrtsstraße")
        self.assertEqual(markers[0]["prioritaet"], "high")

        # Marker löschen
        deleted = await self.db.delete_marker(mid)
        self.assertTrue(deleted)
        markers_after = await self.db.get_markers()
        self.assertEqual(len(markers_after), 0)

    async def test_ptt_insert_and_timeline(self):
        await self.db.upsert_radio(1003, "Mario")
        await self.db.insert_ptt(1003, 3500, typ="Gruppe", slot="TS1", rssi=-85.0, timestamp="2026-09-08T12:10:00Z")
        await self.db.insert_marker(51.23, 6.78, "checkpoint", "Kontrollpunkt Alpha", "normal", timestamp="2026-09-08T12:10:05Z")

        events = await self.db.get_timeline_events()
        self.assertEqual(len(events), 2)
        ptt_ev = [e for e in events if e["id"].startswith("ptt_")][0]
        self.assertIn("Mario", ptt_ev["content"])
        self.assertEqual(ptt_ev["radio_id"], 1003)

    async def test_get_state_at_timestamp_replay(self):
        """Testet die Kernfunktionalität für die Zurückspul-/Replay-Funktion."""
        await self.db.upsert_radio(1001, "Jan2")
        await self.db.upsert_radio(1002, "Mario")

        # T0: 10:00:00
        await self.db.insert_gps(1001, 51.2200, 6.7700, timestamp="2026-09-08T10:00:00Z")
        await self.db.insert_gps(1002, 51.2210, 6.7710, timestamp="2026-09-08T10:00:00Z")
        await self.db.insert_marker(51.2250, 6.7750, "info", "Bereitstellung", "low", timestamp="2026-09-08T10:00:00Z")

        # T1: 10:05:00 - Jan2 bewegt sich
        await self.db.insert_gps(1001, 51.2300, 6.7800, timestamp="2026-09-08T10:05:00Z")
        await self.db.insert_marker(51.2350, 6.7850, "hazard", "Gasgeruch", "critical", timestamp="2026-09-08T10:05:00Z")

        # T2: 10:10:00 - Jan2 bewegt sich weiter
        await self.db.insert_gps(1001, 51.2400, 6.7900, timestamp="2026-09-08T10:10:00Z")

        # Abfrage 1: Zustand bei T0 (10:00:00)
        state_t0 = await self.db.get_state_at_timestamp("2026-09-08T10:01:00Z")
        self.assertEqual(len(state_t0["radios"]), 2)
        jan_pos_t0 = [r for r in state_t0["radios"] if r["radio_id"] == 1001][0]
        self.assertAlmostEqual(jan_pos_t0["lat"], 51.2200)
        # Nur 1 Marker existierte bei T0
        self.assertEqual(len(state_t0["markers"]), 1)
        self.assertEqual(state_t0["markers"][0]["typ"], "info")

        # Abfrage 2: Zustand bei T1 (10:05:00)
        state_t1 = await self.db.get_state_at_timestamp("2026-09-08T10:06:00Z")
        jan_pos_t1 = [r for r in state_t1["radios"] if r["radio_id"] == 1001][0]
        self.assertAlmostEqual(jan_pos_t1["lat"], 51.2300)
        # 2 Marker existierten bei T1
        self.assertEqual(len(state_t1["markers"]), 2)

        # Abfrage 3: Zustand bei T2 (10:10:00)
        state_t2 = await self.db.get_state_at_timestamp("2026-09-08T10:11:00Z")
        jan_pos_t2 = [r for r in state_t2["radios"] if r["radio_id"] == 1001][0]
        self.assertAlmostEqual(jan_pos_t2["lat"], 51.2400)


if __name__ == "__main__":
    unittest.main()
