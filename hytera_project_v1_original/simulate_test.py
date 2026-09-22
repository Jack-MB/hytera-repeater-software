# -*- coding: utf-8 -*-
"""
simulate_test.py – Vollstaendiger End-to-End-Test
Simuliert einen Einsatz ohne echte Hardware und prueft:
  - MissionLogger (Events, Persistenz, Report)
  - MissionSSEServer (/state, /report, /audio, /events SSE)
  - timeline_viewer.html Syntaxpruefung
"""
import sys, os, json, time, threading, struct, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = "\033[92m[OK]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
INFO = "\033[94m[INFO]\033[0m"

errors = []

def ok(msg):    print(f"  {PASS} {msg}")
def fail(msg):  print(f"  {FAIL} {msg}"); errors.append(msg)
def info(msg):  print(f"  {INFO} {msg}")

print("\n=== 1. MISSION LOGGER ===")
try:
    from mission_logger import MissionLogger, MISSIONS_DIR
    ok("Import MissionLogger")
except Exception as e:
    fail(f"Import MissionLogger: {e}"); sys.exit(1)

# Testeinsatz anlegen
ml = MissionLogger()
TEST_NAME = "_TEST_SIMULATION_"

# Aufraeumen falls vorhanden
import shutil as _sh
test_dir = os.path.join(MISSIONS_DIR, TEST_NAME)
if os.path.exists(test_dir): _sh.rmtree(test_dir)

mdir = ml.start(TEST_NAME)
ok(f"Mission gestartet: {mdir}")

# Master-Aufnahmedateien oeffnen
ml.open_master_rec("TS1")
ml.open_master_rec("TS2")
if "TS1" in ml._master_files and "TS2" in ml._master_files:
    ok("Master-Aufnahmedateien geoeffnet (TS1, TS2)")
else:
    fail("Master-Aufnahmedateien nicht geoeffnet")

# Synthetisches G.711-Audio schreiben (Stille = 0x7F, Ton = variierende Werte)
def make_audio(sec, freq=1000):
    """Erzeugt sec Sekunden synthetische G.711-µ-law Daten (8kHz)."""
    import math
    samples = bytearray(int(sec * 8000))
    for i in range(len(samples)):
        # Einfache Sinuswelle, als µ-law kodiert (vereinfacht: nur 8-bit)
        val = int(127 * math.sin(2 * math.pi * freq * i / 8000))
        samples[i] = (val + 128) & 0xFF
    return bytes(samples)

# Etwas Audio vor dem ersten PTT schreiben (Stille)
silence = bytes([0x7F] * 8000)  # 1 Sekunde Stille
ml.write_audio("TS1", silence)

# PTT 1 simulieren: Radio 1042, TS1, 3 Sekunden
time.sleep(0.05)
ml.log_ptt_start(1042, "TS1")
audio1 = make_audio(3.0)
ml.write_audio("TS1", audio1)
time.sleep(0.05)
ml.log_ptt_end(1042, "TS1")
ok("PTT 1 (Radio 1042, TS1, 3s) geloggt")

# Stille zwischen PTTs
ml.write_audio("TS1", silence)

# PTT 2 simulieren: Radio 1017, TS2, 5 Sekunden
time.sleep(0.05)
ml.log_ptt_start(1017, "TS2")
ml.open_master_rec("TS2")
audio2 = make_audio(5.0)
ml.write_audio("TS2", audio2)
time.sleep(0.05)
ml.log_ptt_end(1017, "TS2")
ok("PTT 2 (Radio 1017, TS2, 5s) geloggt")

# GPS-Event
ml.log_gps(1042, 48.137154, 11.576124, speed=0, heading=270)
ok("GPS-Event geloggt")

# SMS
ml.log_sms(1042, 1017, "Ich bin auf Position", is_group=False)
ok("SMS geloggt")

# USV
ml.log_usv(85, "Normal", 120)
ok("USV-Event gebatcht")

# Notiz
ml.add_note("Testeinsatz Simulation")
ok("Notiz geloggt")

# Radio online/offline
ml.log_radio_status(1042, True)
ml.log_radio_status(1017, True)
ok("Radio-Status geloggt")

