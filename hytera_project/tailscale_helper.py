# -*- coding: utf-8 -*-
"""
tailscale_helper.py - Hilfsfunktionen fuer Tailscale VPN-Integration
Prueft ob Tailscale laeuft und gibt die 100.x.x.x IP zurueck.

Erkennungs-Reihenfolge:
  1. tailscale.exe ip -4   (sicherste Methode)
  2. ipconfig-Ausgabe parsen (Windows-Fallback)
  3. socket.getaddrinfo    (letzter Fallback)
"""
import subprocess, socket, re, os

# Tailscale-Netz ist immer 100.64.0.0/10
_TS_PREFIX = "100."

# Moegliche Pfade fuer tailscale.exe auf Windows
_TS_PATHS = [
    "tailscale",                                              # im PATH
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Tailscale\tailscale.exe"),
]


def _run_tailscale_cli() -> str | None:
    """Versucht tailscale ip -4 ueber alle bekannten Pfade."""
    for exe in _TS_PATHS:
        try:
            out = subprocess.check_output(
                [exe, "ip", "-4"],
                stderr=subprocess.DEVNULL,
                timeout=4,
                creationflags=subprocess.CREATE_NO_WINDOW  # kein CMD-Fenster
            ).decode("utf-8", errors="replace").strip()
            # Erste Zeile nehmen (manchmal mehrere IPs)
            first = out.splitlines()[0].strip() if out else ""
            if first.startswith(_TS_PREFIX):
                return first
        except (FileNotFoundError, PermissionError):
            continue   # naechsten Pfad probieren
        except Exception:
            continue
    return None


def _scan_ipconfig() -> str | None:
    """Parst ipconfig-Ausgabe nach 100.x.x.x Adressen (Windows-Fallback)."""
    try:
        out = subprocess.check_output(
            ["ipconfig"],
            stderr=subprocess.DEVNULL,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        ).decode("cp850", errors="replace")   # Windows-Codepage
        # Suche nach IPv4-Adressen die mit 100. beginnen
        matches = re.findall(r"IPv4[^:]*:\s*(100\.\d+\.\d+\.\d+)", out)
        for ip in matches:
            # Nur echtes Tailscale-Subnetz: 100.64.0.0/10
            parts = ip.split(".")
            if len(parts) == 4:
                second = int(parts[1])
                if 64 <= second <= 127:   # 100.64.x.x - 100.127.x.x
                    return ip
    except Exception:
        pass
    return None


def _scan_socket() -> str | None:
    """socket.getaddrinfo Fallback - funktioniert nicht auf allen Windows-Systemen."""
    try:
        ifaces = socket.getaddrinfo(socket.gethostname(), None)
        for af, *_, (ip, *_) in ifaces:
            if af == socket.AF_INET and ip.startswith(_TS_PREFIX):
                parts = ip.split(".")
                if len(parts) == 4:
                    second = int(parts[1])
                    if 64 <= second <= 127:
                        return ip
    except Exception:
        pass
    return None


def get_tailscale_ip() -> str | None:
    """
    Gibt die Tailscale-IP (100.x.x.x) des lokalen Rechners zurueck,
    oder None wenn Tailscale nicht laeuft / nicht installiert ist.
    """
    # Methode 1: tailscale.exe CLI (sicherste Methode)
    ip = _run_tailscale_cli()
    if ip:
        return ip

    # Methode 2: ipconfig-Ausgabe (Windows-Fallback)
    ip = _scan_ipconfig()
    if ip:
        return ip

    # Methode 3: socket.getaddrinfo (letzter Fallback)
    return _scan_socket()


def is_tailscale_running() -> bool:
    """True wenn Tailscale aktiv ist und eine 100.x.x.x IP hat."""
    return get_tailscale_ip() is not None


def get_remote_url(local_url: str) -> str | None:
    """
    Ersetzt die LAN-IP in local_url durch die Tailscale-IP,
    damit der URL von extern erreichbar ist.
    Beispiel: 'http://192.168.1.101:19080/' -> 'http://100.x.x.x:19080/'
    """
    if not local_url:
        return None
    ts_ip = get_tailscale_ip()
    if not ts_ip:
        return None
    # IP-Adresse im URL ersetzen (alles zwischen http:// und dem naechsten :/ oder /)
    return re.sub(r'(?<=http://)[^/:]+', ts_ip, local_url)


def install_hint() -> str:
    """Gibt einen Hinweis-Text zurueck wenn Tailscale nicht installiert ist."""
    return (
        "Tailscale nicht gefunden.\n"
        "Installieren: https://tailscale.com/download\n"
        "Danach: Kostenloser Account -> Einloggen -> Laptop und Handy verbinden.\n"
        "Kein Router-Eingriff noetig!"
    )
