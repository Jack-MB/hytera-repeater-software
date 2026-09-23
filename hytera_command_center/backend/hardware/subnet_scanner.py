"""
Hytera Command Center – Subnetz-Autodiscovery
Scannt das lokale IPv4-Subnetz asynchron nach:
  - Hytera DMR-Repeater (SNMP UDP Port 161)
  - PowerWalker USV (Modbus TCP Port 502)
  - LTE-Gateway / Switch / Router (HTTP TCP Port 80, 8043)
"""

import asyncio
from dataclasses import dataclass, field
import logging
import socket
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("subnet_scanner")


@dataclass
class DiscoveredDevice:
    ip: str
    open_ports: List[int] = field(default_factory=list)
    type: str = "unknown"
    description: str = ""
    device_name: str = ""
    port: int = 0
    confidence: str = "medium"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ip": self.ip,
            "open_ports": self.open_ports,
            "type": self.type,
            "description": self.description or self.device_name,
            "device_name": self.device_name or self.description,
            "port": self.port or (self.open_ports[0] if self.open_ports else 0),
            "confidence": self.confidence,
        }


# Hytera SNMP Get Packet für sysDescr (1.3.6.1.2.1.1.1.0)
SNMP_PROBE_PACKET = bytes.fromhex(
    "302902010004067075626c6963a01c020412345678020100020100300e300c06082b060102010101000500"
)


def get_local_ip_and_subnet() -> Tuple[str, str]:
    """Ermittelt die primäre aktive lokale IP und das Standard /24-Subnetzpräfix."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "127.0.0.1"
    finally:
        s.close()

    parts = local_ip.split(".")
    if len(parts) == 4 and parts[0] != "127":
        subnet_prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
    else:
        subnet_prefix = "192.168.0."
    return local_ip, subnet_prefix


async def _probe_tcp_port(ip: str, port: int, timeout_s: float = 0.35) -> bool:
    """Prüft, ob ein TCP-Port offen ist."""
    try:
        conn = asyncio.open_connection(ip, port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout_s)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


def _probe_snmp_udp(ip: str, port: int = 161, timeout_s: float = 0.4) -> bool:
    """Sendet ein SNMP-Probe-Paket und wartet synchron mit kurzem Timeout auf Antwort."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_s)
    try:
        sock.sendto(SNMP_PROBE_PACKET, (ip, port))
        resp, _ = sock.recvfrom(2048)
        return len(resp) > 0
    except Exception:
        return False
    finally:
        sock.close()


async def scan_subnet(
    subnet_prefix: Optional[str] = None,
    timeout_s: float = 0.35,
    max_concurrency: int = 60,
) -> Dict[str, Any]:
    """
    Scannt asynchron alle 254 Adressen eines /24 Subnetzes.
    Gibt gefundene Hardware mit Typ und vorgeschlagener Konfiguration zurück.
    """
    local_ip, auto_prefix = get_local_ip_and_subnet()
    prefix = (subnet_prefix.strip() if subnet_prefix else auto_prefix)
    if not prefix.endswith("."):
        prefix += "."

    sem = asyncio.Semaphore(max_concurrency)
    loop = asyncio.get_running_loop()
    found_devices: List[Dict[str, Any]] = []

    async def scan_single_ip(host_num: int):
        target_ip = f"{prefix}{host_num}"
        async with sem:
            # 1. SNMP UDP Port 161 (Repeater)
            is_snmp = await loop.run_in_executor(None, _probe_snmp_udp, target_ip, 161, timeout_s)
            if is_snmp:
                found_devices.append({
                    "ip": target_ip,
                    "type": "repeater",
                    "device_name": "Hytera DMR-Repeater",
                    "port": 161,
                    "confidence": "high",
                })
                return

            # 2. Modbus TCP Port 502 (USV)
            is_modbus = await _probe_tcp_port(target_ip, 502, timeout_s)
            if is_modbus:
                found_devices.append({
                    "ip": target_ip,
                    "type": "usv",
                    "device_name": "PowerWalker USV (Modbus TCP)",
                    "port": 502,
                    "confidence": "high",
                })
                return

            # 3. Router / Gateway Ports 80 / 8043 (ZTE / Omada)
            is_http = await _probe_tcp_port(target_ip, 80, timeout_s)
            if is_http:
                is_omada_ctl = await _probe_tcp_port(target_ip, 8043, timeout_s)
                dev_type = "omada" if is_omada_ctl else "zte"
                name = "TP-Link Omada Controller" if is_omada_ctl else "ZTE Router / Gateway"
                found_devices.append({
                    "ip": target_ip,
                    "type": dev_type,
                    "device_name": name,
                    "port": 8043 if is_omada_ctl else 80,
                    "confidence": "medium",
                })

    tasks = [scan_single_ip(h) for h in range(1, 255)]
    await asyncio.gather(*tasks, return_exceptions=True)

    return {
        "status": "ok",
        "subnet_scanned": f"{prefix}0/24",
        "local_ip": local_ip,
        "count": len(found_devices),
        "devices": found_devices,
        "results": found_devices,
    }


class SubnetScanner:
    """Kompatibilitäts-Wrapper für Subnetz-Scans."""

    @staticmethod
    async def scan_subnet(
        subnet_prefix: Optional[str] = None,
        timeout_s: float = 0.35,
        max_concurrency: int = 60
    ) -> Tuple[List[DiscoveredDevice], float]:
        import time
        t0 = time.time()
        res = await scan_subnet(subnet_prefix, timeout_s, max_concurrency)
        elapsed = round(time.time() - t0, 2)
        devices = []
        for d in res.get("devices", []):
            devices.append(DiscoveredDevice(
                ip=d["ip"],
                type=d.get("type", "unknown"),
                description=d.get("device_name", ""),
                device_name=d.get("device_name", ""),
                port=d.get("port", 0),
                open_ports=[d.get("port", 0)] if d.get("port") else []
            ))
        return devices, elapsed