# Batch sofort flushen
ml._flush_batch()
ok("Batch geflusht")

# Pruefen ob Events gespeichert
ptt_events = [e for e in ml.events if "ptt" in e.etype]
if len(ptt_events) >= 4:  # 2x start + 2x end
    ok(f"PTT-Events: {len(ptt_events)} ({[e.etype for e in ptt_events]})")
else:
    fail(f"Zu wenige PTT-Events: {len(ptt_events)}, erwartet 4")

# Pruefen ob GPS im Batch oder Events
gps_events = [e for e in ml.events if e.etype == "gps"]
if gps_events:
    ok(f"GPS-Event in events: {gps_events[0].data}")
else:
    info("GPS noch im Batch (normal nach flush sollte da sein)")

# JSON-Datei pruefen
if os.path.exists(ml.json_path):
    with open(ml.json_path, encoding="utf-8") as f:
        jdata = json.load(f)
    ok(f"mission.json vorhanden: {len(jdata.get('events', []))} Events")
else:
    fail("mission.json nicht gefunden")

print("\n=== 2. REPORT GENERATOR ===")
IDS_MAP = {
    "1042": {"name": "Meyer, Klaus", "geraet": "HT750 #3"},
    "1017": {"name": "Schmidt, Anna", "geraet": "PD785 #1"},
}

try:
    report_html = ml.generate_report(ids_map=IDS_MAP, live=False)
    ok(f"generate_report(live=False) liefert Pfad: {os.path.basename(report_html)}")
    if os.path.exists(report_html):
        ok(f"Funkprotokoll.html vorhanden ({os.path.getsize(report_html)} Bytes)")
        with open(report_html, encoding="utf-8") as f:
            content = f.read()
        checks = ["Meyer, Klaus", "Schmidt, Anna", "Funkprotokoll",
                  "Zusammenfassung", "Statistik"]
        for c in checks:
            if c in content: ok(f"  Report enthält '{c}'")
            else: fail(f"  Report fehlt '{c}'")
    else:
        fail("Funkprotokoll.html wurde nicht gespeichert")
except Exception as e:
    fail(f"generate_report: {e}")

try:
    live_html = ml.generate_report(ids_map=IDS_MAP, live=True)
    if isinstance(live_html, str) and "refresh" in live_html:
        ok("generate_report(live=True) liefert String mit meta-refresh")
    elif isinstance(live_html, str):
        fail("generate_report(live=True): kein meta-refresh im HTML")
    else:
        fail(f"generate_report(live=True): falscher Typ {type(live_html)}")
except Exception as e:
    fail(f"generate_report(live=True): {e}")

print("\n=== 3. SSE SERVER ===")

TEST_PORT = 19081

try:
    from mission_server import MissionSSEServer
    import mission_server as _ms_mod
    _ms_mod.PORT = TEST_PORT
    ok("Import MissionSSEServer")
except Exception as e:
    fail(f"Import MissionSSEServer: {e}"); sys.exit(1)

srv = MissionSSEServer(ml, ids_file=os.path.join(os.path.dirname(__file__), "ids.json"))
url = srv.start()
ok(f"Server gestartet: {url}")
time.sleep(0.5)  # Warten bis Port offen

import urllib.request, urllib.error

def http_get(path, timeout=5):
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{TEST_PORT}{path}", timeout=timeout)
        return req.status, req.read(), req.headers.get("Content-Type","")
    except urllib.error.HTTPError as e:
        return e.code, b"", ""
    except Exception as e:
        return 0, str(e).encode(), ""

# /state
status, body, ct = http_get("/state")
if status == 200 and b'"events"' in body:
    data = json.loads(body)
    ok(f"/state: {len(data.get('events',[]))} Events, active={data.get('active')}")
else:
    fail(f"/state: HTTP {status}, CT={ct}")

# /report
status, body, ct = http_get("/report")
if status == 200 and b"Funkprotokoll" in body:
    ok(f"/report: HTTP 200, {len(body)} Bytes, refresh in HTML: {'refresh' in body.decode('utf-8','ignore')}")
else:
    fail(f"/report: HTTP {status}")

