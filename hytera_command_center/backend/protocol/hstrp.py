"""
Hytera Command Center – HSTRP Basis-Protokoll (asyncio)
Hytera Site Trunking Repeater Protocol

Paket-Format (12 Byte Header):
  Offset 0–2:  Signature  b'\\x32\\x42\\x00'
  Offset 3:    Type       (SYN=0x24, SYN-ACK=0x05, HEARTBEAT=0x02,
                            ACK=0x01, FROM_RADIO=0x20, TO_RADIO=0x00)
  Offset 4–5:  Reserved   0x0000
  Offset 6–7:  Sequence   uint16 LE (bei ACK: Seq des zu quittierenden Pakets)
  Offset 8–9:  Length     uint16 LE (Payload-Länge nach Header)
  Offset 10–11:Opcode     uint16 LE (RCP-Opcode – nur bei FROM_RADIO/TO_RADIO)

Verbindungsaufbau:
  Repeater →  PC: SYN     (Typ 0x24)
  PC       → Repeater: SYN-ACK (Typ 0x05)
  Repeater ↔  PC: HEARTBEAT (Typ 0x02) alle ~30s → PC antwortet mit HEARTBEAT
  Repeater →  PC: FROM_RADIO (Typ 0x20) → PC antwortet mit ACK (Typ 0x01)
"""

import asyncio
import logging
import struct
import time
from typing import Callable, Dict, Any, Optional, Awaitable

try:
    from .config import HSTRP_SIGNATURE, HSTRP_SYN, HSTRP_SYNACK, HSTRP_HEARTBEAT, HSTRP_ACK, HSTRP_FROM_RADIO
except ImportError:
    try:
        from backend.config import HSTRP_SIGNATURE, HSTRP_SYN, HSTRP_SYNACK, HSTRP_HEARTBEAT, HSTRP_ACK, HSTRP_FROM_RADIO
    except ImportError:
        from config import HSTRP_SIGNATURE, HSTRP_SYN, HSTRP_SYNACK, HSTRP_HEARTBEAT, HSTRP_ACK, HSTRP_FROM_RADIO

logger = logging.getLogger("hstrp")

HSTRP_HEADER_SIZE = 12
HSTRP_SIG = b'\x32\x42\x00'

EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


def build_hstrp_response(pkt_type: int, seq: int = 0, payload: bytes = b'') -> bytes:
    """
    Baut ein HSTRP-Antwort-Paket.
    Header: Signatur (3B) + Typ (1B) + Reserved (2B) + Seq (2B LE) + Len (2B LE) + Opcode (2B LE)
    """
    opcode = 0x0000
    length = len(payload)
    header = struct.pack(
        '<3sBHHH',
        HSTRP_SIG,       # 3 Byte Signatur
        pkt_type,        # 1 Byte Typ
        0x0000,          # 2 Byte Reserved
        seq & 0xFFFF,    # 2 Byte Sequenz
        length,          # 2 Byte Payload-Länge
    ) + struct.pack('<H', opcode)  # 2 Byte Opcode
    return header + payload


def build_synack(syn_seq: int = 0) -> bytes:
    """Baut ein SYN-ACK-Paket als Antwort auf einen SYN-Request."""
    return build_hstrp_response(HSTRP_SYNACK, seq=syn_seq)


def build_heartbeat() -> bytes:
    """Baut ein HEARTBEAT-Antwort-Paket."""
    return build_hstrp_response(HSTRP_HEARTBEAT, seq=0)


def build_ack(seq: int) -> bytes:
    """Baut ein ACK-Paket für ein empfangenes FROM_RADIO-Paket."""
    return build_hstrp_response(HSTRP_ACK, seq=seq)


def parse_hstrp_header(data: bytes) -> Optional[Dict[str, Any]]:
    """
    Parst den HSTRP-Header (12 Byte).
    Gibt None zurück wenn das Paket kein gültiges HSTRP-Paket ist.
    """
    if len(data) < HSTRP_HEADER_SIZE:
        return None
    if data[:3] != HSTRP_SIG:
        return None
    try:
        pkt_type = data[3]
        reserved = struct.unpack_from('<H', data, 4)[0]
        seq      = struct.unpack_from('<H', data, 6)[0]
        length   = struct.unpack_from('<H', data, 8)[0]
        opcode   = struct.unpack_from('<H', data, 10)[0]
        actual_payload_len = max(0, len(data) - HSTRP_HEADER_SIZE)
        if actual_payload_len < length:
            logger.debug(
                f"HSTRP Paket unvollständig: {length} Bytes deklariert, aber nur {actual_payload_len} Bytes empfangen"
            )
        payload  = data[HSTRP_HEADER_SIZE:HSTRP_HEADER_SIZE + length]
        return {
            "type":    pkt_type,
            "seq":     seq,
            "length":  length,
            "opcode":  opcode,
            "payload": payload,
            "raw":     data,
        }
    except struct.error:
        return None


