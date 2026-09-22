# -*- coding: utf-8 -*-
"""
hytera_protocol.py
HSTRP-Handshake + alle Hytera-Datenströme
"""
import socket, threading, time, struct, queue, logging
from datetime import datetime

log = logging.getLogger("hytera")

# ── HSTRP Signatur & Typen ────────────────────────────────
HSTRP_SIG       = b'\x32\x42\x00'
HSTRP_TO_RADIO  = 0x00
HSTRP_ACK       = 0x01
HSTRP_HEARTBEAT = 0x02
HSTRP_SYNACK    = 0x05
HSTRP_FROM_RAD  = 0x20
HSTRP_SYN       = 0x24

def make_hstrp(ptype, seqid=0, payload=b''):
    return HSTRP_SIG + struct.pack('>BH', ptype, seqid) + payload

def is_hstrp(data):
    return len(data) >= 6 and data[:3] == HSTRP_SIG

def is_rtp(data):
    return len(data) >= 12 and (data[0] >> 6) == 2

def parse_rtp_payload(data):
    """Gibt G.711-Payload zurück oder None."""
    if not is_rtp(data): return None
    cc = data[0] & 0x0F
    hdr = 12 + 4 * cc
    if data[0] & 0x10:                      # Extension
        if len(data) < hdr + 4: return None
        ext_len = struct.unpack_from('>H', data, hdr + 2)[0]
        hdr += 4 + ext_len * 4
    return data[hdr:] if len(data) > hdr else None

def parse_rtp_radio_id(data):
    """Extrahiert die Radio-ID aus dem Hytera RTP Extension Header.
    Hytera codiert die ID als uint32 BE an Extension-Byte 0-3,
    wobei die unteren 3 Bytes die DMR-ID enthalten.
    Gibt radio_id (int) oder None zurück."""
    if not is_rtp(data): return None
    if not (data[0] & 0x10): return None   # kein Extension-Header
    cc = data[0] & 0x0F
    ext_offset = 12 + 4 * cc
    if len(data) < ext_offset + 8: return None
    # Extension: 2B profile, 2B len (in 32-bit words), dann Daten
    ext_len_words = struct.unpack_from('>H', data, ext_offset + 2)[0]
    if ext_len_words < 1: return None
    data_start = ext_offset + 4
    if len(data) < data_start + 4: return None
    val = struct.unpack_from('>I', data, data_start)[0]
    radio_id = val & 0xFFFFFF
    if 100 <= radio_id <= 16776415:
        return radio_id
    return None