# /audio/TS1/<start>/<dur>
# PTT 1 startet nach 1s Stille (~rel=1.0), dauert ~3s
# Wir fragen 2 Sekunden ab
ptt1 = next((e for e in ml.events if e.etype=="ptt_start" and e.data.get("slot")=="TS1"), None)
if ptt1:
    start_s = ptt1.rel_time
    status, body, ct = http_get(f"/audio/TS1/{start_s:.2f}/2.00")
    if status == 200 and ct.startswith("audio/wav"):
        # WAV-Header pruefen: "RIFF" + "WAVE" + fmt type 7 (µ-law)
        if body[:4] == b"RIFF" and body[8:12] == b"WAVE":
            fmt_type = struct.unpack_from("<H", body, 20)[0]
            ok(f"/audio/TS1: HTTP 200, {len(body)} Bytes WAV, Format={fmt_type} (7=µ-law)")
            if fmt_type != 7: fail("WAV-Format ist nicht µ-law (7)")
        else:
            fail(f"/audio: Kein gueltiger WAV-Header (magic={body[:12]})")
    else:
        fail(f"/audio/TS1: HTTP {status}, CT={ct}")
else:
    fail("Kein ptt_start fuer TS1 gefunden")

# /audio ohne Master-File (TS3 gibts nicht)
status, body, ct = http_get("/audio/TS3/0/1")
if status == 404:
    ok("/audio/TS3: korrekt 404 fuer nicht existierenden Slot")
else:
    fail(f"/audio/TS3: erwartet 404, bekam {status}")

# SSE-Verbindung testen (1 Event senden waehrend verbunden)
# SSE-Verbindung testen (direkt via socket, urllib versteht kein chunked SSE)
import socket
sse_ok = False
try:
    s = socket.create_connection(("127.0.0.1", TEST_PORT), timeout=3)
    s.sendall(b"GET /events HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: text/event-stream\r\n\r\n")
    buf = b""
    s.settimeout(2)
    try:
        while len(buf) < 4096:
            chunk = s.recv(512)
            if not chunk: break
            buf += chunk
            if b"data:" in buf: break
    except socket.timeout:
        pass
    s.close()
    if b"data:" in buf:
        ok(f"SSE: Init-Event empfangen ({len(buf)} Bytes)")
        sse_ok = True
    else:
        fail(f"SSE: Kein data:-Event in Antwort ({buf[:200]})")
except Exception as e:
    fail(f"SSE: Verbindungsfehler: {e}")

print("\n=== 4. AUDIO WAV-HEADER KORREKTUR ===")
# Pruefe WAV-Header genau (nach RIFF-Spec)
raw = make_audio(1.0)
hdr = MissionSSEServer.__init__.__module__  # nur zum Import-check
from mission_server import _Handler
wav_hdr = _Handler._mulaw_wav_header(len(raw))
print(f"  WAV-Header: {len(wav_hdr)} Bytes")
if wav_hdr[:4] == b"RIFF": ok("RIFF-Magic korrekt")
else: fail("RIFF-Magic fehlt")
if wav_hdr[8:12] == b"WAVE": ok("WAVE-Format korrekt")
else: fail("WAVE-Format fehlt")
fmt_type_local = struct.unpack_from("<H", wav_hdr, 20)[0]
if fmt_type_local == 7: ok("AudioFormat = 7 (µ-law)")
else: fail(f"AudioFormat = {fmt_type_local}, erwartet 7")
if wav_hdr[12:16] == b"fmt ": ok("fmt-Chunk vorhanden")
else: fail("fmt-Chunk fehlt")

print("\n=== 5. MISSION STOP & CLEANUP ===")
ml.stop()
if not ml.active:
    ok("Mission gestoppt")
else:
    fail("Mission noch aktiv nach stop()")

srv.stop()
ok("SSE-Server gestoppt")

# Aufraaeumen
if os.path.exists(test_dir):
    _sh.rmtree(test_dir)
    ok(f"Testdaten geloescht: {test_dir}")

print("\n" + "="*50)
if errors:
    print(f"\033[91m{len(errors)} FEHLER:\033[0m")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print(f"\033[92mALLE TESTS BESTANDEN\033[0m")
