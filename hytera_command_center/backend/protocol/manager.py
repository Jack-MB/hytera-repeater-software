"""
Hytera Command Center – UDP Protocol Manager
Koordiniert alle Hytera NAI UDP-Listener (12 Ports).

Startet für jeden konfigurierten Port einen HSTRPProtocol-Listener.
Leitet Payloads an die spezialisierten Parser weiter:
  - RRS-Ports (30001/30002)  → RRSParser
  - GPS-Ports (30003/30004)  → GNSSParser
  - SMS-Ports (30007/30008)  → SMSParser
  - CC-Ports  (30009/30010)  → CallControlParser (TS1/TS2)
  - Audio-Ports (30012/30014)→ RTP/Audio-Callback
  - Alle Ports               → PacketDumpCollector (Diagnose)

Mock-Modus: Generiert realistische Einsatz-Telemetrie für 5 Einheiten.
"""

import asyncio
import logging
import math
import random
import socket
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Any, Optional, Awaitable, List

try:
    from .hstrp import HSTRPProtocol
    from .call_control import CallControlParser
    from .gnss import GNSSParser
    from .sms import SMSParser
    from .rrs import RRSParser
    from .packet_dump import PacketDumpCollector
    from ..config import UDP_PORT_MAP, DEFAULT_GPS_PORT
    from ..db_manager import db_manager
except ImportError:
    from hstrp import HSTRPProtocol
    from call_control import CallControlParser
    from gnss import GNSSParser
    from sms import SMSParser
    from rrs import RRSParser
    from packet_dump import PacketDumpCollector
    from config import UDP_PORT_MAP, DEFAULT_GPS_PORT
    from db_manager import db_manager

logger = logging.getLogger("udp_manager")

EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


