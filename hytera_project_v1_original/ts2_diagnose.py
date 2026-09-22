# -*- coding: utf-8 -*-
"""
ts2_diagnose.py
Lauscht 30 Sekunden auf UDP-Port 30014 (TS2 Audio NAI).
Zeigt jeden eingehenden Paket mit Zeitstempel, Quell-IP und Groesse.
Starte das Script, dann per Funk auf TS2 senden und pruefen ob Pakete ankommen.
"""
import socket, struct, time

PORT     = 30014
TIMEOUT  = 30          # Sekunden
HSTRP_SIG = b'\x32\x42\x00'

def is_rtp(d):
    return len(d) >= 12 and (d[0] >> 6) == 2

def is_hstrp(d):
    return len(d) >= 6 and d[:3] == HSTRP_SIG

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("0.0.0.0", PORT))
except OSError as e:
    print(f"[FEHLER] Port {PORT} konnte nicht gebunden werden: {e}")
    print("         Evtl. laeuft der Dispatcher bereits und belegt den Port.")
    input("Enter druecken...")
    raise SystemExit(1)

sock.settimeout(1.0)
print(f"[TS2-DIAGNOSE] Hoere auf UDP-Port {PORT} fuer {TIMEOUT}s")
print(f"[TS2-DIAGNOSE] Bitte jetzt auf Kanal 2 (TS2) funken!")
print("-" * 60)

t_end     = time.time() + TIMEOUT
pkt_count = 0
rtp_count = 0
hstrp_count = 0

while time.time() < t_end:
    try:
        data, addr = sock.recvfrom(4096)
        pkt_count += 1
        ts = time.strftime("%H:%M:%S")
        if is_rtp(data):
            rtp_count += 1
            ptype = data[1] & 0x7F
            print(f"[{ts}] RTP  von {addr[0]}:{addr[1]}  {len(data)}B  PT={ptype}")
        elif is_hstrp(data):
            hstrp_count += 1
            ptype = data[3]
            print(f"[{ts}] HSTRP von {addr[0]}:{addr[1]}  {len(data)}B  Type=0x{ptype:02X}")
        else:
            print(f"[{ts}] UNBEKANNT von {addr[0]}:{addr[1]}  {len(data)}B  hex={data[:8].hex()}")
    except socket.timeout:
        rem = int(t_end - time.time())
        print(f"\r  ... warte ({rem}s verbleibend)  ", end="", flush=True)

print(f"\n\n[ERGEBNIS] {TIMEOUT}s gehoert:")
print(f"  Pakete gesamt : {pkt_count}")
print(f"  RTP-Pakete    : {rtp_count}  (Audio)")
print(f"  HSTRP-Pakete  : {hstrp_count} (Handshake/Heartbeat)")
if pkt_count == 0:
    print("\n  >>> KEINE Pakete empfangen!")
    print("  Moegliche Ursachen:")
    print("  1. NAI im CPS fuer TS2 nicht konfiguriert (wahrscheinlichste Ursache)")
    print("  2. Firewall blockiert UDP 30014")
    print("  3. Repeater sendet TS2 auf anderem Port")
    print("  4. Radio kommuniziert ohne Repeater (echter Direktbetrieb)")
    print()
    print("  Loesung: In Hytera CPS -> NAI-Konfiguration:")
    print("  'Audio Forward (TS2)' aktivieren, IP = diese PC-IP, Port = 30014")
elif rtp_count > 0:
    print(f"\n  >>> TS2-Audio kommt an! {rtp_count} RTP-Pakete")
    print("  Die Software sollte TS2 korrekt aufnehmen.")
    print("  Falls keine RAW-Datei erzeugt wird: Dispatcher neu starten.")

sock.close()
input("\nEnter druecken zum Beenden...")
