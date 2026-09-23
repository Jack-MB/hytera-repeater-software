"""
Hytera Command Center – IP TX Transmitter Service (asyncio)
Ermöglicht das Senden von Sprache und PTT-Steuerung über die
Hytera NAI / Ethernet-Schnittstelle (HR1065 / RD985 Repeater).

Unterstützt:
  - RCP Call Setup (Opcode 0x0841) auf Port 30009 (TS1) / 30010 (TS2)
  - RCP Button Request (Opcode 0x0041, FRONT_PTT Press / Release)
  - RTP Voice Streaming (G.711 µ-law, 8 kHz, 160 B / 20ms) auf Port 30012 (TS1) / 30014 (TS2)
  - Audio-Resampling beliebiger Quell-Abtastraten (z. B. 44.1 kHz, 48 kHz Smartphone-Audio)
  - Automatischer Time-Out-Timer (TOT) Schutz gegen Dauersenden
"""

import asyncio
import logging
import math
import struct
import time
from typing import Optional, Dict, Any, Callable, Awaitable

import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    try:
        import audioop
    except ImportError:
        audioop = None

logger = logging.getLogger("tx_sender")

# ── Protokoll-Konstanten ───────────────────────────────────────────────────────
HSTRP_SIG = b'\x32\x42\x00'
HSTRP_TO_RADIO = 0x00
HSTRP_ACK = 0x01
HSTRP_HEARTBEAT = 0x02
HSTRP_SYNACK = 0x05

RCP_MSG_HDR = 0x02            # MessageHeader.RCP
RCP_OPCODE_CALL_SETUP = 0x0841# Call Setup (dest_id + call_type)
RCP_OPCODE_BUTTON = 0x0041    # Button Request (PTT)

BUTTON_TARGET_FRONT_PTT = 0x03
BUTTON_TARGET_BACK_PTT  = 0x1E
BUTTON_OP_RELEASE       = 0x00
BUTTON_OP_PRESS         = 0x01

CALL_TYPE_PRIVATE = 0
CALL_TYPE_GROUP   = 1
CALL_TYPE_ALL     = 2

DEFAULT_REPEATER_IP = "192.168.0.230"
DEFAULT_DISPATCHER_ID = 100

RTP_SAMPLE_RATE = 8000
RTP_FRAME_SAMPLES = 160       # 20ms @ 8000 Hz
RTP_FRAME_DURATION_S = 0.020  # 20 Millisekunden


# ── Hilfsfunktionen für Checksummen und Codierung ──────────────────────────────
def calculate_rcp_checksum(header_1_to_5: bytes, payload: bytes) -> int:
    """
    Berechnet die Hytera-spezifische Checksumme für RCP-Nachrichten:
    csum = (~(sum(header[1:5]) + sum(payload)) + 0x33) & 0xFF
    """
    total = sum(header_1_to_5) + sum(payload)
    return (~total + 0x33) & 0xFF


def build_rcp_call_setup(call_type: int, dest_id: int, seq: int = 1) -> bytes:
    """
    Baut ein vollständiges HSTRP TO_RADIO Paket für RCP Call Setup (Opcode 0x0841).
    Format:
      HSTRP: 32 42 00 00 <seq:2B BE>
      RCP:   02 <41 08: LE Opcode> <05 00: LE Len> <call_type:1B> <dest_id:4B LE> <csum:1B> <03:Trailer>
    """
    payload = struct.pack('<BI', call_type & 0xFF, dest_id & 0xFFFFFFFF)
    opcode = RCP_OPCODE_CALL_SETUP
    header = struct.pack('<BHH', RCP_MSG_HDR, opcode, len(payload))
    csum = calculate_rcp_checksum(header[1:5], payload)
    rcp_block = header + payload + struct.pack('BB', csum, 0x03)
    
    hstrp_hdr = HSTRP_SIG + struct.pack('>BH', HSTRP_TO_RADIO, seq & 0xFFFF)
    return hstrp_hdr + rcp_block


