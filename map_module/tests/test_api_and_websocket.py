"""
End-to-End Integrationstests für FastAPI Backend & WebSockets
Testet REST-API-Endpunkte, WebSocket-Verbindung, Incident-Erstellung, Replay und HTML-Auslieferung.
"""

import os
import sys
import json
import io
import unittest
from fastapi.testclient import TestClient
from PIL import Image

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.abspath(os.path.join(TEST_DIR, ".."))
ROOT_DIR = os.path.abspath(os.path.join(MODULE_DIR, ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from map_module.main import app
class TestFastAPIAndWebSockets(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # TestClient mit lifespan-Kontext ausführen
        cls._client_cm = TestClient(app)
        cls.client = cls._client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._client_cm.__exit__(None, None, None)

    def test_01_get_index_html(self):
        """Testet die Auslieferung der Hauptoberfläche index.html."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("HYTERA HR1065 // LAGEZENTRUM", response.text)
        self.assertIn("visualization-timeline", response.text)
        self.assertIn("map", response.text)

    def test_02_health_check(self):
        """Testet den /api/health Endpunkt."""
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "online")
        self.assertIn("timestamp", data)
        self.assertIn("total_radios", data)

    def test_03_initial_state(self):
        """Testet den /api/initial-state Endpunkt."""
        response = self.client.get("/api/initial-state")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("map_center", data)
        self.assertIn("map_zoom", data)
        self.assertIn("overlay", data)
        self.assertIn("radios", data)
        self.assertIn("markers", data)

    def test_04_marker_lifecycle(self):
        """Testet Erstellung, Abruf und Löschung eines Vorfallsmarkers."""
        # 1. Marker erstellen
        new_marker_payload = {
          "lat": 51.2333,
          "lon": 6.7822,
          "typ": "hazard",
          "beschreibung": "Chemikalienaustritt Halle 3",
          "prioritaet": "critical",
          "author": "Disponent-01"
        }
        res_post = self.client.post("/api/markers", json=new_marker_payload)
        self.assertEqual(res_post.status_code, 200)
        post_data = res_post.json()
        self.assertEqual(post_data["status"], "ok")
        marker_id = post_data["marker"]["id"]
        self.assertTrue(marker_id > 0)

        # 2. Marker in Liste prüfen
        res_list = self.client.get("/api/markers")
        self.assertEqual(res_list.status_code, 200)
        markers = res_list.json()["markers"]
        created = [m for m in markers if m["id"] == marker_id]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["typ"], "hazard")
        self.assertEqual(created[0]["prioritaet"], "critical")

        # 3. Marker löschen
        res_del = self.client.delete(f"/api/markers/{marker_id}")
        self.assertEqual(res_del.status_code, 200)
        self.assertEqual(res_del.json()["status"], "ok")

        # 4. Prüfen ob gelöscht
        res_list_after = self.client.get("/api/markers")
        remaining = [m for m in res_list_after.json()["markers"] if m["id"] == marker_id]
        self.assertEqual(len(remaining), 0)

    def test_05_overlay_config(self):
        """Testet das Abrufen und Aktualisieren der Lageplan-Overlay-Einstellungen."""
        res_get = self.client.get("/api/overlay-config")
        self.assertEqual(res_get.status_code, 200)
        orig_config = res_get.json()
        self.assertIn("bounds", orig_config)

        # Update senden
        new_config = {
          "image_url": "/static/assets/tactical_overlay.png",
          "bounds": [[51.2200, 6.7700], [51.2450, 6.7950]],
          "opacity": 0.75,
          "visible": True
        }
        res_post = self.client.post("/api/overlay-config", json=new_config)
        self.assertEqual(res_post.status_code, 200)
        updated = res_post.json()["overlay"]
        self.assertAlmostEqual(updated["opacity"], 0.75)
        self.assertEqual(updated["bounds"][0][0], 51.2200)

    def test_06_timeline_endpoint(self):
        """Testet den /api/timeline Endpunkt."""
        response = self.client.get("/api/timeline?limit=100")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("items", data)
        self.assertIsInstance(data["items"], list)

    def test_07_history_replay_endpoint(self):
        """Testet den /api/history Endpunkt für den Zeitreise-Replay-Modus."""
        # Einen Marker anlegen
        self.client.post("/api/markers", json={
            "lat": 51.2311,
            "lon": 6.7811,
            "typ": "checkpoint",
            "beschreibung": "Kontrollpunkt West",
            "prioritaet": "normal",
            "author": "Operator"
        })

        ts = "2026-09-08T23:59:59Z"
        response = self.client.get(f"/api/history?timestamp={ts}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["query_timestamp"], ts)
        self.assertIn("radios", data)
        self.assertIn("markers", data)
        self.assertIn("breadcrumbs", data)

    def test_08_websocket_live_connection(self):
        """Testet WebSocket-Verbindung, Handshake und Ping/Pong."""
        with self.client.websocket_connect("/ws/live") as ws:
            # Ping senden
            ws.send_text(json.dumps({"action": "ping"}))
            # Antwort empfangen
            reply = ws.receive_text()
            data = json.loads(reply)
            self.assertEqual(data.get("type"), "pong")
            self.assertIn("timestamp", data)

    def test_09_infrastructure_endpoint(self):
        """Testet den /api/infrastructure Endpunkt für Repeater-, USV- und Router-Telemetrie."""
        response = self.client.get("/api/infrastructure")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("repeater", data)
        self.assertIn("usv", data)
        self.assertIn("router", data)
        self.assertIn("timestamp", data)
        self.assertIn("vswr", data["repeater"])
        self.assertIn("batterie_pct", data["usv"])
        self.assertIn("signal_bars", data["router"])

    def test_10_settings_endpoint(self):
        """Testet das Lesen und Aktualisieren von Systemeinstellungen (/api/settings)."""
        # 1. GET Settings
        res_get = self.client.get("/api/settings")
        self.assertEqual(res_get.status_code, 200)
        settings = res_get.json()["settings"]
        self.assertIn("gps_tracking_enabled", settings)
        self.assertIn("show_no_gps_units", settings)
        self.assertIn("theme", settings)

        # 2. POST Settings Update
        res_post = self.client.post("/api/settings", json={
            "settings": {
                "gps_tracking_enabled": False,
                "show_no_gps_units": True,
                "theme": "business"
            }
        })
        self.assertEqual(res_post.status_code, 200)
        updated = res_post.json()["settings"]
        self.assertEqual(updated["gps_tracking_enabled"], False)
        self.assertEqual(updated["show_no_gps_units"], True)
        self.assertEqual(updated["theme"], "business")

    def test_11_radio_position_and_gps_toggle(self):
        """Testet manuelle Positionszuweisung für Geräte ohne GPS und GPS-Fähigkeitstoggle."""
        # Funkgerät registrieren
        self.client.post("/api/radios", json={
            "radio_id": 9991,
            "alias": "Stationäre Wache Nord",
            "device_model": "RT81"
        })

        # Manuelle Position zuweisen
        res_pos = self.client.post("/api/radios/9991/position", json={
            "lat": 51.2355,
            "lon": 6.7855
        })
        self.assertEqual(res_pos.status_code, 200)
        pos_data = res_pos.json()
        self.assertEqual(pos_data["radio_id"], 9991)
        self.assertAlmostEqual(pos_data["lat"], 51.2355)
        self.assertAlmostEqual(pos_data["lon"], 6.7855)

        # GPS Toggle für Funkgerät
        res_gps = self.client.post("/api/radios/9991/gps-toggle", json={
            "has_gps": False
        })
        self.assertEqual(res_gps.status_code, 200)
        self.assertEqual(res_gps.json()["has_gps"], False)

    def test_12_mock_toggle(self):
        """Testet das Ein- und Ausschalten des Telemetrie-Simulators (/api/mock/toggle)."""
        res_toggle = self.client.post("/api/mock/toggle")
        self.assertEqual(res_toggle.status_code, 200)
        state_1 = res_toggle.json()["mock_active"]
        self.assertIsInstance(state_1, bool)

        # Erneut toggeln (Rückkehr zum Ausgangszustand)
        res_toggle_2 = self.client.post("/api/mock/toggle")
        self.assertEqual(res_toggle_2.status_code, 200)
        state_2 = res_toggle_2.json()["mock_active"]
        self.assertEqual(state_2, not state_1)

    def test_13_overlay_upload_image(self):
        """Testet den Datei-Upload für Lagepläne (/api/overlay/upload)."""
        # Erstelle ein kleines Test-Bild im Speicher
        img = Image.new("RGBA", (200, 150), color=(37, 99, 235, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        response = self.client.post(
            "/api/overlay/upload",
            files={"file": ("test_einsatzplan.png", buf, "image/png")}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("image_url", data)
        self.assertTrue(data["image_url"].startswith("/static/uploads/"))
        self.assertEqual(data["width"], 200)
        self.assertEqual(data["height"], 150)

    def test_14_recent_calls_endpoint(self):
        """Testet den Endpunkt /api/recent-calls für den Leitstellen-Call-Log-Ticker."""
        response = self.client.get("/api/recent-calls")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("calls", data)
        self.assertIsInstance(data["calls"], list)

    def test_15_overlay_calibration_persistence(self):
        """Testet die Speicherung und Persistenz kalibrierter GPS-Grenzkoordinaten."""
        new_calibrated_bounds = [[51.2210, 6.7710], [51.2420, 6.7920]]
        res_post = self.client.post("/api/overlay-config", json={
            "image_url": "/static/assets/tactical_overlay.png",
            "bounds": new_calibrated_bounds,
            "opacity": 0.80,
            "visible": True
        })
        self.assertEqual(res_post.status_code, 200)

        # Über GET abrufen und prüfen, ob die Bounds exakt übereinstimmen
        res_get = self.client.get("/api/overlay-config")
        self.assertEqual(res_get.status_code, 200)
        saved_config = res_get.json()
        self.assertEqual(saved_config["bounds"], new_calibrated_bounds)
        self.assertAlmostEqual(saved_config["opacity"], 0.80)


if __name__ == "__main__":
    unittest.main()
