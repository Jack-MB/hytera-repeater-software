"""
Hytera Command Center – Audio Recorder Service v2
==================================================
Nimmt RTP-Pakete (G.711 µ-law/A-law) auf und speichert sie als WAV-Dateien.

Wesentliche Verbesserungen v2:
  1. JitterBuffer: Sequenznummer-Reordering und Deduplizierung (80-160ms Fenster)
  2. StreamingWAVWriter: Schreibt Audiodaten direkt auf die Festplatte (kein RAM-Puffer, absturzsicher)
  3. RTP-Paket-Diagnose & Probe-Modus: Analyse von Codec, Payload-Type, Header-Extensions und Radio-ID
  4. Direkte PTT-ID-Verknüpfung: Übergabe von ptt_id im Callback für sofortiges DB-Update
  5. Automatische Retention-Housekeeping: Ältere Aufnahmen (> 30 Tage) werden bereinigt

Technische Details:
  - Hytera HR1065 sendet G.711 µ-law (8 kHz, mono) auf Port 30012 (TS1) und 30014 (TS2)
  - RTP-Header: 12 Byte (RFC 3550) + optionale Hytera-Extension
  - Payload-Typ 0 = G.711 µ-law (PCMU), 8 = G.711 A-law (PCMA)
  - Konvertierung: µ-law / A-law → Linear PCM 16-bit → WAV (8 kHz Mono)
"""

import os
import io
import struct
import wave
import asyncio
import logging
import time
import inspect
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple, Callable, Awaitable, Any

logger = logging.getLogger("audio_recorder")


# ─────────────────────────────────────────────────────────────────
# 1. G.711 µ-law & A-law Dekodierung (Reine Python Lookup-Tables)
# ─────────────────────────────────────────────────────────────────

def _build_ulaw_table() -> list:
    """Erstellt die µ-law Dekodierungs-Lookup-Tabelle (256 Einträge)."""
    table = []
    for i in range(256):
        byte = ~i & 0xFF
        sign = -1 if (byte & 0x80) else 1
        exponent = (byte >> 4) & 0x07
        mantissa = byte & 0x0F
        magnitude = ((mantissa << 1) + 33) << exponent
        table.append(sign * (magnitude - 33))
    return table


def _build_alaw_table() -> list:
    """Erstellt die A-law Dekodierungs-Lookup-Tabelle (256 Einträge)."""
    table = []
    for i in range(256):
        a = i ^ 0x55
        sign = -1 if (a & 0x80) else 1
        a &= 0x7F
        if a < 16:
            linear = (a << 1) | 1
        else:
            exponent = (a >> 4) & 0x07
            mantissa = a & 0x0F
            linear = ((mantissa | 0x10) << exponent)
        table.append(sign * linear)
    return table


_ULAW_TABLE: list = _build_ulaw_table()
_ALAW_TABLE: list = _build_alaw_table()


def decode_ulaw(data: bytes) -> bytes:
    """Konvertiert G.711 µ-law bytes → 16-bit signed PCM Little-Endian bytes."""
    pcm = bytearray(len(data) * 2)
    for i, b in enumerate(data):
        sample = _ULAW_TABLE[b]
        struct.pack_into("<h", pcm, i * 2, max(-32768, min(32767, sample)))
    return bytes(pcm)


def decode_alaw(data: bytes) -> bytes:
    """Konvertiert G.711 A-law bytes → 16-bit signed PCM Little-Endian bytes."""
    pcm = bytearray(len(data) * 2)
    for i, b in enumerate(data):
        sample = _ALAW_TABLE[b]
        struct.pack_into("<h", pcm, i * 2, max(-32768, min(32767, sample)))
    return bytes(pcm)


# ─────────────────────────────────────────────────────────────────
# 2. RTP Header Parser & Diagnose
# ─────────────────────────────────────────────────────────────────