def build_rcp_button_request(
    target: int = BUTTON_TARGET_FRONT_PTT,
    operation: int = BUTTON_OP_PRESS,
    seq: int = 1
) -> bytes:
    """
    Baut ein vollständiges HSTRP TO_RADIO Paket für PTT Taste Drücken/Loslassen (Opcode 0x0041).
    Format:
      HSTRP: 32 42 00 00 <seq:2B BE>
      RCP:   02 <41 00: LE Opcode> <02 00: LE Len> <target:1B> <op:1B> <csum:1B> <03:Trailer>
    """
    payload = struct.pack('<BB', target & 0xFF, operation & 0xFF)
    opcode = RCP_OPCODE_BUTTON
    header = struct.pack('<BHH', RCP_MSG_HDR, opcode, len(payload))
    csum = calculate_rcp_checksum(header[1:5], payload)
    rcp_block = header + payload + struct.pack('BB', csum, 0x03)
    
    hstrp_hdr = HSTRP_SIG + struct.pack('>BH', HSTRP_TO_RADIO, seq & 0xFFFF)
    return hstrp_hdr + rcp_block


def build_rcp_radio_disable(dest_id: int, seq: int = 1) -> bytes:
    """
    Baut ein vollständiges HSTRP TO_RADIO Paket für RCP Radio Disable / Stun (Opcode 0x0847).
    """
    payload = struct.pack('<BI', 0x01, dest_id & 0xFFFFFFFF)
    opcode = 0x0847
    header = struct.pack('<BHH', RCP_MSG_HDR, opcode, len(payload))
    csum = calculate_rcp_checksum(header[1:5], payload)
    rcp_block = header + payload + struct.pack('BB', csum, 0x03)
    hstrp_hdr = HSTRP_SIG + struct.pack('>BH', HSTRP_TO_RADIO, seq & 0xFFFF)
    return hstrp_hdr + rcp_block


def build_rtp_voice_packet(
    ulaw_payload: bytes,
    seq: int,
    timestamp: int,
    ssrc: int = 0x12345678
) -> bytes:
    """
    Baut ein Hytera-kompatibles RTP-Audiopaket (G.711 µ-law, 8 kHz).
    Enthält den 12-Byte RTP-Header mit Extension-Flag (0x9000),
    gefolgt vom 16-Byte Hytera Extension Header und der Audionutzlast.
    """
    # RTP Fixed Header: V=2, P=0, X=1 (Extension), CC=0 -> 0x90
    # Payload Type = 0 (PCMU, G.711 µ-law) -> 0x00
    rtp_fixed = struct.pack(
        '>BBHII',
        0x90,                   # V=2, X=1
        0x00,                   # Marker=0, PT=0 (PCMU)
        seq & 0xFFFF,
        timestamp & 0xFFFFFFFF,
        ssrc & 0xFFFFFFFF,
    )
    # Hytera Extension Header:
    # 00 15: Extension Profile ID (0x0015)
    # 00 03: Length = 3 Worte (12 Bytes Extension-Daten)
    # 12 Bytes Daten (Nullen)
    hytera_ext = bytes.fromhex('00150003000000000000000000000000')

    # Payload auf exakt 160 Bytes auffüllen falls kürzer
    payload = ulaw_payload
    if len(payload) < RTP_FRAME_SAMPLES:
        payload = payload + (b'\xFF' * (RTP_FRAME_SAMPLES - len(payload)))
    elif len(payload) > RTP_FRAME_SAMPLES:
        payload = payload[:RTP_FRAME_SAMPLES]

    return rtp_fixed + hytera_ext + payload


