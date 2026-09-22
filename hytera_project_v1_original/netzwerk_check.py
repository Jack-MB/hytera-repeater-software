"""
Hytera Netzwerk-Diagnose
Lauscht auf allen relevanten Hytera-Ports und zeigt jeden eingehenden Packet.
Strg+C zum Beenden.
"""
import socket
import threading
import time
from datetime import datetime

# Alle bekannten Hytera-Ports aus der CPS
PORTS = {
    30001: "Radio RRS     Slot1",
    30002: "Radio RRS     Slot2",
    30003: "Radio GNSS    Slot1",
    30004: "Radio GNSS    Slot2",
    30005: "Telemetry     Slot1",
    30006: "Telemetry     Slot2",
    30007: "Radio TMS     Slot1",
    30008: "Radio TMS     Slot2",
    30009: "Call Control  Slot1",
    30010: "Call Control  Slot2",
    30012: "** VOICE **   Slot1",  # <-- das wollen wir
    30014: "** VOICE **   Slot2",  # <-- das wollen wir
    30015: "Analog Call Control ",
    30016: "Analog Voice Service",
    3017:  "Self-Def Msg  Slot1",
    3018:  "Self-Def Msg  Slot2",
    50000: "IP Connect Networking",
    50001: "IP Connect Voice/Data",
}

packet_count = {}
lock = threading.Lock()

def listen(port, name):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        s.settimeout(1.0)
    except OSError as e:
        print(f"  [FEHLER] Port {port:5d} ({name}): {e}")
        return

    with lock:
        packet_count[port] = 0

    while True:
        try:
            data, addr = s.recvfrom(4096)
            now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            with lock:
                packet_count[port] += 1
                cnt = packet_count[port]
            print(
                f"  [{now}] Port {port:5d}  {name}  "
                f"| Von: {addr[0]}:{addr[1]}  "
                f"| {len(data):4d} Bytes  "
                f"| Pakete gesamt: {cnt}"
            )
        except socket.timeout:
            pass
        except Exception:
            break


def stats_printer():
    """Gibt alle 10s eine Zusammenfassung aus, wenn nichts ankommt."""
    last_total = 0
    while True:
        time.sleep(10)
        with lock:
            total = sum(packet_count.values())
        if total == last_total and total == 0:
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
                  f"... Noch keine Pakete empfangen. "
                  f"Repeater IP korrekt? Third-Party-Server-IP gesetzt?")
        last_total = total


if __name__ == "__main__":
    print("=" * 70)
    print("  Hytera Netzwerk-Diagnose")
    print("  Lausche auf allen Hytera-Ports...")
    print("  Drücke Strg+C zum Beenden.")
    print("=" * 70)
    print()

    threads = []
    for port, name in PORTS.items():
        t = threading.Thread(target=listen, args=(port, name), daemon=True)
        t.start()
        threads.append(t)

    # Stats-Thread
    threading.Thread(target=stats_printer, daemon=True).start()

    print(f"  {len(PORTS)} Ports geöffnet. Warte auf Pakete vom Repeater (192.168.1.100)...\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n  Diagnose beendet.")
        with lock:
            print("\n  Zusammenfassung:")
            for port, name in PORTS.items():
                cnt = packet_count.get(port, 0)
                if cnt > 0:
                    print(f"    Port {port:5d}  {name}: {cnt} Pakete")
            total = sum(packet_count.values())
            if total == 0:
                print("    Keine Pakete empfangen.")
            print()