@dataclass
class RTPHeader:
    """RFC 3550 RTP Header mit Hytera-Erweiterungsinformationen."""
    version:      int   # üblicherweise 2
    padding:      bool
    extension:    bool
    cc:           int   # CSRC count
    marker:       bool
    payload_type: int   # 0=PCMU, 8=PCMA, 96+=Dynamic/AMBE
    sequence:     int   # 16-bit uint
    timestamp:    int   # 32-bit uint
    ssrc:         int   # 32-bit uint
    radio_id:     Optional[int] = None
    payload_offset: int = 12
    valid:        bool = False


def parse_rtp_header(data: bytes) -> Optional[RTPHeader]:
    """Parst den RTP-Header und extrahiert ggf. die Hytera Radio-ID aus der Extension."""
    if len(data) < 12:
        return None
    b0, b1 = data[0], data[1]
    version = (b0 >> 6) & 0x03
    if version != 2:
        return None

    padding   = bool(b0 & 0x20)
    extension = bool(b0 & 0x10)
    cc        = b0 & 0x0F
    marker    = bool(b1 & 0x80)
    pt        = b1 & 0x7F
    seq       = struct.unpack_from(">H", data, 2)[0]
    ts        = struct.unpack_from(">I", data, 4)[0]
    ssrc      = struct.unpack_from(">I", data, 8)[0]

    offset = 12 + cc * 4
    extracted_radio_id = None

    if extension and len(data) >= offset + 4:
        ext_len_words = struct.unpack_from(">H", data, offset + 2)[0]
        ext_data_start = offset + 4
        # Hytera codiert die Radio-ID in den ersten 4 Bytes der Extension als uint32 BE
        if ext_len_words >= 1 and len(data) >= ext_data_start + 4:
            val = struct.unpack_from(">I", data, ext_data_start)[0]
            rid = val & 0xFFFFFF
            if 100 <= rid <= 16776415:
                extracted_radio_id = rid
        offset += 4 + ext_len_words * 4

    return RTPHeader(
        version=version, padding=padding, extension=extension, cc=cc,
        marker=marker, payload_type=pt, sequence=seq, timestamp=ts,
        ssrc=ssrc, radio_id=extracted_radio_id, payload_offset=offset,
        valid=True
    )


def get_rtp_payload(data: bytes, header: RTPHeader) -> bytes:
    """Gibt den Nutzlast-Teil eines RTP-Pakets zurück (unter Berücksichtigung von Padding)."""
    offset = header.payload_offset
    if len(data) <= offset:
        return b""
    if header.padding and len(data) > offset:
        pad_len = data[-1]
        if pad_len > 0 and (len(data) - pad_len) >= offset:
            return data[offset:-pad_len]
    return data[offset:]


def diagnose_rtp_packet(data: bytes) -> Dict[str, Any]:
    """
    Führt eine tiefe Paket-Diagnose auf einem rohen UDP-Paket durch.
    Gibt Metadaten über Codec, Header, Payload-Größe und vermutetes Format zurück.
    """
    if len(data) < 12:
        return {
            "is_valid_rtp": False,
            "raw_size": len(data),
            "reason": f"Zu kurz für RTP ({len(data)} Bytes < 12)",
            "hex_preview": data[:32].hex(" ") if data else "",
        }

    header = parse_rtp_header(data)
    if not header or not header.valid:
        return {
            "is_valid_rtp": False,
            "raw_size": len(data),
            "reason": "Ungültige RTP Version oder Header",
            "hex_preview": data[:32].hex(" "),
        }

    payload = get_rtp_payload(data, header)
    codec_name = "Unbekannt"
    expected_sample_rate = 8000

    if header.payload_type == 0:
        codec_name = "G.711 µ-law (PCMU)"
    elif header.payload_type == 8:
        codec_name = "G.711 A-law (PCMA)"
    elif header.payload_type in (96, 97, 98, 99, 100):
        codec_name = f"Dynamic / Proprietär (PT={header.payload_type}, evtl. AMBE+2 oder Hytera SELP)"
    else:
        codec_name = f"RTP Payload Type {header.payload_type}"

    # Berechne Millisekunden basierend auf 8kHz Mono Standard (1 Byte = 1 Sample = 0.125ms)
    duration_ms = (len(payload) / 8.0) if header.payload_type in (0, 8) else 0.0

    return {
        "is_valid_rtp": True,
        "raw_size": len(data),
        "version": header.version,
        "payload_type": header.payload_type,
        "codec_name": codec_name,
        "sequence": header.sequence,
        "timestamp": header.timestamp,
        "ssrc": header.ssrc,
        "has_extension": header.extension,
        "radio_id_in_extension": header.radio_id,
        "payload_len_bytes": len(payload),
        "estimated_duration_ms": round(duration_ms, 1),
        "hex_preview": data[:32].hex(" "),
        "payload_preview": payload[:16].hex(" ") if payload else "",
    }