# ── Audio-Resampling & G.711 µ-law Encoder ─────────────────────────────────────
def pcm16_to_ulaw(pcm_bytes: bytes) -> bytes:
    """Konvertiert 16-Bit Signed Linear PCM zu 8-Bit G.711 µ-law."""
    if audioop is not None:
        return audioop.lin2ulaw(pcm_bytes, 2)
    
    # Reiner Python-Fallback nach ITU-T G.711
    BIAS = 0x84
    CLIP = 32635
    out = bytearray(len(pcm_bytes) // 2)
    for i in range(0, len(pcm_bytes), 2):
        sample = struct.unpack_from('<h', pcm_bytes, i)[0]
        sign = 0x80 if sample < 0 else 0
        if sample < 0:
            sample = -sample
        if sample > CLIP:
            sample = CLIP
        sample += BIAS
        exponent = 7
        mask = 0x4000
        while exponent > 0 and (sample & mask) == 0:
            exponent -= 1
            mask >>= 1
        mantissa = (sample >> (exponent + 3)) & 0x0F
        ulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
        out[i // 2] = ulaw_byte
    return bytes(out)


def resample_pcm16_mono(pcm_bytes: bytes, in_rate: int, out_rate: int = 8000) -> bytes:
    """
    Resampelt 16-Bit Mono PCM von einer Quell-Abtastrate (z. B. 48000 Hz, 44100 Hz)
    auf 8000 Hz.
    """
    if in_rate == out_rate or not pcm_bytes:
        return pcm_bytes

    if audioop is not None:
        resampled, _ = audioop.ratecv(pcm_bytes, 2, 1, in_rate, out_rate, None)
        return resampled

    # Einfacher linearer Resampling-Fallback ohne C-Extensions
    num_samples = len(pcm_bytes) // 2
    if num_samples == 0:
        return b''
    samples = struct.unpack(f'<{num_samples}h', pcm_bytes)
    out_samples_len = int(num_samples * out_rate / in_rate)
    out = []
    for i in range(out_samples_len):
        pos = i * in_rate / out_rate
        idx = int(pos)
        frac = pos - idx
        if idx + 1 < num_samples:
            val = int(samples[idx] * (1.0 - frac) + samples[idx + 1] * frac)
        else:
            val = samples[-1]
        out.append(max(-32768, min(32767, val)))
    return struct.pack(f'<{len(out)}h', *out)


# ── HyteraTxSender Klasse ──────────────────────────────────────────────────────
class HyteraTxSender:
    """
    Verwaltet das Senden von Sprache und PTT an den Hytera Repeater.
    Gewährleistet exaktes 20ms Frame-Pacing, Time-Out Timer (TOT) und
    Thread-sichere Audio-Pufferung.
    """

    def __init__(
        self,
        repeater_ip: str = DEFAULT_REPEATER_IP,
        send_udp_func: Optional[Callable[[bytes, tuple], None]] = None,
        tot_timeout_s: float = 60.0,
        on_state_change: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.repeater_ip = repeater_ip
        self.send_udp_func = send_udp_func
        self.tot_timeout_s = tot_timeout_s
        self.on_state_change = on_state_change

        # Status
        self.is_transmitting = False
        self.active_slot = "TS1"
        self.active_target_id = 1
        self.active_call_type = CALL_TYPE_GROUP
        self.tx_start_time: Optional[float] = None
        self.frames_sent = 0
        self.bytes_sent = 0

        # Sequenznummern
        self._rcp_seq = 1
        self._rtp_seq = 100
        self._rtp_timestamp = 1000

        # Audio-Puffer & Pacing Queue
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._pcm_residue = bytearray()
        self._pacer_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None

    def _get_ports_for_slot(self, slot: str) -> tuple[int, int]:
        """Gibt (rcp_port, rtp_port) für TS1 oder TS2 zurück."""
        if slot.upper() == "TS2":
            return 30010, 30014
        return 30009, 30012

    def _send_packet(self, data: bytes, port: int) -> None:
        """Sendet ein UDP-Paket an den Repeater."""
        addr = (self.repeater_ip, port)
        if self.send_udp_func:
            self.send_udp_func(data, addr)
        else:
            # Fallback: Schneller One-Shot UDP Socket
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(data, addr)
                sock.close()
            except Exception as exc:
                logger.error(f"TX Sende-Fehler an {addr}: {exc}")

    async def _notify_state(self) -> None:
        """Informiert Callbacks / WebSockets über Zustandsänderungen."""
        if self.on_state_change:
            try:
                await self.on_state_change(self.get_status())
            except Exception as exc:
                logger.debug(f"TX State Callback Fehler: {exc}")

    async def start_tx(
        self,
        slot: str = "TS1",
        target_id: int = 1,
        call_type: int = CALL_TYPE_GROUP,
    ) -> bool:
        """
        Initiiert den Sendevorgang:
        1. Call Setup auf RCP-Port (Opcode 0x0841)
        2. Wartezeit 50 ms
        3. PTT-Drücken (Opcode 0x0041 FRONT_PTT Press)
        4. Startet Pacer- und TOT-Watchdog-Tasks
        """
        if self.is_transmitting:
            logger.info("start_tx aufgerufen, aber TX ist bereits aktiv (Re-Keying oder aktiver Stream).")
            return True

        self.is_transmitting = True
        self.active_slot = slot.upper()
        self.active_call_type = call_type
        if call_type == CALL_TYPE_ALL:
            self.active_target_id = 16777215
        else:
            self.active_target_id = target_id
        self.tx_start_time = time.monotonic()
        self.frames_sent = 0
        self.bytes_sent = 0
        self._pcm_residue.clear()

        # Audio-Queue leeren
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        rcp_port, _ = self._get_ports_for_slot(self.active_slot)

        # 1. Call Setup
        self._rcp_seq = (self._rcp_seq + 1) & 0xFFFF
        setup_pkt = build_rcp_call_setup(
            call_type=self.active_call_type,
            dest_id=self.active_target_id,
            seq=self._rcp_seq,
        )
        self._send_packet(setup_pkt, rcp_port)
        logger.info(f"TX Call Setup -> {self.active_slot}, TG {self.active_target_id}, Typ {self.active_call_type}")

        # Kurze Pause für Repeater-Bereitstellung
        await asyncio.sleep(0.050)

        # 2. PTT Taste drücken
        self._rcp_seq = (self._rcp_seq + 1) & 0xFFFF
        ptt_pkt = build_rcp_button_request(
            target=BUTTON_TARGET_FRONT_PTT,
            operation=BUTTON_OP_PRESS,
            seq=self._rcp_seq,
        )
        self._send_packet(ptt_pkt, rcp_port)
        logger.info(f"TX PTT KEY-UP gesendet an Port {rcp_port}")

        # 3. Tasks starten
        self._pacer_task = asyncio.create_task(self._frame_pacer_loop())
        self._watchdog_task = asyncio.create_task(self._tot_watchdog())

        await self._notify_state()
        return True

    async def stop_tx(self) -> bool:
        """
        Beendet den Sendevorgang:
        1. PTT-Taste loslassen (Opcode 0x0041 FRONT_PTT Release)
        2. Stoppt Hintergrund-Tasks
        """
        if not self.is_transmitting:
            return False

        self.is_transmitting = False
        duration_s = (time.monotonic() - self.tx_start_time) if self.tx_start_time else 0.0

        # Tasks beenden
        if self._pacer_task and not self._pacer_task.done():
            self._pacer_task.cancel()
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()

        rcp_port, _ = self._get_ports_for_slot(self.active_slot)

        # PTT Taste loslassen (zweimal senden für Zuverlässigkeit wie in HytAudioBridge)
        self._rcp_seq = (self._rcp_seq + 1) & 0xFFFF
        release_pkt = build_rcp_button_request(
            target=BUTTON_TARGET_FRONT_PTT,
            operation=BUTTON_OP_RELEASE,
            seq=self._rcp_seq,
        )
        self._send_packet(release_pkt, rcp_port)
        self._send_packet(release_pkt, rcp_port)

        logger.info(f"TX PTT KEY-DOWN -> Dauer: {duration_s:.1f}s, {self.frames_sent} Frames gesendet.")
        self.tx_start_time = None
        self._pcm_residue.clear()

        await self._notify_state()
        return True

    async def send_radio_disable(self, radio_id: int, slot: str = "TS1") -> bool:
        """Sendet einen OTA Radio Disable (Stun) Befehl an ein Zielfunkgerät."""
        rcp_port, _ = self._get_ports_for_slot(slot)
        self._rcp_seq = (self._rcp_seq + 1) & 0xFFFF
        pkt = build_rcp_radio_disable(radio_id, seq=self._rcp_seq)
        self._send_packet(pkt, rcp_port)
        logger.info(f"OTA Radio Disable (Stun) gesendet an Radio-ID {radio_id} auf Slot {slot}")
        return True

    def feed_pcm16_audio(self, pcm_bytes: bytes, in_sample_rate: int = 8000) -> int:
        """
        Nimmt rohe 16-Bit Mono PCM-Audiodaten entgegen (z. B. vom Smartphone-Mikrofon),
        resampelt sie nach Bedarf auf 8000 Hz, konvertiert sie in G.711 µ-law und
        reiht sie in 160-Byte Paketen in die Sende-Queue ein.
        Gibt die Anzahl erzeugter 20ms-Frames zurück.
        """
        if not self.is_transmitting or not pcm_bytes:
            return 0

        # 1. Resampling auf 8000 Hz falls nötig
        if in_sample_rate != RTP_SAMPLE_RATE:
            pcm_8k = resample_pcm16_mono(pcm_bytes, in_sample_rate, RTP_SAMPLE_RATE)
        else:
            pcm_8k = pcm_bytes

        # 2. Zu Restpuffer hinzufügen (für exakte 160-Sample-Blöcke = 320 Bytes PCM16)
        self._pcm_residue.extend(pcm_8k)
        frame_bytes = RTP_FRAME_SAMPLES * 2  # 320 Bytes PCM16

        frames_added = 0
        while len(self._pcm_residue) >= frame_bytes:
            chunk = bytes(self._pcm_residue[:frame_bytes])
            del self._pcm_residue[:frame_bytes]
            ulaw_frame = pcm16_to_ulaw(chunk)
            self._audio_queue.put_nowait(ulaw_frame)
            frames_added += 1

        return frames_added

    async def _frame_pacer_loop(self) -> None:
        """
        Sendet exakt alle 20 ms ein 160-Byte Audio-Frame an das Relais.
        Falls keine neuen Audiodaten vorliegen, wird Stille (0xFF) gesendet,
        damit der DMR-Sync-Burst nicht abreißt.
        """
        _, rtp_port = self._get_ports_for_slot(self.active_slot)
        silence_frame = b'\xFF' * RTP_FRAME_SAMPLES

        try:
            while self.is_transmitting:
                start_tick = time.monotonic()

                try:
                    # Nicht blockierend holen oder Stille senden
                    frame = self._audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    frame = silence_frame

                # RTP Paket bauen
                self._rtp_seq = (self._rtp_seq + 1) & 0xFFFF
                self._rtp_timestamp = (self._rtp_timestamp + RTP_FRAME_SAMPLES) & 0xFFFFFFFF
                rtp_packet = build_rtp_voice_packet(
                    ulaw_payload=frame,
                    seq=self._rtp_seq,
                    timestamp=self._rtp_timestamp,
                )

                self._send_packet(rtp_packet, rtp_port)
                self.frames_sent += 1
                self.bytes_sent += len(rtp_packet)

                # Exaktes Pacing auf 20ms
                elapsed = time.monotonic() - start_tick
                sleep_time = max(0.001, RTP_FRAME_DURATION_S - elapsed)
                await asyncio.sleep(sleep_time)
        except asyncio.CancelledError:
            pass

    async def _tot_watchdog(self) -> None:
        """Verhindert versehentliches Dauersenden durch TOT-Timeout."""
        try:
            await asyncio.sleep(self.tot_timeout_s)
            if self.is_transmitting:
                logger.warning(f"TX TOT-Watchdog ausgelöst ({self.tot_timeout_s}s erreicht) -> Notabschaltung!")
                await self.stop_tx()
        except asyncio.CancelledError:
            pass

    def get_status(self) -> Dict[str, Any]:
        """Gibt den aktuellen Sendestatus für API und WebSockets zurück."""
        duration_s = 0.0
        if self.is_transmitting and self.tx_start_time:
            duration_s = round(time.monotonic() - self.tx_start_time, 1)

        return {
            "is_transmitting": self.is_transmitting,
            "slot":            self.active_slot,
            "target_id":       self.active_target_id,
            "call_type":       self.active_call_type,
            "duration_s":      duration_s,
            "frames_sent":     self.frames_sent,
            "bytes_sent":      self.bytes_sent,
            "queue_depth":     self._audio_queue.qsize(),
            "repeater_ip":     self.repeater_ip,
        }
