import os
import time
import threading
import json
import random
import webbrowser
from mission_logger import MissionLogger
from mission_server import MissionSSEServer

# Ensure RECORD_DIR exists
RECORD_DIR = os.path.join(os.path.dirname(__file__), "recordings")
os.makedirs(RECORD_DIR, exist_ok=True)

print("Starte Dashboard Simulation...")

mission = MissionLogger()
mission.start("Simulation_Demo")

server = MissionSSEServer(mission)
url = server.start()
print(f"Server gestartet unter {url}")
# Öffnet die Timeline direkt im Browser
webbrowser.open(url)

# Simuliere fortlaufenden Status (USV & Router)
def sim_status():
    usv_batt = 100
    while mission.active:
        if usv_batt > 20:
            usv_batt -= 1
        tx = random.uniform(0.5, 5.0)
        rx = random.uniform(10.0, 50.0)
        
        server.set_usv({
            "batt_pct": usv_batt,
            "load_pct": random.randint(10, 30),
            "time_left_min": usv_batt * 3,
            "temp_c": 32,
            "v_in": 230,
            "v_out": 230,
            "status": "Normal"
        })
        server.set_router({
            "erreichbar": True,
            "sys_name": "TP-Link ER605",
            "uptime_s": 150000,
            "cpu_load": random.randint(5, 25),
            "traffic_in_mbps": rx,
            "traffic_out_mbps": tx
        })
        time.sleep(2)

threading.Thread(target=sim_status, daemon=True).start()

# Events zeitversetzt einspielen
def sim_events():
    time.sleep(2)
    mission.add_note("Simulation gestartet. Systeme online.")
    
    time.sleep(2)
    print("Simuliere TS1 Funkspruch...")
    mission.log_event({"type": "ptt_start", "radio_id": 1001, "slot": "TS1", "name": "Alpha 1", "rssi": -65, "_byteCount": 16000})
    time.sleep(3)
    mission.log_event({"type": "ptt_end", "radio_id": 1001, "slot": "TS1", "duration": 3.0, "byte_count": 16000, "byte_offset": 0})
    
    time.sleep(2)
    print("Simuliere GPS Event...")
    mission.log_event({"type": "gps", "radio_id": 1002, "lat": 51.5234, "lon": 10.1234, "name": "Bravo 2"})
    
    time.sleep(2)
    print("Simuliere USB-Live Aufnahme (OHNE reale Hardware)...")
    mission.log_event({"type": "ptt_start", "radio_id": 999999, "slot": "USB", "name": "USB-Live", "rssi": -40, "_byteCount": 0})
    time.sleep(4)
    mission.log_event({"type": "ptt_end", "radio_id": 999999, "slot": "USB", "duration": 4.0, "byte_count": 0, "_audio_file": None})
    
    time.sleep(1)
    mission.add_note("USB-Spracherkennung (VOX) erfolgreich.")
    
    time.sleep(2)
    print("Simuliere TS2 Funkspruch...")
    mission.log_event({"type": "ptt_start", "radio_id": 1005, "slot": "TS2", "name": "Charlie 3", "rssi": -85, "_byteCount": 32000})
    time.sleep(2)
    mission.log_event({"type": "ptt_end", "radio_id": 1005, "slot": "TS2", "duration": 2.0, "byte_count": 32000, "byte_offset": 16000})
    
    time.sleep(3)
    mission.add_note("Simulation abgeschlossen. Alles laeuft flüssig.")
    print("\n--- Alle simulierten Events gesendet ---")
    print("Schau dir den Zeitstrahl im Browser an!")
    print("Das Skript beendet sich in 60 Sekunden automatisch...")
    
threading.Thread(target=sim_events, daemon=True).start()

time.sleep(60)
mission.stop()
server.stop()
print("Simulation beendet.")