# ─────────────────────────────────────────────────────────────────
# 3. JitterBuffer: Reordering & Deduplizierung
# ─────────────────────────────────────────────────────────────────

def seq_diff(s1: int, s2: int) -> int:
    """Berechnet (s1 - s2) unter Berücksichtigung des 16-Bit Sequence-Wraparounds."""
    return ((s1 - s2 + 32768) % 65536) - 32768


class JitterBuffer:
    """
    RTP Jitter-Buffer zur Paket-Sortierung und Filterung von Duplikaten.
    Arbeitet mit einem kompakten Zeit-/Paketfenster (typisch 4–8 Pakete = 80–160ms).
    """

    def __init__(self, max_buffer_packets: int = 6):
        self.max_buffer_packets = max_buffer_packets
        self._packets: Dict[int, bytes] = {}  # seq -> pcm_bytes
        self._seen: set = set()               # Zuletzt gesehene Sequenznummern
        self._expected_seq: Optional[int] = None

    def push(self, seq: int, pcm: bytes) -> List[bytes]:
        """
        Fügt ein PCM-Frame mit Sequenznummer ein.
        Gibt eine Liste von in der richtigen Reihenfolge fertigen PCM-Frames zurück.
        """
        if seq in self._seen:
            # Duplikat verwerfen
            return []

        self._seen.add(seq)
        if len(self._seen) > 200:
            # Begrenze das Duplikate-Set
            self._seen = {s for s in self._seen if seq_diff(seq, s) < 100}

        if self._expected_seq is None:
            self._expected_seq = seq

        self._packets[seq] = pcm

        ready: List[bytes] = []

        # Solange das erwartete Paket vorhanden ist, direkt ausliefern
        while self._expected_seq in self._packets:
            ready.append(self._packets.pop(self._expected_seq))
            self._expected_seq = (self._expected_seq + 1) & 0xFFFF

        # Wenn der Puffer zu voll wird (Paketverlust / zu große Lücke), erzwinge Fortlauf
        if len(self._packets) >= self.max_buffer_packets:
            # Älteste Sequenz im Puffer suchen
            sorted_seqs = sorted(self._packets.keys(), key=lambda s: seq_diff(s, self._expected_seq or 0))
            if sorted_seqs:
                oldest_seq = sorted_seqs[0]
                ready.append(self._packets.pop(oldest_seq))
                self._expected_seq = (oldest_seq + 1) & 0xFFFF
                # Prüfe ob jetzt Nachfolger da sind
                while self._expected_seq in self._packets:
                    ready.append(self._packets.pop(self._expected_seq))
                    self._expected_seq = (oldest_seq + 1) & 0xFFFF

        return ready

    def flush(self) -> List[bytes]:
        """Leert den gesamten verbleibenden Puffer in Sequenz-Reihenfolge."""
        if not self._packets:
            return []
        sorted_seqs = sorted(self._packets.keys(), key=lambda s: seq_diff(s, self._expected_seq or 0))
        result = [self._packets[s] for s in sorted_seqs]
        self._packets.clear()
        return result


# ─────────────────────────────────────────────────────────────────
# 4. StreamingWAVWriter: Disk-basiertes Streaming
# ─────────────────────────────────────────────────────────────────

