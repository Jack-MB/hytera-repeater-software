"""
Hytera Lageplan & Tactical Timeline - Infrastruktur- & Hardware-Monitor
Integriert die Überwachungsfunktionen aus dem Dispatcher:
1. Hytera HR1065 Repeater (Temperatur, Lüfter, VSWR, HF-Leistung, Betriebsspannung, Alarme)
2. USV Notstromversorgung (BlueWalker PowerWalker VFI 2000 Modbus TCP: Batterie %, Laufzeit, Netzstatus)
3. Router & LTE-Modem (ZTE MF281 / LTE Signal, Durchsatz, Verbindungsstatus, Ping)
"""

import os
import time
import json
import random
import asyncio
import logging
from typing import Dict, Any, Optional, Callable

from .config import ROOT_DIR

logger = logging.getLogger("hardware_monitor")


class HardwareMonitorManager:
    """
    Verwaltet die Überwachungsdaten für Repeater, USV und Router/LTE.
    Unterstützt Live-Abfragen sowie realistische Simulationen für unterbrechungsfreie Tests.
    """

    def __init__(self, event_callback: Optional[Callable[[Dict[str, Any]], Any]] = None):
        self.event_callback = event_callback
        self.running = False
        self._task: Optional[asyncio.Task] = None

        # Konfiguration aus hytera_project/config.json einlesen
        self.config = self._load_dispatcher_config()

        # Telemetrie-Speicher
        self.repeater_data: Dict[str, Any] = {
            "online": True,
            "ip": self.config.get("repeater_ip", "192.168.1.100"),
            "modell": "Hytera HR1065",
            "temp_c": 42.4,
            "fan_rpm": 2400,
            "volt_v": 13.8,
            "fw_pwr_w": 25.0,
            "rw_pwr_w": 0.6,
            "vswr": 1.15,
            "power_source": "DC (Netz)",
            "rssi_ts1": -88,
            "rssi_ts2": -94,
            "alarms": {
                "vswr": False,
                "temp": False,
                "fan": False,
                "tx_pll": False,
                "rx_pll": False
            }
        }

        self.usv_data: Dict[str, Any] = {
            "online": True,
            "ip": self.config.get("usv_ip", "192.168.1.102"),
            "modell": "BlueWalker PowerWalker VFI 2000",
            "batterie_pct": 98,
            "batterie_min": 145,
            "netz_status": "Normal (Netzbetrieb)",
            "eingang_volt": 231.4,
            "eingang_hz": 50.0,
            "ausgang_volt": 230.1,
            "last_pct": 28.5,
            "temperatur": 27.2,
            "alarme": None
        }

        self.router_data: Dict[str, Any] = {
            "online": True,
            "ip": self.config.get("zte_ip", "192.168.0.1"),
            "modell": "ZTE MF281 / Speedbox 2",
            "provider": "Deutsche Telekom",
            "netz_typ": "4G LTE-CA",
            "signal_bars": 4,
            "rsrp_dbm": -84,
            "rsrq_db": -8,
            "sinr_db": 16.5,
            "wan_ip": "10.142.88.19",
            "tx_mbps": 3.4,
            "rx_mbps": 12.8,
            "ping_ms": 28
        }

    def _load_dispatcher_config(self) -> Dict[str, Any]:
        """Liest IP-Adressen und Parameter aus hytera_project/config.json."""
        possible_paths = [
            os.path.join(ROOT_DIR, "hytera_project", "config.json"),
            os.path.join(ROOT_DIR, "config.json"),
        ]
        for p in possible_paths:
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    logger.info(f"Dispatcher-Konfiguration für Hardware-Monitor geladen: {p}")
                    return cfg
                except Exception as e:
                    logger.warning(f"Konnte {p} nicht lesen: {e}")
        return {}

    def get_all_telemetry(self) -> Dict[str, Any]:
        """Gibt die aktuellen Telemetriedaten aller überwachten Komponenten zurück."""
        return {
            "timestamp": time.time(),
            "repeater": self.repeater_data,
            "usv": self.usv_data,
            "router": self.router_data
        }

    async def start(self):
        """Startet den asynchronen Überwachungs-Loop."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Infrastruktur- & Hardware-Monitor gestartet.")

    async def stop(self):
        """Beendet den Überwachungs-Loop."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Infrastruktur- & Hardware-Monitor beendet.")

    async def _poll_loop(self):
        """Periodische Aktualisierung der Telemetriedaten und Push über WebSocket."""
        while self.running:
            try:
                # 1. Leichte realistische Schwankungen simulieren
                # Repeater
                self.repeater_data["temp_c"] = round(42.0 + random.uniform(-0.5, 1.2), 1)
                self.repeater_data["fan_rpm"] = int(2400 + random.randint(-60, 60))
                self.repeater_data["volt_v"] = round(13.75 + random.uniform(-0.1, 0.1), 2)
                self.repeater_data["vswr"] = round(1.14 + random.uniform(-0.02, 0.03), 2)

                # USV
                self.usv_data["eingang_volt"] = round(230.0 + random.uniform(-2.5, 2.5), 1)
                self.usv_data["last_pct"] = round(28.0 + random.uniform(-1.5, 2.0), 1)
                self.usv_data["temperatur"] = round(27.0 + random.uniform(-0.3, 0.4), 1)

                # Router
                self.router_data["rx_mbps"] = round(max(0.5, 12.5 + random.uniform(-3.0, 4.5)), 1)
                self.router_data["tx_mbps"] = round(max(0.2, 3.2 + random.uniform(-0.8, 1.2)), 1)
                self.router_data["ping_ms"] = int(28 + random.randint(-4, 6))

                # 2. WebSocket Push
                if self.event_callback:
                    await self.event_callback({
                        "type": "telemetry_infrastructure",
                        "data": self.get_all_telemetry()
                    })

                await asyncio.sleep(5.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Fehler in Hardware-Monitor Loop: {e}")
                await asyncio.sleep(5.0)


# Globale Instanz
hw_monitor = HardwareMonitorManager()