# ── Basis-Listener mit HSTRP-Handshake ───────────────────
class HSTRPListener(threading.Thread):
    """Basisklasse: öffnet UDP-Port (oder mehrere), führt HSTRP-Handshake durch,
    ruft on_packet() / on_rtp() bei eingehenden Daten auf."""

    def __init__(self, port, name, hb_cb=None, extra_ports=None):
        """port: primärer Port (int). extra_ports: optionale Liste weiterer Ports (TS2 etc.)."""
        super().__init__(daemon=True, name=name)
        self.port   = port
        self.name_  = name
        self.hb_cb  = hb_cb
        self._ports = [port] + (extra_ports or [])  # alle zu bindenden Ports
        self._stop  = threading.Event()
        self._sock  = None
        self._peer  = None   # (ip, port) des Repeaters
        self._seq   = 0

    def stop(self): self._stop.set()

    def _next_seq(self):
        s = self._seq; self._seq = (self._seq + 1) & 0xFFFF; return s

    def _send(self, data):
        if self._peer and self._sock:
            try: self._sock.sendto(data, self._peer)
            except: pass

    def on_packet(self, data, addr): pass  # Subklasse überschreibt
    def on_rtp(self, payload, addr): pass  # Subklasse überschreibt

    def run(self):
        # Mehrere Sockets – einer pro Port (TS1 + TS2)
        socks = []
        for p in self._ports:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", p))
                s.settimeout(1.0)
                socks.append(s)
                log.info(f"[{self.name_}] Port {p} offen")
            except OSError as e:
                log.error(f"[{self.name_}] Port {p} Fehler: {e}")

        if not socks:
            return
        self._sock = socks[0]  # primärer Socket für _send()

        import selectors
        sel = selectors.DefaultSelector()
        for s in socks:
            sel.register(s, selectors.EVENT_READ)

        while not self._stop.is_set():
            try:
                events = sel.select(timeout=0.02)
                for key, _ in events:
                    s = key.fileobj
                    data, addr = s.recvfrom(4096)
                    self._peer = addr
                    self._sock = s
                    if self.hb_cb: self.hb_cb(addr[0])

                    if is_hstrp(data):
                        ptype = data[3]
                        seqid = struct.unpack_from('>H', data, 4)[0]

                        if ptype == HSTRP_SYN:
                            self._seq  = seqid
                            ack = make_hstrp(HSTRP_SYNACK, self._next_seq())
                            s.sendto(ack, addr)
                            log.info(f"[{self.name_}] SYN von {addr[0]}:{addr[1]} (Port {s.getsockname()[1]}) → SYN-ACK")

                        elif ptype == HSTRP_HEARTBEAT:
                            # Heartbeat immer mit SeqID=0 beantworten (laut HSTRP-Spec)
                            hb = make_hstrp(HSTRP_HEARTBEAT, 0)
                            s.sendto(hb, addr)

                        elif ptype in (HSTRP_FROM_RAD, HSTRP_TO_RADIO):
                            ack = make_hstrp(HSTRP_ACK, seqid)
                            s.sendto(ack, addr)
                            self.on_packet(data[6:], addr)

                    elif is_rtp(data):
                        self.on_rtp(data, addr)  # ganzes RTP-Paket übergeben

            except Exception as e:
                log.debug(f"[{self.name_}] {e}")
            self._on_idle_check()   # Hook fuer Idle-Erkennung in Subklassen

        sel.close()
        for s in socks:
            try: s.close()
            except: pass
        log.info(f"[{self.name_}] gestoppt")


# ── Audio-Listener (TS1 / TS2) ───────────────────────────
    def _on_idle_check(self):
        pass  # Subklassen koennen ueberschreiben


class AudioListener(HSTRPListener):
    def __init__(self, port, slot_name, timeout, log_cb, status_cb,
                 hb_cb=None, audio_feed=None, rec_dir=None, bytes_cb=None, radio_id_cb=None):
        super().__init__(port, slot_name, hb_cb)
        self.slot_name    = slot_name
        self.timeout      = timeout
        self.log_cb       = log_cb
        self.status_cb    = status_cb
        self.audio_feed   = audio_feed
        self.rec_dir      = rec_dir
        self.bytes_recv   = 0
        self.pkt_count    = 0
        self.bytes_cb     = bytes_cb
        self.radio_id_cb  = radio_id_cb
        self.write_cb     = None         # callback(slot_name, payload_bytes)
        self.rec_cb       = None         # callback(slot_name)
        self.ptt_start_cb = None         # callback(slot_name, radio_id) — PTT erkannt
        self.ptt_end_cb   = None         # callback(slot_name, radio_id) — PTT ende
        self._active      = False
        self._last_pkt    = 0
        self._last_rid    = None         # Letzte Radio-ID aus RTP-Extension

    def on_rtp(self, raw_data, addr):
        now = time.time()
        rid = parse_rtp_radio_id(raw_data)
        if rid:
            self._last_rid = rid
            if self.radio_id_cb:
                self.radio_id_cb(rid)
        payload = parse_rtp_payload(raw_data)
        if payload is None: return
        if self.audio_feed: self.audio_feed(payload)
        if self.write_cb: self.write_cb(self.slot_name, payload)
        self.pkt_count  += 1
        self.bytes_recv += len(payload)
        if self.bytes_cb:
            self.bytes_cb(self.slot_name, self.pkt_count, self.bytes_recv)
        if not self._active:
            self._active = True
            self.status_cb(self.slot_name, "\u25cf AKTIV", "#f38ba8")
            if self.rec_cb: self.rec_cb(self.slot_name)
            # PTT-Start aus Audio melden (Fallback falls CC-Listener fehlt)
            if self.ptt_start_cb and self._last_rid:
                self.ptt_start_cb(self.slot_name, self._last_rid)
        self._last_pkt = now

    def _on_idle_check(self):
        """Wird vom HSTRPListener-Loop jede Sekunde aufgerufen."""
        if self._active and (time.time() - self._last_pkt) > self.timeout:
            self._active = False
            self.status_cb(self.slot_name, "bereit", "#a6e3a1")
            # PTT-Ende aus Audio melden
            if self.ptt_end_cb and self._last_rid:
                self.ptt_end_cb(self.slot_name, self._last_rid)