class StreamingWAVWriter:
    """
    Schreibt 16-Bit PCM Mono WAV-Dateien direkt auf die Festplatte.
    Vorteile:
      - Kein Halten großer Audiodaten im RAM
      - Absturzsicher: Bereits empfangene Daten sind sicher auf Disk
      - Saubere Header-Aktualisierung bei Finalisierung
    """

    SAMPLE_RATE  = 8000   # 8 kHz Standard
    CHANNELS     = 1      # Mono
    SAMPLE_WIDTH = 2      # 16-Bit Signed Integer
    BYTES_PER_SEC = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.temp_filepath = filepath + ".part"
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        self._fp = open(self.temp_filepath, "wb")
        self._bytes_written = 0
        self._write_placeholder_header()

    def _write_placeholder_header(self) -> None:
        """Schreibt einen initialen 44-Byte WAV-Header mit 0-Größen."""
        # RIFF Header
        header = bytearray(44)
        header[0:4] = b"RIFF"
        struct.pack_into("<I", header, 4, 36)  # ChunkSize (später aktualisiert)
        header[8:12] = b"WAVE"
        header[12:16] = b"fmt "
        struct.pack_into("<I", header, 16, 16)  # Subchunk1Size (16 für PCM)
        struct.pack_into("<H", header, 20, 1)   # AudioFormat: 1 = Linear PCM
        struct.pack_into("<H", header, 22, self.CHANNELS)
        struct.pack_into("<I", header, 24, self.SAMPLE_RATE)
        struct.pack_into("<I", header, 28, self.BYTES_PER_SEC)
        struct.pack_into("<H", header, 32, self.CHANNELS * self.SAMPLE_WIDTH)  # BlockAlign
        struct.pack_into("<H", header, 34, self.SAMPLE_WIDTH * 8)              # BitsPerSample
        header[36:40] = b"data"
        struct.pack_into("<I", header, 40, 0)   # Subchunk2Size (später aktualisiert)
        self._fp.write(header)
        self._fp.flush()

    def write_pcm(self, pcm_data: bytes) -> None:
        """Schreibt dekodierte PCM-Bytes direkt in die Datei."""
        if not pcm_data or self._fp.closed:
            return
        self._fp.write(pcm_data)
        self._bytes_written += len(pcm_data)

    def finalize(self) -> str:
        """Schreibt die finalen Längen in den WAV-Header und benennt die Datei atomar um."""
        if self._fp.closed:
            return self.filepath
        try:
            # Header-Größen nachtragen
            self._fp.seek(4)
            self._fp.write(struct.pack("<I", 36 + self._bytes_written))
            self._fp.seek(40)
            self._fp.write(struct.pack("<I", self._bytes_written))
            self._fp.flush()
        finally:
            self._fp.close()

        # Atomare Umbenennung von .part zur Zieldatei
        if os.path.exists(self.filepath):
            try:
                os.remove(self.filepath)
            except OSError:
                pass
        os.replace(self.temp_filepath, self.filepath)

        duration_s = self._bytes_written / self.BYTES_PER_SEC
        logger.info(f"WAV gespeichert: {self.filepath} ({duration_s:.1f}s, {self._bytes_written // 1024} KB)")
        return self.filepath

    def discard(self) -> None:
        """Schließt und löscht die temporäre Aufnahmedatei."""
        if not self._fp.closed:
            self._fp.close()
        if os.path.exists(self.temp_filepath):
            try:
                os.remove(self.temp_filepath)
            except OSError:
                pass

    @property
    def duration_s(self) -> float:
        return self._bytes_written / self.BYTES_PER_SEC


# ─────────────────────────────────────────────────────────────────
# 5. Recording Session
# ─────────────────────────────────────────────────────────────────

@dataclass
class RecordingSession:
    """Eine aktive Aufnahme-Session pro SSRC (= ein PTT-Vorgang)."""
    ssrc:          int
    radio_id:      int
    slot:          str
    started_at:    float = field(default_factory=time.time)
    last_packet:   float = field(default_factory=time.time)
    writer:        Optional[StreamingWAVWriter] = None
    jitter_buf:    JitterBuffer = field(default_factory=JitterBuffer)
    packet_count:  int = 0
    payload_type:  int = 0  # 0=µ-law, 8=A-law
    filepath:      str = ""
    ptt_id:        Optional[int] = None


# ─────────────────────────────────────────────────────────────────
# 6. AudioRecorder – Hauptklasse
# ─────────────────────────────────────────────────────────────────

