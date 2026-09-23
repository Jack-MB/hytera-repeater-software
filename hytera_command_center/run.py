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


def ensure_ssl_certs():
    ssl_dir = os.path.join(ROOT_DIR, "ssl")
    os.makedirs(ssl_dir, exist_ok=True)
    cert_path = os.path.join(ssl_dir, "cert.pem")
    key_path = os.path.join(ssl_dir, "key.pem")
    if not (os.path.isfile(cert_path) and os.path.isfile(key_path)):
        try:
            import datetime
            import ipaddress
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa

            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, "Hytera Command Center"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Hytera Command Center"),
            ])
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
                .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
                .add_extension(
                    x509.SubjectAlternativeName([
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                        x509.IPAddress(ipaddress.IPv4Address("0.0.0.0")),
                    ]),
                    critical=False,
                )
                .sign(key, hashes.SHA256())
            )
            with open(key_path, "wb") as f:
                f.write(key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                ))
            with open(cert_path, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
            print("[INFO] Lokales SSL-Zertifikat automatisch in /ssl generiert.")
        except Exception as e:
            print(f"[WARNUNG] Konnte SSL-Zertifikat nicht generieren: {e}")
            return None, None
    return cert_path, key_path


def main():
    check_python_version()
    install_requirements()
    create_directories()

    try:
        import uvicorn
    except ImportError:
        print("[FEHLER] uvicorn nicht installiert. Führe aus: pip install uvicorn")
        sys.exit(1)

    use_ssl = "--ssl" in sys.argv or os.environ.get("HCC_SSL") in ("1", "true", "True")
    cert_path, key_path = (None, None)
    if use_ssl:
        cert_path, key_path = ensure_ssl_certs()
        if not (cert_path and key_path):
            use_ssl = False

    protocol = "https" if use_ssl else "http"
    default_port = 8443 if use_ssl else 8000
    host = os.environ.get("HCC_HOST", "0.0.0.0")
    port = int(os.environ.get("HCC_PORT", default_port))
    os.environ["HCC_PORT"] = str(port)
    os.environ["HCC_PROTOCOL"] = protocol

    import socket
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    print(f"\n" + "="*56)
    print(f"  Hytera Command Center ({protocol.upper()}-Modus)")
    print(f"  Lokal:  {protocol}://localhost:{port}")
    print(f"  LAN:    {protocol}://{local_ip}:{port}  (fuer Tablets / Funkraum)")
    print("="*56)
    if not use_ssl:
        print("  💡 TIPP FUER MIKROFON & BROWSER:")
        print(f"     Auf diesem Rechner rufen Sie bitte auf: http://localhost:{port}")
        print("     (Auf 'localhost' erlaubt der Browser das Mikrofon ohne Warnung!)")
    else:
        print("  🔒 HTTPS AKTIV:")
        print("     Im Browser einmalig 'Erweitert' -> 'Weiter zu...' bestaetigen.")
        print("     Danach ist das Mikrofon auf ALLEN Tablets/Geraeten im LAN freigegeben!")
    print("="*56 + "\n")

    # Uvicorn-Meldung überschreiben (0.0.0.0 durch echte klickbare Links ersetzen)
    try:
        import uvicorn.server
        _orig_log = uvicorn.server.Server._log_started_message

        def _patched_log_started_message(self, listeners):
            if self.config.host == "0.0.0.0":
                from uvicorn.server import logger
                logger.info(
                    f"Uvicorn bereit: {protocol}://localhost:{self.config.port} (LAN: {protocol}://{local_ip}:{self.config.port})"
                )
            else:
                _orig_log(self, listeners)

        uvicorn.server.Server._log_started_message = _patched_log_started_message
    except Exception:
        pass

    ssl_kwargs = {}
    if use_ssl and cert_path and key_path:
        ssl_kwargs = {
            "ssl_keyfile": key_path,
            "ssl_certfile": cert_path,
        }

    uvicorn.run(
        "backend.main:app",
        host         = host,
        port         = port,
        reload       = True,
        log_level    = "info",
        access_log   = True,
        workers      = 1,
        app_dir      = ROOT_DIR,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    main()
