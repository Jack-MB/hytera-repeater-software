"""
Hytera Command Center – Paket-Diagnose Collector
Erfasst alle eingehenden UDP-Pakete für den Diagnose-Tab.

Ring-Buffer mit max. 500 Paketen.
HSTRP-Header wird geparst und angezeigt.
RCP-Opcodes werden in Klartext aufgelöst.
Statistik pro Port (Pakete/s, gesamt).
"""

import asyncio
import logging
import time
from collections import deque
from typing import Dict, Any, Optional, Callable, Awaitable, List, Deque

try:
    from ..config import UDP_PORT_MAP, RCP_OPCODES
except ImportError:
    from config import UDP_PORT_MAP, RCP_OPCODES

logger = logging.getLogger("packet_dump")

EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]

MAX_BUFFER    = 500
STATS_WINDOW  = 10.0   # Sekunden für Paket/s-Berechnung


class PacketDumpCollector:
    """
    Sammelt und analysiert alle UDP-Pakete für Diagnose-Zwecke.
    Broadcasts neue Pakete via WebSocket an den Diagnose-Tab.
    """

    def __init__(self, broadcast_cb: EventCallback):
        self.broadcast_cb = broadcast_cb
        self._buffer: Deque[Dict[str, Any]] = deque(maxlen=MAX_BUFFER)
        self._stats: Dict[int, Dict[str, Any]] = {}   # port → stats
        self._enabled = False   # Wird erst aktiv wenn Diagnose-Tab geöffnet

        # Initialisiere Stats für alle bekannten Ports
        for port in UDP_PORT_MAP:
            self._stats[port] = {
                "port":     port,
                "service":  UDP_PORT_MAP[port],
                "total":    0,
                "window":   deque(maxlen=100),  # Timestamps der letzten N Pakete
                "pps":      0.0,
                "last_ts":  None,
            }

    def enable(self) -> None:
        """Aktiviert das Paket-Logging (wenn Diagnose-Tab geöffnet wird)."""
        self._enabled = True
        logger.info("Paket-Dump aktiviert.")

    def disable(self) -> None:
        self._enabled = False
        logger.info("Paket-Dump deaktiviert.")

    async def collect(self, event: Dict[str, Any]) -> None:
        """Empfängt ein Event vom HSTRP-Manager und speichert es."""
        port    = event.get("port", 0)
        opcode  = event.get("opcode", None)
        ev_type = event.get("type", "unknown")

        # Statistik immer aktualisieren (auch wenn Dump deaktiviert)
        if port in self._stats:
            stat = self._stats[port]
            stat["total"] += 1
            now = time.monotonic()
            stat["window"].append(now)
            stat["last_ts"] = now
            # Pakete/s = Anzahl Pakete im letzten STATS_WINDOW-Sekunden-Fenster
            cutoff = now - STATS_WINDOW
            recent = sum(1 for t in stat["window"] if t >= cutoff)
            stat["pps"] = round(recent / STATS_WINDOW, 2)

        # Paket-Details nur wenn Dump aktiv
        if not self._enabled:
            return

        # Opcode in Klartext auflösen
        opcode_label = ""
        if opcode is not None:
            opcode_label = RCP_OPCODES.get(opcode, f"0x{opcode:04X}")

        # Hex-Dump der Rohdaten
        raw = event.get("raw", b'') or event.get("data", b'')
        hex_dump = raw.hex() if isinstance(raw, (bytes, bytearray)) else ""

        # Paket-Eintrag
        entry = {
            "id":           id(event),
            "ts":           time.time(),
            "port":         port,
            "service":      UDP_PORT_MAP.get(port, f"PORT_{port}"),
            "type":         ev_type,
            "opcode":       f"0x{opcode:04X}" if opcode is not None else None,
            "opcode_label": opcode_label,
            "addr":         str(event.get("addr", "")),
            "payload_len":  len(event.get("payload", b'')),
            "hex_preview":  hex_dump[:64] + ("..." if len(hex_dump) > 64 else ""),
            "hex_full":     hex_dump[:512],   # Max 256 Byte für vollständigen Dump
        }

        self._buffer.appendleft(entry)

        # WebSocket-Broadcast (fire-and-forget)
        try:
            await self.broadcast_cb({
                "type":   "packet_dump",
                "packet": entry,
            })
        except Exception:
            pass

    def get_buffer(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Gibt die letzten N Pakete aus dem Buffer zurück."""
        return list(self._buffer)[:limit]

    def get_stats(self) -> List[Dict[str, Any]]:
        """Gibt Port-Statistiken zurück."""
        result = []
        for port, stat in self._stats.items():
            result.append({
                "port":     port,
                "service":  stat["service"],
                "total":    stat["total"],
                "pps":      stat["pps"],
                "last_ts":  stat["last_ts"],
                "active":   (time.monotonic() - stat["last_ts"]) < 5.0 if stat["last_ts"] else False,
            })
        return result

    def clear_buffer(self) -> None:
        """Leert den Paket-Buffer."""
        self._buffer.clear()
