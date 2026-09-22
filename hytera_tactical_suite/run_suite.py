#!/usr/bin/env python3
"""
Hytera Tactical Suite - Standalone Launcher
Startet das vollständige Taktik-Lagezentrum auf dem lokalen PC oder Server.
Öffnet automatisch den Browser und richtet alle Pfade relativ zu dieser Datei ein.
"""

import os
import sys
import argparse
import webbrowser
import threading
import time

# Eigene Verzeichnisse in sys.path aufnehmen (100% autark & portabel)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

import socket
import subprocess
import uvicorn
from config import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT, DEFAULT_UDP_PORT


def free_port(port: int, proto: str = "tcp"):
    """Prüft, ob ein Port noch von einer vorherigen Instanz belegt ist, und gibt ihn frei."""
    if sys.platform != "win32":
        return
    try:
        output = subprocess.check_output(f"netstat -ano -p {proto}", shell=True).decode(errors="ignore")
        for line in output.splitlines():
            line_upper = line.upper()
            if f":{port} " in line and (proto == "udp" or "LISTENING" in line_upper):
                parts = line.strip().split()
                if len(parts) >= 4:
                    pid_str = parts[-1]
                    if pid_str.isdigit():
                        pid = int(pid_str)
                        if pid != os.getpid() and pid > 0:
                            print(f"[INFO] Port {port}/{proto.upper()} ist noch von vorheriger Instanz (PID {pid}) belegt. Beende alten Prozess...")
                            subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
                            time.sleep(0.6)
    except Exception:
        pass


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Hytera Tactical Suite - Autarkes DMR-Lagezentrum & Incident-Tracking"
    )
    parser.add_argument("--host", default=DEFAULT_HTTP_HOST, help=f"Host-IP (Standard: {DEFAULT_HTTP_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT, help=f"HTTP-Port (Standard: {DEFAULT_HTTP_PORT})")
    parser.add_argument("--no-browser", action="store_true", help="Browser nicht automatisch öffnen")
    parser.add_argument("--mock", action="store_true", help="Mock-Telemetrie für Simulation/Test aktivieren")
    return parser.parse_args()


def open_browser_delayed(url: str, delay: float = 1.2):
    def _open():
        time.sleep(delay)
        print(f"\n[INFO] Oeffne Browser unter: {url}")
        webbrowser.open(url)
    threading.Thread(target=_open, daemon=True).start()


def main():
    args = parse_arguments()

    # Vor dem Start eventuelle verwaiste alte Instanzen auf Port 8000 & 30003 sauber beenden
    free_port(args.port, proto="tcp")
    free_port(DEFAULT_UDP_PORT, proto="udp")

    if args.mock:
        os.environ["HYTERA_MAP_ENABLE_MOCK"] = "1"
    else:
        os.environ["HYTERA_MAP_ENABLE_MOCK"] = "0"

    url_host = "localhost" if args.host in ("0.0.0.0", "127.0.0.1") else args.host
    app_url = f"http://{url_host}:{args.port}"

    # Netzwerk-IPs ermitteln (LAN & Tailscale)
    ts_ip = None
    lan_ip = None
    try:
        from tailscale_helper import get_tailscale_ip, get_local_lan_ip
        ts_ip = get_tailscale_ip()
        lan_ip = get_local_lan_ip()
    except Exception:
        pass

    print("=" * 70)
    print("      [HYTERA TACTICAL SUITE] LAGEZENTRUM & FUNKFUEHRUNG        ")
    print("=" * 70)
    print(f" Modul-Pfad:        {CURRENT_DIR}")
    print(f" Betriebsmodus:     {'DEMO-MOCK (5 Einheiten simuliert)' if args.mock else 'ECHTBETRIEB (Live DMR-Repeater UDP)'}")
    print()
    print(" [WEB-DASHBOARD & FERNZUGRIFF]")
    print(f"  * Lokal (Host-PC): http://localhost:{args.port}")
    if lan_ip:
        print(f"  * Netzwerk (LAN):  http://{lan_ip}:{args.port}  (Tablets / ELW)")
    if ts_ip:
        print(f"  * VPN (Tailscale): http://{ts_ip}:{args.port}  [ONLINE]")
    else:
        print(f"  * VPN (Tailscale): Nicht aktiv / getrennt")
    print(f"  * Einsatzbericht:  http://localhost:{args.port}/report")
    print(f"  * Live-Push (WS):  ws://{args.host}:{args.port}/ws/live")
    print()
    print(" [DMR-FUNK & PROTOKOLL-PORTS (HR1065)]")
    print(f"  * GPS / Telemetrie: UDP {DEFAULT_UDP_PORT}  (Standard HSTRP)")
    print(f"  * Notfall / Traps:  UDP 10162  (Repeater SNMP-Traps)")
    print(f"  * DMR-Dienste:      UDP 30001 - 30014  (RRS, CC, SMS, Audio RTP)")
    print(f"  * HSTRP-Status:     Aktiv - warte auf Datenpakete vom Repeater...")
    print()
    print(" [HARDWARE & INFRASTRUKTUR]")
    print(f"  * DMR-Repeater:     192.168.0.230 (Hytera HR1065)")
    print(f"  * Notstrom (USV):   192.168.0.232:502 (BlueWalker Modbus TCP)")
    print(f"  * Router / Switch:  192.168.0.1 (ZTE Speedbox / Omada ER605)")
    print("=" * 70)
    print("Beenden mit [Strg + C]\n")

    if not args.no_browser:
        open_browser_delayed(app_url)

    # Uvicorn starten
    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        log_level="info"
    )


if __name__ == "__main__":
    main()