OnRecordingDone = Callable[..., Awaitable[None]]


class AudioRecorder:
    """
    Audio Recorder Service v2 für Hytera HR1065.
    Verarbeitet RTP-Streams von Port 30012 und 30014, wandelt G.711 in WAV
    und verknüpft die Aufnahme direkt mit der Datenbank.
    """

    SESSION_TIMEOUT_S = 2.5   # Nach 2.5s Pause ohne Pakete wird Aufnahme abgeschlossen
    MIN_DURATION_S    = 0.3   # Mindestdauer (unter 300ms verwerfen)
    MAX_DURATION_S    = 300.0 # Maximale Länge eines einzelnen Durchgangs

    def __init__(
        self,
        recordings_dir: str,
        on_done: Optional[OnRecordingDone] = None,
        enabled: bool = True,
        retention_days: int = 30,
    ):
        self.recordings_dir = recordings_dir
        self.on_done        = on_done
        self.enabled        = enabled
        self.retention_days = retention_days

        self._sessions:       Dict[int, RecordingSession] = {}  # ssrc → session
        self._cleanup_task:   Optional[asyncio.Task] = None
        self._housekeep_task: Optional[asyncio.Task] = None

        # Probe-Diagnose Modus
        self._probe_active:   bool = False
        self._probe_packets:  List[Dict[str, Any]] = []
        self._probe_max:      int = 50

    async def start(self) -> None:
        """Startet den Recorder und die Hintergrund-Aufgaben."""
        os.makedirs(self.recordings_dir, exist_ok=True)
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="audio_session_cleanup"
        )
        self._housekeep_task = asyncio.create_task(
            self._housekeeping_loop(), name="audio_housekeeping"
        )
        logger.info(f"AudioRecorder v2 gestartet. Verzeichnis: {self.recordings_dir}")

    def stop(self) -> None:
        """Stoppt den Recorder und finalisiert alle offenen Sessions."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        if self._housekeep_task:
            self._housekeep_task.cancel()
        for ssrc in list(self._sessions.keys()):
            self._finalize_session(ssrc)

    # ── Probe Diagnose API ──────────────────────────────────────────

    def start_probe(self, max_packets: int = 50) -> None:
        """Aktiviert den Probe-Modus zum Aufzeichnen und Analysieren von RTP-Headern."""
        self._probe_active  = True
        self._probe_max     = max(5, min(max_packets, 200))
        self._probe_packets = []
        logger.info(f"Audio-Probe-Modus aktiviert (max {self._probe_max} Pakete).")

    def stop_probe(self) -> None:
        """Deaktiviert den Probe-Modus."""
        self._probe_active = False

    def get_probe_data(self) -> Dict[str, Any]:
        """Gibt die bisher im Probe-Modus gesammelten Diagnose-Daten zurück."""
        summary = {
            "probe_active": self._probe_active,
            "packet_count": len(self._probe_packets),
            "payload_types_seen": list(set(p.get("payload_type") for p in self._probe_packets if "payload_type" in p)),
            "codecs_detected": list(set(p.get("codec_name") for p in self._probe_packets if "codec_name" in p)),
            "radio_ids_seen": list(set(p.get("radio_id_in_extension") for p in self._probe_packets if p.get("radio_id_in_extension"))),
            "samples": self._probe_packets[-20:],
        }
        return summary

    # ── Packet Processing ───────────────────────────────────────────

    async def process_rtp_event(
        self,
        port:     int,
        raw:      bytes,
        radio_id: int = 0,
        ptt_id:   Optional[int] = None,
    ) -> None:
        """
        Verarbeitet ein rohes RTP-UDP-Paket aus dem Hytera-Netzwerk-Stream.
        Wird direkt aus dem manager.py / main.py Event-Handler aufgerufen.
        """
        if not self.enabled or len(raw) < 12:
            return

        # Bei aktivem Probe-Modus Paket analysieren
        if self._probe_active:
            diag = diagnose_rtp_packet(raw)
            diag["port"] = port
            diag["timestamp_received"] = time.time()
            self._probe_packets.append(diag)
            if len(self._probe_packets) >= self._probe_max:
                self._probe_active = False
                logger.info("Audio-Probe-Modus automatisch beendet (Limit erreicht).")

        header = parse_rtp_header(raw)
        if not header or not header.valid:
            return

        # Nur unterstützte Codecs aufnehmen (PCMU=0, PCMA=8)
        if header.payload_type not in (0, 8):
            return

        payload = get_rtp_payload(raw, header)
        if not payload:
            return

        # Radio-ID bevorzugt aus dem Hytera-Extension-Header nehmen falls vorhanden
        effective_radio_id = header.radio_id or radio_id

        ssrc = header.ssrc
        slot = "TS1" if port in (30012,) else "TS2"

        # Neue Session starten oder bestehende fortführen
        if ssrc not in self._sessions:
            await self._start_session(ssrc, effective_radio_id, slot, header.payload_type, ptt_id)

        session = self._sessions[ssrc]
        session.last_packet  = time.time()
        session.packet_count += 1
        session.payload_type  = header.payload_type
        if ptt_id and not session.ptt_id:
            session.ptt_id = ptt_id
        if effective_radio_id and not session.radio_id:
            session.radio_id = effective_radio_id

        # 1. Dekodieren zu 16-Bit PCM
        if header.payload_type == 0:
            pcm_chunk = decode_ulaw(payload)
        else:
            pcm_chunk = decode_alaw(payload)

        # 2. Durch Jitter-Buffer sortieren und schreiben
        ordered_chunks = session.jitter_buf.push(header.sequence, pcm_chunk)
        if session.writer:
            for chunk in ordered_chunks:
                session.writer.write_pcm(chunk)

        # 3. Sicherheitsprüfung maximale Aufnahmedauer
        if (time.time() - session.started_at) > self.MAX_DURATION_S:
            logger.warning(f"Audio-Session {ssrc}: Maximale Aufnahmedauer erreicht, finalisiere.")
            await self._finish_session(ssrc)

    async def _start_session(
        self, ssrc: int, radio_id: int, slot: str, payload_type: int, ptt_id: Optional[int] = None
    ) -> None:
        """Startet eine neue Aufnahme-Session mit StreamingWAVWriter."""
        ts  = time.strftime("%Y%m%d_%H%M%S")
        day = time.strftime("%Y-%m-%d")
        filename = f"{radio_id}_{ts}_{slot}.wav"
        filepath = os.path.join(self.recordings_dir, day, filename)

        writer = StreamingWAVWriter(filepath)
        session = RecordingSession(
            ssrc=ssrc, radio_id=radio_id, slot=slot,
            writer=writer, filepath=filepath, payload_type=payload_type,
            ptt_id=ptt_id
        )
        self._sessions[ssrc] = session
        logger.debug(f"Audio-Session gestartet: SSRC={ssrc}, Radio={radio_id}, Slot={slot}, PTT={ptt_id}")

    def _finalize_session(self, ssrc: int) -> Optional[Tuple[int, str, float, Optional[int]]]:
        """Finalisiert eine Session synchron. Gibt (radio_id, filepath, duration, ptt_id) zurück."""
        session = self._sessions.pop(ssrc, None)
        if not session or not session.writer:
            return None

        # Verbleibende Pakete aus dem JitterBuffer flushen
        tail_chunks = session.jitter_buf.flush()
        for chunk in tail_chunks:
            session.writer.write_pcm(chunk)

        duration = session.writer.duration_s
        if duration < self.MIN_DURATION_S or session.packet_count < 3:
            session.writer.discard()
            logger.debug(f"Audio-Session {ssrc} verworfen (zu kurz: {duration:.2f}s, {session.packet_count} Pkts)")
            return None

        try:
            session.writer.finalize()
            return session.radio_id, session.filepath, duration, session.ptt_id
        except Exception as e:
            logger.warning(f"WAV-Finalisierung fehlgeschlagen: {e}")
            return None

    async def _finish_session(self, ssrc: int) -> None:
        """Finalisiert eine Session asynchron und ruft on_done mit den Details auf."""
        result = self._finalize_session(ssrc)
        if result and self.on_done:
            radio_id, filepath, duration, ptt_id = result
            try:
                # Untersuche Callback-Signatur auf ptt_id Unterstützung
                sig = inspect.signature(self.on_done)
                param_count = len(sig.parameters)
                if param_count >= 4:
                    await self.on_done(radio_id, filepath, duration, ptt_id)
                else:
                    await self.on_done(radio_id, filepath, duration)
            except Exception as e:
                logger.error(f"on_done Callback fehlgeschlagen: {e}")

    async def flush_all(self) -> None:
        """Finalisiert alle aktuell offenen Sessions sofort."""
        for ssrc in list(self._sessions.keys()):
            await self._finish_session(ssrc)

    async def stop_async(self) -> None:
        """Stoppt den Recorder asynchron und ruft on_done für alle offenen Sessions auf."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        if self._housekeep_task:
            self._housekeep_task.cancel()
        await self.flush_all()

    async def _cleanup_loop(self) -> None:
        """Überwacht laufende Sessions und finalisiert inaktive bei Timeout."""
        while True:
            try:
                sleep_interval = min(0.5, max(0.05, self.SESSION_TIMEOUT_S / 2))
                await asyncio.sleep(sleep_interval)
                now = time.time()
                expired = [
                    ssrc for ssrc, s in self._sessions.items()
                    if (now - s.last_packet) > self.SESSION_TIMEOUT_S
                ]
                for ssrc in expired:
                    await self._finish_session(ssrc)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Audio-Cleanup-Loop Fehler: {e}")

    async def _housekeeping_loop(self) -> None:
        """Führt alle 24 Stunden eine Bereinigung alter Aufnahmen durch."""
        while True:
            try:
                housekeeping_recordings(self.recordings_dir, self.retention_days)
                await asyncio.sleep(86400)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Audio-Housekeeping Fehler: {e}")
                await asyncio.sleep(3600)

    def get_stats(self) -> Dict[str, Any]:
        """Gibt detaillierte Statistiken über aktive und historische Sessions zurück."""
        return {
            "enabled": self.enabled,
            "active_sessions": len(self._sessions),
            "probe_active": self._probe_active,
            "sessions": [
                {
                    "ssrc":       s.ssrc,
                    "radio_id":   s.radio_id,
                    "slot":       s.slot,
                    "ptt_id":     s.ptt_id,
                    "duration_s": round(s.writer.duration_s if s.writer else (time.time() - s.started_at), 1),
                    "packets":    s.packet_count,
                    "codec":      "G.711 µ-law" if s.payload_type == 0 else "G.711 A-law",
                }
                for s in self._sessions.values()
            ],
        }