# ── Call-Control-Listener ─────────────────────────────────
class CallEvent:
    __slots__ = ('ts','slot','call_type','sender_id','target_id','status','rssi')
    def __init__(self, slot, call_type, sender_id, target_id, status, rssi=None):
        self.ts        = datetime.now()
        self.slot      = slot
        self.call_type = call_type
        self.sender_id = sender_id
        self.target_id = target_id
        self.status    = status   # "start" / "end"
        self.rssi      = rssi     # dBm (negativ) oder None wenn unbekannt

    @staticmethod
    def rssi_quality(dbm) -> str:
        """Gibt eine lesbare Qualitaetsstufe zurueck."""
        if dbm is None:      return "?"
        if dbm >= -70:       return "ausgezeichnet"
        if dbm >= -80:       return "gut"
        if dbm >= -90:       return "maessig"
        if dbm >= -100:      return "schwach"
        return "sehr schwach"

    @staticmethod
    def rssi_color(dbm) -> str:
        """Gibt eine Hex-Farbe fuer die RSSI-Ampel zurueck."""
        if dbm is None:      return "#757575"  # grau
        if dbm >= -70:       return "#a6e3a1"  # gruen
        if dbm >= -80:       return "#f9e2af"  # gelb
        if dbm >= -90:       return "#fab387"  # orange
        return "#f38ba8"                        # rot

