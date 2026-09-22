# -*- coding: utf-8 -*-
import sys, socket, time, struct, threading, queue
sys.stdout.reconfigure(encoding="utf-8")

import pyaudio, audioop

PORTS   = [30012, 30014]
results = {}  # port -> list of (size, data)
lock    = threading.Lock()

def capture(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port)); s.settimeout(0.5)
    with lock: results[port] = []
    while not stop.is_set():
        try:
            data, addr = s.recvfrom(4096)
            with lock:
                results[port].append((len(data), data, addr[0]))
                print(f"[Port {port}] {len(data):5d} Bytes von {addr[0]}  HEX={data[:20].hex(' ')}")
        except socket.timeout: pass
    s.close()

stop = threading.Event()
for p in PORTS:
    threading.Thread(target=capture, args=(p,), daemon=True).start()

print("="*60)
print("  >>> PTT druecken und 5 Sekunden halten! <<<")
print("="*60)
time.sleep(8)
stop.set()
time.sleep(1)

# Analyse: Groesste Pakete finden (wahrscheinlich Audio)
print("\n\n=== ANALYSE ===")
audio_samples = []
for port, pkts in results.items():
    big = [(sz, d, a) for sz,d,a in pkts if sz > 30]
    print(f"Port {port}: {len(pkts)} Pakete gesamt, {len(big)} groessere (>30 Bytes)")
    for sz, d, a in big[:3]:
        print(f"  Groesse={sz}  HEX={d[:32].hex(' ')}")
        audio_samples.append(d)

if not audio_samples:
    print("\nKEINE Audio-Pakete empfangen!")
    print("Moegliche Gruende:")
    print("  1. PTT wurde nicht gedrueckt")
    print("  2. Repeater sendet nicht an diese IP")
    print("  3. 'Third Party Server IP' nicht korrekt gesetzt")
    sys.exit(0)

print(f"\n{len(audio_samples)} Audio-Pakete zum Testen gefunden.")
print("\nTeste alle Audio-Formate durch...\n")

pa = pyaudio.PyAudio()

def play_format(name, pcm_data, rate=8000):
    try:
        st = pa.open(format=pyaudio.paInt16, channels=1, rate=rate,
                     output=True, frames_per_buffer=160)
        st.write(pcm_data); time.sleep(0.3)
        st.stop_stream(); st.close()
        return True
    except Exception as e:
        print(f"  Fehler: {e}"); return False

for i, raw in enumerate(audio_samples[:5]):
    print(f"\n--- Paket {i+1} ({len(raw)} Bytes) ---")

    # Versuche 1: Roh-PCM 8kHz (kein Header)
    print("[1] Roh-PCM 8kHz (kein Header)...")
    play_format("Roh-PCM", raw)

    # Versuche 2: G.711 ulaw, kein Header
    print("[2] G.711 ulaw direkt...")
    try:
        pcm = audioop.ulaw2lin(raw, 2)
        play_format("ulaw direkt", pcm)
    except: print("  ulaw Fehler")

    # Versuche 3: G.711 alaw, kein Header
    print("[3] G.711 alaw direkt...")
    try:
        pcm = audioop.alaw2lin(raw, 2)
        play_format("alaw direkt", pcm)
    except: print("  alaw Fehler")

    # Versuche 4: RTP-Header überspringen (12 Bytes), dann ulaw
    if len(raw) > 12:
        print("[4] RTP+12 Bytes Header, dann ulaw...")
        try:
            pcm = audioop.ulaw2lin(raw[12:], 2)
            play_format("RTP ulaw", pcm)
        except: print("  Fehler")

    # Versuche 5: Hytera-Header (erste X Bytes) überspringen
    for skip in [4, 8, 16, 20, 24]:
        if len(raw) > skip:
            print(f"[5.{skip}] Skip {skip} Bytes, dann ulaw...")
            try:
                pcm = audioop.ulaw2lin(raw[skip:], 2)
                play_format(f"skip{skip} ulaw", pcm)
            except: pass

    input("\n  >>> Hast du etwas gehoert? (Enter zum naechsten Paket) <<<")

pa.terminate()
print("\nDiagnose abgeschlossen.")
