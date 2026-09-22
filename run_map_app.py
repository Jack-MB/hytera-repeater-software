#!/usr/bin/env python3
"""
Hytera Lageplan & Tactical Timeline - Hauptstarter & Orchestrator (AGENT 4: Systems Engineer)
Startet die Anwendung wahlweise als Standalone-Desktop-App (pywebview) unter Windows
oder als Headless-Server (--headless) für Linux / Leitstellen-Dienste.
"""

import os
import sys
import time
import signal
import socket
import urllib.request
import threading
import argparse
import logging

# ── 1. Robuste Pfad- und sys.path-Auflösung ─────────────────
# Egal aus welchem Arbeitsverzeichnis (CWD) dieses Skript gestartet wird:
# Wir ermitteln das Skript-Verzeichnis und fügen es dem Python-Pfad hinzu.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Prüfen, ob wir uns in hytera_project befinden und map_module im Parent liegt
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s"
)
logger = logging.getLogger("run_map_app")


def wait_for_server(host: str, port: int, timeout: float = 15.0) -> bool:
    """Wartet, bis der FastAPI HTTP-Server erreichbar ist."""
    start_time = time.time()
    url = f"http://{host}:{port}/api/health" if host != "0.0.0.0" else f"http://127.0.0.1:{port}/api/health"

    logger.info(f"Warte auf Initialisierung des Web-Servers unter {url}...")
    while time.time() - start_time < timeout:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HealthCheck"})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    logger.info("Web-Server ist online und antwortet mit HTTP 200 OK.")
                    return True
        except Exception:
            time.sleep(0.25)

    logger.warning("Timeout beim Warten auf den Web-Server erreicht!")
    return False


def run_desktop_app(host: str, port: int, title: str):
    """
    Startet FastAPI in einem Hintergrund-Thread und öffnet pywebview im Desktop-Fenster.
    """
    try:
        import webview
    except ImportError:
        logger.error("Modul 'pywebview' nicht gefunden! Bitte installieren mit: pip install pywebview")
        sys.exit(1)

    import uvicorn
    from map_module.main import app

    # Uvicorn-Server-Instanz für Thread-Steuerung
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
        access_log=False
    )
    server = uvicorn.Server(config)

    # Server im Hintergrund-Thread starten
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Warten, bis der Server hochgefahren ist
    if not wait_for_server(host, port, timeout=12.0):
        logger.error("Konnte Backend-Server nicht rechtzeitig erreichen.")

    url = f"http://127.0.0.1:{port}" if host in ("0.0.0.0", "127.0.0.1") else f"http://{host}:{port}"
    logger.info(f"Öffne pywebview Desktop-Fenster für: {url}")

    # pywebview-Fenster erstellen
    # Vollbild oder maximiertes taktisches Fenster
    window = webview.create_window(
        title=title,
        url=url,
        width=1400,
        height=900,
        min_size=(900, 600),
        resizable=True,
        fullscreen=False,
        frameless=False,
        easy_drag=True
    )

    # GUI Event-Loop starten (blockiert, bis das Fenster geschlossen wird)
    webview.start(debug=False)

    logger.info("pywebview-Fenster geschlossen. Fahre Hintergrunddienste herunter...")
    server.should_exit = True
    server_thread.join(timeout=3.0)
    logger.info("Desktop-Anwendung sauber beendet.")


def run_headless_server(host: str, port: int):
    """
    Startet FastAPI und Uvicorn direkt auf dem Hauptthread (für Linux-Server oder Headless-Dienste).
    """
    import uvicorn
    from map_module.main import app

    logger.info("=" * 60)
    logger.info(" Hytera HR1065 Tactical Map & Timeline - HEADLESS SERVER")
    logger.info(f" Lausch-Adresse: http://{host}:{port}")
    logger.info(" Drücken Sie STRG+C zum Beenden.")
    logger.info("=" * 60)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Hytera HR1065 Taktischer Lageplan & Timeline Starter"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Startet ausschließlich den FastAPI-Web- und UDP-Server ohne Desktop-GUI (empfohlen für Linux oder Server-Betrieb)."
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="HTTP Host-Adresse (Standard: 0.0.0.0 bei --headless, sonst 127.0.0.1)."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP Port für das Web-Interface und REST/WebSocket (Standard: 8000)."
    )
    parser.add_argument(
        "--udp-port",
        type=int,
        default=30003,
        help="UDP Port für Hytera HR1065 GPS-Daten (Standard: 30003)."
    )
    parser.add_argument(
        "--mock",
        dest="enable_mock",
        action="store_true",
        default=True,
        help="Aktiviert den realistischen Telemetrie- und PTT-Mock-Generator (Standard: aktiviert)."
    )
    parser.add_argument(
        "--no-mock",
        dest="enable_mock",
        action="store_false",
        help="Deaktiviert den Mock-Generator (nur echte UDP-Pakete vom Repeater verarbeiten)."
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Pfad zur SQLite-Datenbankdatei (Standard: tactical_map.db im Modulordner)."
    )
    parser.add_argument(
        "--title",
        type=str,
        default="Hytera HR1065 // Taktischer Lageplan & Tactical Timeline",
        help="Fenstertitel für pywebview."
    )

    args = parser.parse_args()

    # Umgebungsvariablen für das Backend setzen
    os.environ["HYTERA_MAP_UDP_PORT"] = str(args.udp_port)
    os.environ["HYTERA_MAP_ENABLE_MOCK"] = "1" if args.enable_mock else "0"
    if args.db:
        os.environ["HYTERA_MAP_DB_PATH"] = os.path.abspath(args.db)

    # Standard-Host ermitteln
    if args.host:
        host = args.host
    else:
        host = "0.0.0.0" if args.headless else "127.0.0.1"

    if args.headless:
        run_headless_server(host, args.port)
    else:
        run_desktop_app(host, args.port, args.title)


if __name__ == "__main__":
    main()