# ─────────────────────────────────────────────────────────────────
# 7. Retention Housekeeping
# ─────────────────────────────────────────────────────────────────

def housekeeping_recordings(recordings_dir: str, retention_days: int = 30) -> int:
    """
    Bereinigt Aufnahme-Ordner, die älter als retention_days Tage sind.
    Gibt die Anzahl gelöschter Dateien zurück.
    """
    if retention_days <= 0 or not os.path.isdir(recordings_dir):
        return 0

    cutoff_time = time.time() - (retention_days * 86400)
    deleted_count = 0

    try:
        for entry in os.listdir(recordings_dir):
            entry_path = os.path.join(recordings_dir, entry)
            if os.path.isdir(entry_path):
                # Prüfe Verzeichnis-Mtime
                try:
                    stat = os.stat(entry_path)
                    if stat.st_mtime < cutoff_time:
                        # Verzeichnis rekursiv leeren & löschen
                        for root, dirs, files in os.walk(entry_path, topdown=False):
                            for f in files:
                                f_path = os.path.join(root, f)
                                os.remove(f_path)
                                deleted_count += 1
                            for d in dirs:
                                os.rmdir(os.path.join(root, d))
                        os.rmdir(entry_path)
                        logger.info(f"Housekeeping: Veraltetes Aufnahme-Verzeichnis entfernt: {entry}")
                except Exception as ex:
                    logger.warning(f"Housekeeping-Fehler bei {entry}: {ex}")
    except Exception as e:
        logger.error(f"Housekeeping-Gesamtfehler: {e}")

    return deleted_count

