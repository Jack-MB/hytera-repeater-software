# -*- coding: utf-8 -*-
"""
Hytera Tactical Suite - Tailscale VPN Helper
Ermittelt automatisch den Status und die 100.x.x.x IPv4-Adresse von Tailscale.
Ermöglicht den direkten Mehrbenutzer-Fernzugriff über Mobilfunk/LTE und Doppel-NAT.
"""

import subprocess
import socket
import re
import os
from typing import Optional, Dict, Any

# Tailscale-Netz ist immer 100.64.0.0/10 (100.64.x.x bis 100.127.x.x)
_TS_PREFIX = "100."

# Mögliche Pfade für tailscale.exe auf Windows
_TS_PATHS = [
    "tailscale",
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Tailscale\tailscale.exe"),
]


def _run_tailscale_cli() -> Optional[str]:
    """Versucht tailscale ip -4 über alle bekannten Pfade."""
    for exe in _TS_PATHS:
        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW
            out = subprocess.check_output(
                [exe, "ip", "-4"],
                stderr=subprocess.DEVNULL,
                timeout=3,
                creationflags=creationflags
            ).decode("utf-8", errors="replace").strip()
            first = out.splitlines()[0].strip() if out else ""
            if first.startswith(_TS_PREFIX):
                return first
        except (FileNotFoundError, PermissionError):
            continue
        except Exception:
            continue
    return None


def _scan_ipconfig() -> Optional[str]:
    """Parst Windows ipconfig-Ausgabe nach 100.x.x.x Adressen (Windows-Fallback)."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.check_output(
            ["ipconfig"],
            stderr=subprocess.DEVNULL,
            timeout=4,
            creationflags=subprocess.CREATE_NO_WINDOW
        ).decode("cp850", errors="replace")
        matches = re.findall(r"IPv4[^:]*:\s*(100\.\d+\.\d+\.\d+)", out)
        for ip in matches:
            parts = ip.split(".")
            if len(parts) == 4:
                second = int(parts[1])
                if 64 <= second <= 127:
                    return ip
    except Exception:
        pass
    return None


def _scan_socket() -> Optional[str]:
    """socket.getaddrinfo Fallback für Tailscale-Schnittstellen."""
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


def get_tailscale_ip() -> Optional[str]:
    """
    Gibt die Tailscale-IPv4 (100.x.x.x) des lokalen Rechners zurück
    oder None, wenn Tailscale nicht aktiv/installiert ist.
    """
    # 1. CLI (zuverlässigster Weg)
    ip = _run_tailscale_cli()
    if ip:
        return ip

    # 2. Windows ipconfig Adapter-Scan
    ip = _scan_ipconfig()
    if ip:
        return ip

    # 3. Socket-Scan
    ip = _scan_socket()
    if ip:
        return ip

    return None


def get_tailscale_status(http_port: int = 8000) -> Dict[str, Any]:
    """
    Liefert ein vollständiges Status-Dictionary für API und Frontend.
    """
    ip = get_tailscale_ip()
    active = bool(ip)
    url = f"http://{ip}:{http_port}" if active else None

    return {
        "active": active,
        "ip": ip or "--",
        "url": url or "--",
        "port": http_port,
        "service": "Tailscale WireGuard Mesh-VPN",
        "subnet_route_cmd": "tailscale up --advertise-routes=192.168.0.0/24",
        "hint": "Ermöglicht sicheren Fernzugriff ohne Portweiterleitung über Mobilfunk (ZTE) und Omada-Router."
    }


def get_local_lan_ip() -> Optional[str]:
    """
    Ermittelt die primäre lokale LAN-IPv4-Adresse des Rechners (z. B. 192.168.x.x),
    über die andere Geräte im Netzwerk (ELW, Tablets) zugreifen können.
    """
    # 1. Verbindungssockel zu externer Adresse
    for target in [("8.8.8.8", 80), ("192.168.0.1", 80), ("192.168.1.1", 80)]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(target)
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127.") and not ip.startswith("100."):
                return ip
        except Exception:
            continue

    # 2. Hostname-Scan
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip and not ip.startswith("127.") and not ip.startswith("100."):
                return ip
    except Exception:
        pass

    return None

