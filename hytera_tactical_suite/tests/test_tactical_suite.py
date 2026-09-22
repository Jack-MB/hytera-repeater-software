"""
Umfassende Integrationstests für die Hytera Tactical Suite
Testet alle 7 neuen taktischen Erweiterungen:
1. Notruf & Totmann (Emergency Trigger & Acknowledge)
2. Multi-Plan & Stockwerks-Zuweisung
3. HF-Pegel & RSSI-Heatmap
4. Taktische Zeichen (DV 102) & Vorfälle
5. 1-Klick Einsatzbericht (HTML & JSON)
6. Offline-Kachel Cache & Proxy
7. Funkspruch Audio-Voice-Log & Synthesizer
"""

import os
import sys
import json
import io
import unittest
from PIL import Image
from fastapi.testclient import TestClient

# Pfade auflösen
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
SUITE_DIR = os.path.abspath(os.path.join(TEST_DIR, ".."))
if SUITE_DIR not in sys.path:
    sys.path.insert(0, SUITE_DIR)

from main import app
from audio_manager import audio_manager


class TestHyteraTacticalSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._client_cm = TestClient(app)
        cls.client = cls._client_cm.__enter__()
        # Für Testzwecke zwei Testfunkgeräte isoliert anlegen
        cls.client.post("/api/radios", json={"radio_id": 1001, "alias": "Test-Einheit 1001", "device_model": "HP785", "has_gps": True, "floor_level": "EG"})
        cls.client.post("/api/radios", json={"radio_id": 1002, "alias": "Test-Einheit 1002", "device_model": "RT81", "has_gps": False, "floor_level": "EG"})

    @classmethod
    def tearDownClass(cls):
        cls._client_cm.__exit__(None, None, None)
        try:
            from clean_database import clean_suite
            clean_suite()
        except Exception:
            pass

    def test_01_index_and_report_html(self):
        """Testet die Auslieferung von Hauptoberfläche und Druck-Einsatzbericht."""
        r_index = self.client.get("/")
        self.assertEqual(r_index.status_code, 200)
        self.assertIn("HYTERA HR1065 // TACTICAL SUITE", r_index.text)
        self.assertIn("emergency-banner", r_index.text)
        self.assertIn("measure-dock", r_index.text)

        r_report = self.client.get("/report")
        self.assertEqual(r_report.status_code, 200)
        self.assertIn("EINSATZPROTOKOLL & TAKTISCHER LAGEBERICHT", r_report.text)
        self.assertIn("tbl-incidents", r_report.text)

    def test_02_health_check(self):
        """Testet den /api/health Status-Endpunkt."""
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "online")
        self.assertEqual(data["app"], "Hytera Tactical Suite")
        self.assertIn("total_radios", data)

    def test_03_initial_state(self):
        """Testet /api/initial-state auf Vollständigkeit aller taktischen Daten."""
        r = self.client.get("/api/initial-state")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("tactical_symbols", data)
        self.assertIn("plans", data)
        self.assertIn("geofences", data)
        self.assertIn("infrastructure", data)
        self.assertIn("elw", data["tactical_symbols"])

    def test_04_emergency_lifecycle(self):
        """Testet Notruf-Auslösung, Statusanzeige und Quittierung durch den Disponenten."""
        # 1. Notruf auslösen für Radio 1001
        r_trig = self.client.post("/api/radios/1001/emergency", json={"emergency_type": "MAN-DOWN / NOTFALL"})
        self.assertEqual(r_trig.status_code, 200)
        data_trig = r_trig.json()
        self.assertTrue(data_trig["alert"]["is_emergency"])
        self.assertEqual(data_trig["alert"]["emergency_type"], "MAN-DOWN / NOTFALL")

        # 2. Prüfen ob Notruf in Radios-Liste aktiv ist
        r_list = self.client.get("/api/radios")
        radios = r_list.json()["radios"]
        r1001 = next(r for r in radios if r["radio_id"] == 1001)
        self.assertTrue(r1001["is_emergency"])

        # 3. Notruf quittieren
        r_ack = self.client.post("/api/radios/1001/ack-emergency")
        self.assertEqual(r_ack.status_code, 200)
        self.assertFalse(r_ack.json()["is_emergency"])

        # 4. Erneut prüfen ob quittiert
        r_list2 = self.client.get("/api/radios")
        r1001_cleared = next(r for r in r_list2.json()["radios"] if r["radio_id"] == 1001)
        self.assertFalse(r1001_cleared["is_emergency"])

    def test_05_multi_plan_lifecycle(self):
        """Testet das Erstellen, Auflisten, Aktivieren und Löschen von Multi-Floor Lageplänen."""
        # Plan für 1. OG erstellen
        plan_data = {
            "name": "Werkshalle 1. OG",
            "floor_level": "1. OG",
            "image_url": "/static/assets/tactical_overlay.png",
            "bounds": [[51.22, 6.77], [51.24, 6.79]],
            "opacity": 0.8,
            "is_active": False
        }
        r_create = self.client.post("/api/plans", json=plan_data)
        self.assertEqual(r_create.status_code, 200)
        pid = r_create.json()["plan_id"]

        # Pläne abrufen
        r_get = self.client.get("/api/plans")
        plans = r_get.json()["plans"]
        self.assertTrue(any(p["id"] == pid for p in plans))

        # Plan aktivieren
        r_act = self.client.post(f"/api/plans/{pid}/activate")
        self.assertEqual(r_act.status_code, 200)
        self.assertTrue(r_act.json()["active_plan"]["is_active"])

        # Plan löschen
        r_del = self.client.delete(f"/api/plans/{pid}")
        self.assertEqual(r_del.status_code, 200)

    def test_06_radio_floor_assignment(self):
        """Testet die Zuweisung eines Funkgeräts zu einem Stockwerk."""
        r = self.client.post("/api/radios/1002/floor", json={"floor_level": "2. OG"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["floor_level"], "2. OG")

        r_get = self.client.get("/api/radios")
        r1002 = next(rad for rad in r_get.json()["radios"] if rad["radio_id"] == 1002)
        self.assertEqual(r1002["floor_level"], "2. OG")

    def test_07_geofence_lifecycle(self):
        """Testet das Anlegen und Löschen von Sicherheitszonen (Geofences)."""
        gf_data = {
            "name": "Absperrung Trümmerschatten",
            "zone_type": "danger",
            "shape": "circle",
            "center_lat": 51.2325,
            "center_lon": 6.7800,
            "radius_m": 120.0
        }
        r_create = self.client.post("/api/geofences", json=gf_data)
        self.assertEqual(r_create.status_code, 200)
        gfid = r_create.json()["geofence_id"]

        r_list = self.client.get("/api/geofences")
        self.assertTrue(any(g["id"] == gfid for g in r_list.json()["geofences"]))

        r_del = self.client.delete(f"/api/geofences/{gfid}")
        self.assertEqual(r_del.status_code, 200)

    def test_08_tactical_symbol_marker(self):
        """Testet die Erstellung eines Markers mit BOS DV 102 Taktischem Zeichen."""
        marker_req = {
            "lat": 51.2340,
            "lon": 6.7810,
            "typ": "base",
            "beschreibung": "ELW 1 Bereitstellung Nord",
            "prioritaet": "critical",
            "author": "Einsatzleiter",
            "tactical_symbol": "elw"
        }
        r_create = self.client.post("/api/markers", json=marker_req)
        self.assertEqual(r_create.status_code, 200)
        m = r_create.json()["marker"]
        self.assertEqual(m["tactical_symbol"], "elw")
        self.assertEqual(m["prioritaet"], "critical")

        # Aufräumen
        self.client.delete(f"/api/markers/{m['id']}")

    def test_09_rssi_coverage_endpoint(self):
        """Testet den /api/rssi-coverage Endpunkt für HF-Signalpegel."""
        r = self.client.get("/api/rssi-coverage?limit=50")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("points", data)
        self.assertIsInstance(data["points"], list)

    def test_10_tile_cache_proxy(self):
        """Testet den lokalen Kachel-Proxy /api/tiles/{z}/{x}/{y}.png."""
        r = self.client.get("/api/tiles/15/17150/10900.png")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/png")
        self.assertTrue(len(r.content) > 0)

    def test_11_audio_recording_generation(self):
        """Testet die automatische WAV-Synthese für Funksprüche."""
        audio_url = audio_manager.generate_tactical_radio_audio(call_id=9999, duration_sec=1.5, alias="Test 1")
        self.assertIn("/static/recordings/call_9999.wav", audio_url)

        # Über statische Route abrufen
        r = self.client.get(audio_url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(len(r.content) > 100)

    def test_12_report_data_endpoint(self):
        """Testet den aggregierten Einsatzbericht-Endpunkt /api/report/data."""
        r = self.client.get("/api/report/data")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("title", data)
        self.assertIn("summary", data)
        self.assertIn("radios", data)
        self.assertIn("infrastructure", data)
        self.assertGreaterEqual(data["summary"]["total_radios"], 2)

    def test_13_overlay_upload_image(self):
        """Testet den Upload und die Bildkonvertierung eines neuen Lageplans."""
        img = Image.new("RGB", (300, 200), color=(30, 80, 160))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        response = self.client.post(
            "/api/overlay/upload",
            files={"file": ("test_hallenplan.png", buf, "image/png")}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("/static/uploads/", data["image_url"])
        self.assertEqual(data["width"], 300)
        self.assertEqual(data["height"], 200)

    def test_14_mock_toggle(self):
        """Testet das Pausieren und Reaktivieren des Mock-Simulators."""
        r1 = self.client.post("/api/mock/toggle")
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post("/api/mock/toggle")
        self.assertEqual(r2.status_code, 200)

    def test_15_settings_update(self):
        """Testet das Speichern und Abrufen persistenter Einstellungen."""
        r = self.client.post("/api/settings", json={"settings": {"breadcrumbs_enabled": True, "active_floor": "EG"}})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["settings"]["breadcrumbs_enabled"])

    def test_16_map_provider_and_api_key(self):
        """Testet das Konfigurieren von Karten-Providern (Google Maps, OSM) und optionalen API-Keys."""
        # 1. Standardeinstellung prüfen: Echtbetrieb (Mock aus), OSM als Standard
        r_init = self.client.get("/api/initial-state")
        self.assertEqual(r_init.status_code, 200)
        settings = r_init.json().get("settings", {})
        self.assertFalse(settings.get("mock_active", True), "Mock-Modus muss standardmäßig deaktiviert sein")
        self.assertIn("map_provider", settings)
        self.assertIn("map_api_key", settings)

        # 2. Google Maps Hybrid mit API-Key speichern
        test_key = "AIzaSyTacticalDeploymentTestKey123"
        r_save = self.client.post("/api/settings", json={
            "settings": {
                "map_provider": "google_hybrid",
                "map_api_key": test_key
            }
        })
        self.assertEqual(r_save.status_code, 200)
        saved_settings = r_save.json()["settings"]
        self.assertEqual(saved_settings["map_provider"], "google_hybrid")
        self.assertEqual(saved_settings["map_api_key"], test_key)

        # 3. Über /api/settings abrufen
        r_get = self.client.get("/api/settings")
        self.assertEqual(r_get.status_code, 200)
        retrieved = r_get.json()["settings"]
        self.assertEqual(retrieved["map_provider"], "google_hybrid")
        self.assertEqual(retrieved["map_api_key"], test_key)

        # 4. Zurück auf OSM setzen
        r_reset = self.client.post("/api/settings", json={
            "settings": {
                "map_provider": "osm",
                "map_api_key": ""
            }
        })
        self.assertEqual(r_reset.status_code, 200)
        self.assertEqual(r_reset.json()["settings"]["map_provider"], "osm")

    def test_17_radio_crud_and_json_sync(self):
        """Testet Funkgeräte-Verwaltung (Anlegen, Bearbeiten, Löschen) und ids.json Synchronisation."""
        # 1. Funkgerät 9999 anlegen
        r_create = self.client.post("/api/radios", json={
            "radio_id": 9999,
            "alias": "Test-Trupp Alpha",
            "device_model": "Hytera HP785",
            "has_gps": True,
            "floor_level": "1. OG"
        })
        self.assertEqual(r_create.status_code, 200)

        # 2. Prüfen ob Radio in /api/radios vorhanden
        r_list = self.client.get("/api/radios")
        radios = r_list.json()["radios"]
        r9999 = next((r for r in radios if r["radio_id"] == 9999), None)
        self.assertIsNotNone(r9999)
        self.assertEqual(r9999["alias"], "Test-Trupp Alpha")
        self.assertEqual(r9999["device_model"], "Hytera HP785")
        self.assertTrue(r9999["has_gps"])

        # 3. Prüfen ob ids.json lokal existiert und Radio 9999 enthält
        ids_path = os.path.join(SUITE_DIR, "ids.json")
        self.assertTrue(os.path.exists(ids_path), "ids.json muss existieren")
        with open(ids_path, "r", encoding="utf-8") as f:
            ids_data = json.load(f)
        self.assertIn("9999", ids_data)
        self.assertEqual(ids_data["9999"]["name"], "Test-Trupp Alpha")
        self.assertEqual(ids_data["9999"]["geraet"], "Hytera HP785")

        # 4. Funkgerät aktualisieren
        r_up = self.client.post("/api/radios", json={
            "radio_id": 9999,
            "alias": "Test-Trupp Alpha (Updated)",
            "device_model": "Hytera PDC680",
            "has_gps": False,
            "floor_level": "2. OG"
        })
        self.assertEqual(r_up.status_code, 200)

        with open(ids_path, "r", encoding="utf-8") as f:
            ids_data = json.load(f)
        self.assertEqual(ids_data["9999"]["name"], "Test-Trupp Alpha (Updated)")

        # 5. Funkgerät löschen
        r_del = self.client.delete("/api/radios/9999")
        self.assertEqual(r_del.status_code, 200)

        # 6. Prüfen ob in /api/radios und ids.json entfernt
        r_list2 = self.client.get("/api/radios")
        self.assertIsNone(next((r for r in r_list2.json()["radios"] if r["radio_id"] == 9999), None))
        with open(ids_path, "r", encoding="utf-8") as f:
            ids_data = json.load(f)
        self.assertNotIn("9999", ids_data)

    def test_18_mission_lifecycle_and_report(self):
        """Testet Einsatz-Stammdaten Lebenszyklus und dynamische Berichts-Generierung."""
        # 1. Einsatzdaten setzen
        mission_payload = {
            "mission_title": "Großbrand Industriegebiet Hafen",
            "mission_code": "E-2026-999",
            "mission_status": "running",
            "mission_leader": "Florian ELW 1 (Kdt. Schmidt)",
            "mission_location": "Speicherstraße 12, Halle B",
            "mission_channel": "HR1065 Relais (Kanal 1 / TG 101)",
            "mission_notes": "Vollbrand im Dachstuhl. 3 Trupps unter PA im Innenangriff."
        }
        r_set = self.client.post("/api/mission", json=mission_payload)
        self.assertEqual(r_set.status_code, 200)

        # 2. Über /api/mission abrufen
        r_get = self.client.get("/api/mission")
        self.assertEqual(r_get.status_code, 200)
        m = r_get.json()
        self.assertEqual(m["mission_title"], "Großbrand Industriegebiet Hafen")
        self.assertEqual(m["mission_code"], "E-2026-999")
        self.assertEqual(m["mission_leader"], "Florian ELW 1 (Kdt. Schmidt)")
        self.assertEqual(m["mission_location"], "Speicherstraße 12, Halle B")

        # 3. Über /api/report/data abrufen und prüfen, ob Einsatzdaten im Bericht ankommen
        r_rep = self.client.get("/api/report/data")
        self.assertEqual(r_rep.status_code, 200)
        rep = r_rep.json()
        self.assertEqual(rep["title"], "Großbrand Industriegebiet Hafen")
        self.assertEqual(rep["mission_id"], "E-2026-999")
        self.assertEqual(rep["author"], "Florian ELW 1 (Kdt. Schmidt)")
        self.assertEqual(rep["location"], "Speicherstraße 12, Halle B")
        self.assertEqual(rep["status"], "running")
        self.assertEqual(rep["notes"], "Vollbrand im Dachstuhl. 3 Trupps unter PA im Innenangriff.")

        # 4. Einsatz beenden
        r_close = self.client.post("/api/mission/close")
        self.assertEqual(r_close.status_code, 200)
        self.assertEqual(r_close.json()["mission"]["mission_status"], "finished")

    def test_19_geocoding_api(self):
        """Testet den Geocoding-Endpunkt für direkte Koordinaten und lokale Marker."""
        # 1. Direkte Koordinateneingabe testen
        r_coord = self.client.get("/api/geocode?q=51.2277,%206.7735")
        self.assertEqual(r_coord.status_code, 200)
        res_coord = r_coord.json()["results"]
        self.assertTrue(len(res_coord) > 0)
        self.assertEqual(res_coord[0]["type"], "coordinate")
        self.assertAlmostEqual(res_coord[0]["lat"], 51.2277, places=4)
        self.assertAlmostEqual(res_coord[0]["lon"], 6.7735, places=4)

        # 2. Lokalen Marker anlegen und per Geocode suchen
        r_m = self.client.post("/api/markers", json={
            "lat": 51.2300,
            "lon": 6.7850,
            "beschreibung": "Bereitstellungsraum Rheinterrasse",
            "typ": "Logistik"
        })
        self.assertEqual(r_m.status_code, 200)

        r_search = self.client.get("/api/geocode?q=Rheinterrasse")
        self.assertEqual(r_search.status_code, 200)
        res_search = r_search.json()["results"]
        match = next((m for m in res_search if "Rheinterrasse" in m["name"]), None)
        self.assertIsNotNone(match, "Marker 'Rheinterrasse' muss im Geocode-Ergebnis gefunden werden")
        self.assertEqual(match["type"], "marker")

    def test_20_hardware_monitor_offline_behavior(self):
        """Testet, dass ohne echte Hardware sauber OFFLINE ohne Scheinwerte gemeldet wird."""
        r_infra = self.client.get("/api/infrastructure")
        self.assertEqual(r_infra.status_code, 200)
        infra = r_infra.json()

        # Ohne Verbindung müssen alle Geräte offline sein (keine Scheinwerte!)
        self.assertEqual(infra["repeater"]["status"], "offline")
        self.assertIsNone(infra["repeater"]["temperature_c"])
        self.assertIsNone(infra["repeater"]["vswr"])
        self.assertIsNone(infra["repeater"]["forward_power_w"])

        self.assertEqual(infra["usv"]["status"], "offline")
        self.assertIsNone(infra["usv"]["battery_charge_percent"])
        self.assertIsNone(infra["usv"]["input_voltage_v"])

        self.assertEqual(infra["router"]["status"], "offline")
        self.assertIsNone(infra["router"]["ping_ms"])
        self.assertEqual(infra["router"]["signal_bars"], 0)

    def test_21_map_center_and_settings_persistence(self):
        """Testet das Speichern und Laden des individuellen Kartenzentrums und der Hardware-IPs."""
        new_settings = {
            "map_center": [52.5200, 13.4050],
            "map_zoom": 16,
            "hw_repeater_ip": "192.168.10.50",
            "hw_usv_ip": "192.168.10.51",
            "hw_router_ip": "192.168.10.1",
            "hw_simulation": False
        }
        r_save = self.client.post("/api/settings", json={"settings": new_settings})
        self.assertEqual(r_save.status_code, 200)

        # Über /api/settings prüfen
        r_get = self.client.get("/api/settings")
        s = r_get.json()["settings"]
        self.assertEqual(s["map_center"], [52.5200, 13.4050])
        self.assertEqual(s["map_zoom"], 16)
        self.assertEqual(s["hw_repeater_ip"], "192.168.10.50")
        self.assertEqual(s["hw_usv_ip"], "192.168.10.51")

        # Über /api/initial-state prüfen (Karte startet mit diesen Werten)
        r_init = self.client.get("/api/initial-state")
        init_data = r_init.json()
        self.assertEqual(init_data["map_center"], [52.5200, 13.4050])
        self.assertEqual(init_data["map_zoom"], 16)


if __name__ == "__main__":
    unittest.main()

