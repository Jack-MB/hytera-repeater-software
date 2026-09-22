"""
ptt_full_test.py - Vollstaendiger PTT Handshake Test
"""
import socket, threading, struct, time, sys, os
sys.stdout = __import__('io').TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = __import__('io').TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

REPEATER_IP  = "192.168.1.100"
MY_TX_ID     = 100    # Unsere Dispatcher Radio-ID
TARGET_TG    = 1      # Talkgruppe
CALL_TYPE    = 1      # 1=Group, 2=Private
AUDIO_DUR    = 3      # Sekunden Audio senden

HSTRP_SIG      = b'\x32\x42\x00'
HSTRP_TO_RADIO = 0x00
HSTRP_ACK      = 0x01
HSTRP_HEARTBEAT= 0x02
HSTRP_SYNACK   = 0x05
HSTRP_FROM_RAD = 0x20
HSTRP_SYN      = 0x24

PORTS = {
    30009: ("CC",  "TS1"),
    30010: ("CC",  "TS2"),
    30012: ("AUD", "TS1"),
    30014: ("AUD", "TS2"),
}

def log(s): print(s, flush=True)

def make_hstrp(ptype, seqid=0, payload=b''):
    return HSTRP_SIG + struct.pack('>BH', ptype, seqid) + payload

class Port:
    def __init__(self, port, ptype, slot):
        self.port  = port
        self.ptype = ptype
        self.slot  = slot
        self.seq   = 0
        self.sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", port))
        self.sock.settimeout(0.3)
        self.peer  = (REPEATER_IP, port)
        self.handshake_done = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        log(f"  ✓ {ptype}-{slot} Port {port} offen")

    def _next_seq(self):
        self.seq = (self.seq + 1) & 0xFFFF
        return self.seq

    def send(self, pkt):
        self.sock.sendto(pkt, self.peer)

    def _run(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
                self.peer = addr
                if len(data) >= 6 and data[:3] == HSTRP_SIG:
                    pt  = data[3]
                    seq = struct.unpack_from('>H', data, 4)[0]
                    if pt == HSTRP_SYN:
                        ack = make_hstrp(HSTRP_SYNACK, self._next_seq())
                        self.sock.sendto(ack, addr)
                        self.handshake_done = True
                        log(f"  ✓ SYN-ACK {self.ptype}-{self.slot} (Port {self.port})")
                    elif pt == HSTRP_HEARTBEAT:
                        hb = make_hstrp(HSTRP_HEARTBEAT, 0)
                        self.sock.sendto(hb, addr)
                    elif pt == HSTRP_ACK:
                        log(f"  ✓ ACK empfangen {self.ptype}-{self.slot}")
                    elif pt == HSTRP_FROM_RAD:
                        # Daten vom Repeater - loggen was es ist
                        payload = data[6:]
                        if len(payload) >= 5:
                            op = struct.unpack_from('<H', payload, 1)[0]
                            log(f"  << {self.ptype}-{self.slot} FROM_RADIO opcode=0x{op:04X}")
                        self.sock.sendto(make_hstrp(HSTRP_ACK, seq), addr)
            except socket.timeout: continue
            except: break

    def send_cc_tx_req(self, active=True):
        status = 1 if active else 2
        data = struct.pack('<IIII', CALL_TYPE, TARGET_TG, MY_TX_ID, status)
        rcp  = struct.pack('<BHH', 0x00, 0x0840, len(data)) + data
        pkt  = make_hstrp(HSTRP_TO_RADIO, self._next_seq(), rcp)
        self.send(pkt)
        action = "START" if active else "STOP"
        log(f"  >> CC_TX_REQ {action} -> TG {TARGET_TG}, ID {MY_TX_ID}")
        log(f"     Bytes ({len(pkt)}): {pkt.hex()}")

    def send_silence_rtp(self, duration_sec):
        # G.711 µ-law Stille = 0xFF bytes, 160 Bytes = 20ms @ 8kHz
        FRAME = 160
        frame_count = int(duration_sec * 1000 / 20)
        silence = bytes([0xFF] * FRAME)
        ssrc = 0x12345678
        seq  = 0
        ts   = 0
        PT   = 0  # PCMU
        log(f"  >> Sende {frame_count} RTP Stille-Frames ({duration_sec}s)...")
        for i in range(frame_count):
            # RTP ohne Hytera Extension (erstmal simpel testen)
            hdr = struct.pack('>BBHII', 0x80, PT, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc)
            self.send(hdr + silence)
            seq += 1
            ts  += FRAME
            time.sleep(0.02)
        log(f"  >> Audio fertig")


log("=== Hytera PTT Full Test ===")
log(f"Repeater: {REPEATER_IP}, TG: {TARGET_TG}, ID: {MY_TX_ID}")
log("")

# Ports öffnen
ports = {}
for p,(pt,sl) in PORTS.items():
    try:
        ports[p] = Port(p, pt, sl)
    except Exception as e:
        log(f"  ✗ Port {p}: {e}")

log("")
log("Warte 5s auf SYN Handshake vom Repeater...")
time.sleep(5)

cc1 = ports.get(30009)
aud1 = ports.get(30012)

if not cc1 or not cc1.handshake_done:
    log("WARNUNG: Kein Handshake auf CC-Port 30009! Sende trotzdem...")
else:
    log("Handshake etabliert - sende CC_TX_REQ...")

log("")
log("--- Phase 1: CC_TX_REQ START ---")
if cc1: cc1.send_cc_tx_req(active=True)

log("")
log("--- Phase 2: Warte 200ms dann Audio senden ---")
time.sleep(0.2)

if aud1:
    aud1.send_silence_rtp(AUDIO_DUR)
else:
    log("FEHLER: Kein Audio-Socket!")
    time.sleep(AUDIO_DUR)

log("")
log("--- Phase 3: CC_TX_REQ STOP ---")
if cc1: cc1.send_cc_tx_req(active=False)

log("")
log("Test abgeschlossen. Empfange noch 3s Antworten...")
time.sleep(3)
log("Fertig!")