class HSTRPProtocol(asyncio.DatagramProtocol):
    """
    Asyncio DatagramProtocol-Implementierung für das HSTRP-Protokoll.
    Verarbeitet SYN, HEARTBEAT, FROM_RADIO und sendet automatisch
    SYN-ACK, HEARTBEAT-Antworten und ACKs zurück an den Repeater.
    Reicht Payload-Daten an einen konfigurierbaren Callback weiter.
    """

    def __init__(
        self,
        port: int,
        on_payload_cb: EventCallback,
        on_rtp_cb: Optional[EventCallback] = None,
    ):
        self.port         = port
        self.on_payload_cb = on_payload_cb
        self.on_rtp_cb    = on_rtp_cb
        self.transport: Optional[asyncio.DatagramTransport] = None
        self._repeater_addr: Optional[tuple] = None
        self._last_activity  = time.monotonic()
        self._pkt_count      = 0
        self._ack_count      = 0
        self._syn_count      = 0

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport
        logger.info(f"HSTRP UDP-Listener auf Port {self.port} geöffnet.")

    def _send(self, data: bytes, addr: tuple) -> None:
        """Sendet ein Paket an die angegebene Adresse zurück."""
        if self.transport and not self.transport.is_closing():
            try:
                self.transport.sendto(data, addr)
            except Exception as exc:
                logger.debug(f"HSTRP Sende-Fehler Port {self.port}: {exc}")

    def _dispatch(self, coro) -> None:
        """Startet einen asynchronen Task fehlertolerant mit Exception-Logging."""
        task = asyncio.create_task(coro)
        task.add_done_callback(self._on_task_done)

    @staticmethod
    def _on_task_done(task: asyncio.Task) -> None:
        try:
            if not task.cancelled() and task.exception():
                logger.error(f"HSTRP Task-Ausführungsfehler: {task.exception()}")
        except asyncio.CancelledError:
            pass

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self._last_activity = time.monotonic()
        self._pkt_count += 1

        # RTP-Paket-Erkennung: V=2 (Bits 7-6 = 10) vor HSTRP prüfen
        if len(data) >= 12 and (data[0] >> 6) == 2:
            if self.on_rtp_cb:
                self._dispatch(self.on_rtp_cb({
                    "type":    "rtp",
                    "port":    self.port,
                    "addr":    addr,
                    "payload": data,
                }))
            return

        # HSTRP-Header parsen
        hdr = parse_hstrp_header(data)
        if hdr is None:
            # Kein HSTRP-Paket – als rohen Dump weiterleiten
            self._dispatch(self.on_payload_cb({
                "type":    "raw_packet",
                "port":    self.port,
                "addr":    addr,
                "data":    data,
                "hex":     data.hex(),
            }))
            return

        pkt_type = hdr["type"]
        seq      = hdr["seq"]

        # SYN → SYN-ACK
        if pkt_type == HSTRP_SYN:
            self._syn_count += 1
            self._repeater_addr = addr
            logger.info(f"HSTRP SYN von {addr} (Port {self.port}) → SYN-ACK gesendet")
            self._send(build_synack(seq), addr)
            return

        # HEARTBEAT → HEARTBEAT-Antwort
        if pkt_type == HSTRP_HEARTBEAT:
            self._send(build_heartbeat(), addr)
            return

        # FROM_RADIO: Nutzdaten → ACK senden + an Callback weiterleiten
        if pkt_type == HSTRP_FROM_RADIO:
            self._ack_count += 1
            self._repeater_addr = addr
            self._send(build_ack(seq), addr)

            if hdr["payload"] or hdr["opcode"]:
                self._dispatch(self.on_payload_cb({
                    "type":    "hstrp_payload",
                    "port":    self.port,
                    "addr":    addr,
                    "opcode":  hdr["opcode"],
                    "seq":     seq,
                    "payload": hdr["payload"],
                    "raw":     data,
                }))
            return

        # Alle anderen Paket-Typen (TO_RADIO-Bestätigungen etc.)
        logger.debug(f"HSTRP Typ 0x{pkt_type:02X} von {addr} Port {self.port} – ignoriert")

    def error_received(self, exc: Exception) -> None:
        logger.error(f"HSTRP UDP-Fehler Port {self.port}: {exc}")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            logger.warning(f"HSTRP Verbindung verloren Port {self.port}: {exc}")
        else:
            logger.debug(f"HSTRP Port {self.port} geschlossen.")

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Statistiken für den Diagnose-Tab zurück."""
        idle_s = time.monotonic() - self._last_activity
        return {
            "port":          self.port,
            "packets_total": self._pkt_count,
            "acks_sent":     self._ack_count,
            "syn_received":  self._syn_count,
            "last_activity_s": round(idle_s, 1),
            "repeater_addr": str(self._repeater_addr) if self._repeater_addr else None,
        }
