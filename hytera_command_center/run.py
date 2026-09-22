"""
Hytera Command Center – Starterscript
Installiert Abhängigkeiten, prüft die Umgebung und startet den uvicorn-Server.
"""

import sys
import os
import subprocess
import logging

# Windows: UTF-8 Ausgabe erzwingen
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger("run")
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def check_python_version():
    if sys.version_info < (3, 10):
        print(f"[FEHLER] Python 3.10+ erforderlich (aktuell: {sys.version})")
        sys.exit(1)


def install_requirements():
    req_file = os.path.join(ROOT_DIR, "requirements.txt")
    if not os.path.isfile(req_file):
        print("[WARNUNG] requirements.txt nicht gefunden")
        return
    print("[INFO] Prüfe Abhängigkeiten...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", req_file, "--quiet"],
        capture_output=False,
    )
    if result.returncode != 0:
        print("[WARNUNG] pip install fehlgeschlagen – starte trotzdem")


def create_directories():
    dirs = [
        os.path.join(ROOT_DIR, "frontend", "static", "tiles_cache"),
        os.path.join(ROOT_DIR, "frontend", "static", "uploads"),
        os.path.join(ROOT_DIR, "frontend", "static", "recordings"),
        os.path.join(ROOT_DIR, "frontend", "static", "assets", "tactical_symbols"),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)


def main():
    check_python_version()
    install_requirements()
    create_directories()

    try:
        import uvicorn
    except ImportError:
        print("[FEHLER] uvicorn nicht installiert. Führe aus: pip install uvicorn")
        sys.exit(1)

    host = os.environ.get("HCC_HOST", "0.0.0.0")
    port = int(os.environ.get("HCC_PORT", "8000"))

    import socket
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    print(f"\n" + "="*50)
    print(f"  Hytera Command Center")
    print(f"  Lokal:  http://localhost:{port}")
    print(f"  LAN:    http://{local_ip}:{port}  (fuer Tablets / Funkraum)")
    print("="*50 + "\n")

    # Uvicorn-Meldung überschreiben (0.0.0.0 durch echte klickbare Links ersetzen)
    try:
        import uvicorn.server
        _orig_log = uvicorn.server.Server._log_started_message

        def _patched_log_started_message(self, listeners):
            if self.config.host == "0.0.0.0":
                from uvicorn.server import logger
                logger.info(
                    f"Uvicorn bereit: http://localhost:{self.config.port} (LAN: http://{local_ip}:{self.config.port})"
                )
            else:
                _orig_log(self, listeners)

        uvicorn.server.Server._log_started_message = _patched_log_started_message
    except Exception:
        pass

    uvicorn.run(
        "backend.main:app",
        host         = host,
        port         = port,
        reload       = False,
        log_level    = "info",
        access_log   = True,
        workers      = 1,
        app_dir      = ROOT_DIR,
    )


if __name__ == "__main__":
    main()