class CallControlListener(HSTRPListener):
    CALL_TYPES = {1: "Gruppe", 2: "Privat", 3: "All-Call"}
    TX_STATUS  = {0: "Idle", 1: "Beginn", 2: "Ende", 3: "Unterbrochen"}

    def __init__(self, port, slot_name, event_cb, hb_cb=None, rrs_cb=None, rssi_cb=None):
        super().__init__(port, slot_name, hb_cb)
        self.slot_name = slot_name
        self.event_cb  = event_cb
        self.rrs_cb    = rrs_cb   # callback(RadioStatus)
        self.rssi_cb   = rssi_cb  # callback(radio_id, rssi_dbm, slot)


    def on_packet(self, payload, addr):
        if len(payload) < 5: return
        try:
            msghdr  = payload[0] & 0x7F
            opcode  = struct.unpack_from('<H', payload, 1)[0]
            n_bytes = struct.unpack_from('<H', payload, 3)[0]
            data    = payload[5:5+n_bytes] if n_bytes > 0 else payload[5:]

            # RCP_REPEATER_BROADCAST_TX_STATUS (0xB845)
            if opcode == 0xB845 and len(data) >= 16:
                mode, status, svctype, call_type, target_id, sender_id = \
                    struct.unpack_from('<HHHH II', data)

                # RSSI: Bytes 16-17 (signed int16, Hytera-Einheit: -0.5 dBm pro Schritt)
                # Fallback: Bytes 16 als uint8 (0-255 Skala, typisch)
                rssi_dbm = None
                if len(data) >= 18:
                    raw = struct.unpack_from('<h', data, 16)[0]  # signed int16
                    if raw != 0 and raw != -1:
                        # Hytera kodiert RSSI als halbe dBm-Schritte ab -130 dBm
                        rssi_dbm = max(-130, min(0, -raw // 2)) if raw > 0 else raw
                        if not (-130 <= rssi_dbm <= 0):
                            rssi_dbm = None  # Ungueltig
                elif len(data) >= 17:
                    raw = data[16]
                    if 0 < raw < 200:
                        rssi_dbm = -130 + raw   # 0-200 Scale → -130..-(-70+) dBm

                ev = CallEvent(
                    slot      = self.slot_name,
                    call_type = self.CALL_TYPES.get(call_type, f"Typ {call_type}"),
                    sender_id = sender_id,
                    target_id = target_id,
                    status    = "start" if status == 1 else "end",
                    rssi      = rssi_dbm,
                )
                self.event_cb(ev)
                if rssi_dbm is not None and self.rssi_cb and status == 1:
                    self.rssi_cb(sender_id, rssi_dbm, self.slot_name)
                # Debug-Dump fuer Kalibrierung
                log.debug(f"[CC-{self.slot_name}] 0xB845 raw={data.hex()} rssi={rssi_dbm}")

            # RCP_BROADCAST_TX_STATUS (0xB843)
            elif opcode == 0xB843 and len(data) >= 10:
                process, source, call_type, target_id = \
                    struct.unpack_from('<HHHI', data)
                ev = CallEvent(
                    slot      = self.slot_name,
                    call_type = self.CALL_TYPES.get(call_type, f"Typ {call_type}"),
                    sender_id = source,
                    target_id = target_id,
                    status    = "start" if process == 1 else "end"
                )
                self.event_cb(ev)

            # RRS auf CC-Ports: NUR echte Register-Pakete (0x0003) mit Nutzdaten!
            # 0x0004 (ACK) hat n_bytes=0 und erzeugt sonst Phantom-IDs
            elif opcode in (0x0003, 0x0001) and self.rrs_cb and n_bytes >= 4:
                online = (opcode == 0x0003)
                rid = None
                for fmt, off in (('>I', 0), ('<I', 0)):
                    if len(data) >= off + 4:
                        val = struct.unpack_from(fmt, data, off)[0]
                        cand = val & 0xFFFFFF
                        if 1000 <= cand <= 16776415:
                            rid = cand
                            break
                if rid:
                    self.rrs_cb(RadioStatus(rid, online))
                    log.info(f"[CC-{self.slot_name}] RRS 0x{opcode:04X} radio_id={rid} online={online}")

        except Exception as e:
            log.debug(f"[CC-{self.slot_name}] Parse-Fehler: {e}")


# ── GPS/GNSS-Listener ─────────────────────────────────────
class GPSFix:
    __slots__ = ('ts','radio_id','lat','lon','speed','heading','accuracy')
    def __init__(self, radio_id, lat, lon, speed=0, heading=0, accuracy=0):
        self.ts       = datetime.now()
        self.radio_id = radio_id
        self.lat, self.lon = lat, lon
        self.speed    = speed
        self.heading  = heading
        self.accuracy = accuracy

class GNSSListener(HSTRPListener):
    def __init__(self, port, slot_name, gps_cb, hb_cb=None, extra_ports=None):
        super().__init__(port, slot_name, hb_cb, extra_ports)
        self.slot_name = slot_name
        self.gps_cb    = gps_cb

    def on_packet(self, payload, addr):
        # Hytera GNSS payload: variabel, enthält Lat/Lon als int32 (×1e-7)
        if len(payload) < 20: return
        try:
            # Standard-Offset bei Hytera GNSS nach TxCtrl-Header
            msghdr = payload[0] & 0x7F
            opcode = struct.unpack_from('<H', payload, 1)[0]
            n_bytes = struct.unpack_from('<H', payload, 3)[0]
            data = payload[5:5+n_bytes]
            if len(data) < 16: return

            radio_ip  = struct.unpack_from('>I', data, 0)[0]
            radio_id  = radio_ip & 0xFFFFFF
            lat_raw   = struct.unpack_from('>i', data, 4)[0]
            lon_raw   = struct.unpack_from('>i', data, 8)[0]
            lat = lat_raw / 1e7
            lon = lon_raw / 1e7
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                self.gps_cb(GPSFix(radio_id, lat, lon))
        except Exception as e:
            log.debug(f"[GNSS] {e}")


# ── SMS/TMP-Listener ──────────────────────────────────────
class SMSMessage:
    __slots__ = ('ts','sender_id','target_id','text','is_group')
    def __init__(self, sender_id, target_id, text, is_group=False):
        self.ts = datetime.now()
        self.sender_id = sender_id
        self.target_id = target_id
        self.text      = text
        self.is_group  = is_group

class SMSListener(HSTRPListener):
    def __init__(self, port, slot_name, sms_cb, hb_cb=None, extra_ports=None):
        super().__init__(port, slot_name, hb_cb, extra_ports)
        self.slot_name = slot_name
        self.sms_cb    = sms_cb

    def on_packet(self, payload, addr):
        if len(payload) < 20: return
        try:
            msghdr = payload[0] & 0x7F
            opcode = struct.unpack_from('<H', payload, 1)[0]
            n_bytes = struct.unpack_from('<H', payload, 3)[0]
            data = payload[5:5+n_bytes]
            if len(data) < 13: return

            # TMP opcodes:
            # 0x00A1 = TMP_PRIVATE_NEED_ACK  (Privat mit ACK)
            # 0x80A1 = TMP_PRIVATE_NO_NEED_ACK (Privat ohne ACK)
            # 0x00B1 = TMP_GROUP             (Gruppe mit ACK)
            if opcode not in (0x00A1, 0x80A1, 0x00B1):
                return
            is_group = (opcode == 0x00B1)
            msg_seq, dest_ip, src_ip = struct.unpack_from('>III', data, 0)
            src_id  = src_ip  & 0xFFFFFF
            dest_id = dest_ip & 0xFFFFFF
            text = data[12:].decode('utf-16-le', errors='replace').rstrip('\x00')
            if text:
                self.sms_cb(SMSMessage(src_id, dest_id, text, is_group))
        except Exception as e:
            log.debug(f"[SMS] {e}")


# ── RRS-Listener (An-/Abmeldung) ──────────────────────────
class RadioStatus:
    __slots__ = ('ts','radio_id','online')
    def __init__(self, radio_id, online):
        self.ts = datetime.now()
        self.radio_id = radio_id
        self.online   = online

class RRSListener(HSTRPListener):
    def __init__(self, port, slot_name, status_cb, hb_cb=None, extra_ports=None):
        super().__init__(port, slot_name, hb_cb, extra_ports)
        self.slot_name = slot_name
        self.status_cb = status_cb

    def on_packet(self, payload, addr):
        if len(payload) < 12: return
        try:
            msghdr = payload[0] & 0x7F
            opcode = struct.unpack_from('<H', payload, 1)[0]
            n_bytes = struct.unpack_from('<H', payload, 3)[0]
            data = payload[5:5+n_bytes]
            if len(data) < 4: return

            radio_ip = struct.unpack_from('>I', data, 0)[0]
            radio_id = radio_ip & 0xFFFFFF
            online   = (opcode == 0x0003)  # 0x0003=Register, 0x0001=Offline
            self.status_cb(RadioStatus(radio_id, online))
        except Exception as e:
            log.debug(f"[RRS] {e}")


# ── Telemetrie-Listener ───────────────────────────────────
class TelemetryEvent:
    __slots__ = ('ts','radio_id','channel','value','analog')
    def __init__(self, radio_id, channel, value, analog=False):
        self.ts = datetime.now()
        self.radio_id = radio_id
        self.channel  = channel
        self.value    = value
        self.analog   = analog

class TelemetryListener(HSTRPListener):
    def __init__(self, port, slot_name, tele_cb, hb_cb=None, extra_ports=None):
        super().__init__(port, slot_name, hb_cb, extra_ports)
        self.slot_name = slot_name
        self.tele_cb   = tele_cb

    def on_packet(self, payload, addr):
        if len(payload) < 12: return
        try:
            msghdr  = payload[0] & 0x7F
            opcode  = struct.unpack_from('<H', payload, 1)[0]
            n_bytes = struct.unpack_from('<H', payload, 3)[0]
            data    = payload[5:5+n_bytes]
            if len(data) < 6: return
            radio_ip = struct.unpack_from('>I', data, 0)[0]
            radio_id = radio_ip & 0xFFFFFF
            channel  = data[4] if len(data) > 4 else 0
            value    = data[5] if len(data) > 5 else 0
            self.tele_cb(TelemetryEvent(radio_id, channel, value))
        except Exception as e:
            log.debug("[Tele] %s" % e)