def _get_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UDPManager:
    """
    Verwaltet alle UDP-Listener und verteilt Events an die richtigen Parser.
    """

    def __init__(
        self,
        event_callback: EventCallback,
        enable_mock: bool = False,
    ):
        self.event_callback  = event_callback
        self.enable_mock     = enable_mock
        self._transports: List[asyncio.DatagramTransport] = []
        self._protocols: Dict[int, HSTRPProtocol] = {}
        self._mock_task: Optional[asyncio.Task] = None
        self._running = False

        # Spezial-Parser instantiieren
        self._gnss_ts1 = GNSSParser(event_callback)
        self._gnss_ts2 = GNSSParser(event_callback)
        self._rrs_ts1  = RRSParser(event_callback)
        self._rrs_ts2  = RRSParser(event_callback)
        self._sms_ts1  = SMSParser(event_callback)
        self._sms_ts2  = SMSParser(event_callback)
        self._cc_ts1   = CallControlParser(event_callback, slot="TS1")
        self._cc_ts2   = CallControlParser(event_callback, slot="TS2")
        self._dump     = PacketDumpCollector(event_callback)

        # Port → Parser Mapping
        self._parsers: Dict[int, Any] = {
            30001: self._rrs_ts1,
            30002: self._rrs_ts2,
            30003: self._gnss_ts1,
            30004: self._gnss_ts2,
            30007: self._sms_ts1,
            30008: self._sms_ts2,
        }

    async def _on_hstrp_event(self, event: Dict[str, Any]) -> None:
        """Master-Callback – verteilt Events an die richtigen Parser."""
        port = event.get("port", 0)

        # Diagnose-Dump: immer
        await self._dump.collect(event)

        # Spezialisierter Parser für Port
        parser = self._parsers.get(port)
        if parser:
            await parser.handle_hstrp_event(event)
            return

        # CC-Ports 30009/30010
        if port == 30009:
            opcode  = event.get("opcode", 0)
            payload = event.get("payload", b'')
            addr    = event.get("addr", ("", 0))
            await self._cc_ts1.process(opcode, payload, addr)
            return
        if port == 30010:
            opcode  = event.get("opcode", 0)
            payload = event.get("payload", b'')
            addr    = event.get("addr", ("", 0))
            await self._cc_ts2.process(opcode, payload, addr)
            return

        # RTP/Audio auf Port 30012/30014 → vollständige Rohdaten für Recorder
        if event.get("type") == "rtp":
            raw_bytes = event.get("payload", b"")
            await self.event_callback({
                "type":    "audio_rtp",
                "port":    port,
                "raw_hex": raw_bytes.hex(),           # Vollständiges RTP-Paket (inkl. Header)
                "data":    raw_bytes.hex()[:64],      # Kurzvorschau für Diagnose
                "radio_id": event.get("radio_id", 0), # Falls aus HSTRP bekannt
            })
            return


        # Raw/Unbekannte Pakete → Diagnose-Tab
        if event.get("type") == "raw_packet":
            await self.event_callback({
                "type": "raw_packet",
                "port": port,
                "hex":  event.get("hex", ""),
                "addr": str(event.get("addr", "")),
            })

    async def start(self) -> None:
        """Startet alle UDP-Listener."""
        if self._running:
            return
        self._running = True

        loop = asyncio.get_running_loop()
        started_ports = []

        for port, service_name in UDP_PORT_MAP.items():
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                if hasattr(socket, "SO_REUSEADDR"):
                    try:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    except OSError:
                        pass
                # 1 MB Empfangspuffer für hohe Sprach- und Telemetrie-Spitzen
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
                except OSError:
                    pass
                sock.setblocking(False)
                sock.bind(("0.0.0.0", port))

                transport, protocol = await loop.create_datagram_endpoint(
                    lambda p=port: HSTRPProtocol(
                        port=p,
                        on_payload_cb=self._on_hstrp_event,
                        on_rtp_cb=self._on_hstrp_event,
                    ),
                    sock=sock,
                )
                self._transports.append(transport)
                self._protocols[port] = protocol
                started_ports.append(f"{port}({service_name})")
            except OSError as exc:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
                logger.warning(f"UDP Port {port} ({service_name}): {exc}")

        logger.info(f"UDP-Manager gestartet. Ports: {', '.join(started_ports)}")

        if self.enable_mock:
            self._mock_task = asyncio.create_task(
                self._run_mock(), name="mock_telemetry"
            )
            logger.info("Mock-Modus aktiv: 5 simulierte Einheiten.")

    async def stop(self) -> None:
        """Stoppt alle Listener sauber."""
        self._running = False
        if self._mock_task and not self._mock_task.done():
            self._mock_task.cancel()
            try:
                await self._mock_task
            except asyncio.CancelledError:
                pass
        for transport in self._transports:
            transport.close()
        self._transports.clear()
        self._protocols.clear()

        # CallControl Watchdogs stoppen
        if hasattr(self._cc_ts1, "stop"):
            self._cc_ts1.stop()
        if hasattr(self._cc_ts2, "stop"):
            self._cc_ts2.stop()

        logger.info("UDP-Manager gestoppt.")

    def is_mock_active(self) -> bool:
        return self.enable_mock and bool(self._mock_task and not self._mock_task.done())

    def get_port_stats(self) -> List[Dict[str, Any]]:
        """Gibt Statistiken aller aktiven Ports zurück."""
        stats = []
        for port, proto in self._protocols.items():
            s = proto.get_stats()
            s["service"] = UDP_PORT_MAP.get(port, "unknown")
            stats.append(s)
        return stats

    # ── Mock-Telemetrie ───────────────────────────────────────────────────

    async def _run_mock(self) -> None:
        """
        Simuliert realistische Einsatz-Telemetrie für 5 DMR-Einheiten.
        Generiert GPS-Updates, PTT-Events, gelegentliche Notrufe und RRS-Events.
        """
        # 5 Einheiten definieren
        units = [
            {"id": 10001, "alias": "ELW 1",   "lat": 51.2325, "lon": 6.7800},
            {"id": 10002, "alias": "HLF 1",   "lat": 51.2330, "lon": 6.7810},
            {"id": 10003, "alias": "RTW 1",   "lat": 51.2320, "lon": 6.7790},
            {"id": 10004, "alias": "ELW 2",   "lat": 51.2340, "lon": 6.7820},
            {"id": 10005, "alias": "HLF 2",   "lat": 51.2315, "lon": 6.7795},
        ]

        # Einheiten in DB anlegen
        for u in units:
            await db_manager.upsert_radio(u["id"], u["alias"], "Mock-Gerät")
            await db_manager.set_radio_online(u["id"], True)

        iteration = 0
        try:
            while True:
                iteration += 1
                ts = _get_iso()

                for u in units:
                    # GPS-Position leicht verschieben
                    dlat = (random.random() - 0.5) * 0.0003
                    dlon = (random.random() - 0.5) * 0.0003
                    u["lat"] = round(u["lat"] + dlat, 7)
                    u["lon"] = round(u["lon"] + dlon, 7)
                    speed   = round(random.uniform(0, 25), 1)
                    heading = round(random.uniform(0, 359), 1)
                    rssi    = round(random.uniform(-100, -60), 1)

                    await self.event_callback({
                        "type":      "gps",
                        "radio_id":  u["id"],
                        "lat":       u["lat"],
                        "lon":       u["lon"],
                        "speed":     speed,
                        "heading":   heading,
                        "rssi":      rssi,
                        "gps_fix":   True,
                        "timestamp": ts,
                    })

                # Gelegentliche PTT-Events (jede 3. Iteration)
                if iteration % 3 == 0:
                    caller = random.choice(units)
                    slot   = random.choice(["TS1", "TS2"])
                    await self.event_callback({
                        "type":        "ptt_start",
                        "radio_id":    caller["id"],
                        "slot":        slot,
                        "call_type":   "Gruppe",
                        "is_emergency": False,
                        "rssi":        round(random.uniform(-95, -65), 1),
                        "timestamp":   ts,
                    })
                    await asyncio.sleep(random.uniform(1.5, 4.0))
                    await self.event_callback({
                        "type":        "ptt_end",
                        "radio_id":    caller["id"],
                        "slot":        slot,
                        "call_type":   "Gruppe",
                        "is_emergency": False,
                        "rssi":        round(random.uniform(-95, -65), 1),
                        "duration_ms": random.randint(1500, 8000),
                        "timestamp":   _get_iso(),
                    })

                # Sehr selten: Notruf (jede 20. Iteration, Zufalls-Unit)
                if iteration % 20 == 0:
                    emergency_unit = random.choice(units)
                    logger.info(f"Mock-Notruf: {emergency_unit['alias']} ({emergency_unit['id']})")
                    await self.event_callback({
                        "type":      "emergency",
                        "radio_id":  emergency_unit["id"],
                        "slot":      "TS1",
                        "call_type": "Notruf",
                        "timestamp": ts,
                    })

                await asyncio.sleep(3.0)

        except asyncio.CancelledError:
            logger.debug("Mock-Task beendet.")
