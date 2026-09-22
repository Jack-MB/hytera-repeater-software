#!/usr/bin/env python3
"""
Hytera Tactical Suite - Datenbank- & Umgebungs-Bereiniger (Clean Reset)
Setzt die Suite auf einen absolut sauberen Zustand ohne Demo-Daten,
ohne Demo-Funkgeräte, ohne Audio-Logs und ohne temporäre Test-Uploads zurück.
"""

import os
import glob
import json
import sqlite3

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(MODULE_DIR, "tactical_suite.db")
IDS_PATH = os.path.join(MODULE_DIR, "ids.json")
RECORDINGS_DIR = os.path.join(MODULE_DIR, "static", "recordings")
UPLOADS_DIR = os.path.join(MODULE_DIR, "static", "uploads")


def clean_suite():
    print("[1/4] Bereinige Audioaufnahmen...")
    wav_files = glob.glob(os.path.join(RECORDINGS_DIR, "call_*.wav"))
    for f in wav_files:
        try:
            os.remove(f)
        except Exception:
            pass
    print(f"      -> {len(wav_files)} temporäre WAV-Audiodateien gelöscht.")

    print("[2/4] Bereinige Uploads...")
    up_files = glob.glob(os.path.join(UPLOADS_DIR, "overlay_*.png"))
    for f in up_files:
        try:
            os.remove(f)
        except Exception:
            pass
    print(f"      -> {len(up_files)} temporäre Plan-Dateien gelöscht.")

    print("[3/4] Setze ids.json auf leeren Zustand...")
    with open(IDS_PATH, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=2)
    print("      -> ids.json ist nun leer ({})")

    print("[4/4] Bereinige SQLite Datenbank tactical_suite.db...")
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Tabellen leeren
        c.execute("DELETE FROM radios;")
        c.execute("DELETE FROM gps_history;")
        c.execute("DELETE FROM ptt_logs;")
        c.execute("DELETE FROM markers;")
        c.execute("DELETE FROM geofences;")
        c.execute("DELETE FROM tactical_plans;")
        c.execute("DELETE FROM settings;")

        # Saubere Standardeinstellungen hinterlegen
        clean_settings = {
            "breadcrumbs_enabled": True,
            "audio_enabled": True,
            "ptt_pulse_enabled": True,
            "autopan_active": False,
            "active_floor": "EG",
            "map_provider": "osm",
            "map_api_key": "",
            "mock_active": False,
            "mission_data": {
                "mission_title": "",
                "mission_code": "",
                "mission_status": "preparation",
                "mission_leader": "",
                "mission_location": "",
                "mission_channel": "Kanal 1 / TS1 & TS2",
                "mission_notes": ""
            }
        }

        for key, val in clean_settings.items():
            val_str = json.dumps(val) if isinstance(val, (dict, list, bool)) else str(val)
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?);", (key, val_str))

        conn.commit()
        c.execute("VACUUM;")
        conn.close()
        print("      -> Alle Tabellen geleert, saubere Grundkonfiguration hinterlegt.")

    print("\n[ERFOLG] Hytera Tactical Suite ist jetzt 100% sauber (Clean Version ohne Demo-Daten).")


if __name__ == "__main__":
    clean_suite()
