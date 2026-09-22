# -*- coding: utf-8 -*-
"""
ptt_diagnose.py – PTT Sende-Diagnose für Defined Mode
Zeigt ALLE Pakete auf Call-Control und Voice-Ports.
Sendet nach 5 Sekunden einen Test-CC_TX_REQ und Audio, damit wir sehen was passiert.
Strg+C zum Beenden.
"""
import sys, io, socket, threading, struct, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

REPEATER_IP  = "192.168.1.100"
MY_IP        = ""   # leer = automatisch

PORTS = {
    30009: "CC-TS1",
    30010: "CC-TS2",
    30012: "AUDIO-TS1",
    30014: "AUDIO-TS2",
}

HSTRP_SIG = b'\x32\x42\x00'
HSTRP_TYPES = {0x00:"TO_RADIO",0x01:"ACK",0x02:"HEARTBEAT",0x05:"SYN-ACK",0x20:"FROM_RADIO",0x24:"SYN"}
RCP_OPCODES = {0x0840:"CC_TX_REQ",0xB843:"CC_TX_STATUS",0xB845:"CC_REPEATER_TX_STATUS"}

lock = threading.Lock()

def ts(): return time.strftime("%H:%M:%S")

def hex_short(data, n=32):
    return ' '.join(f'{b:02X}' for b in data[:n]) + ('...' if len(data) > n else '')

def parse(data, port):
    if len(data) >= 6 and data[:3] == HSTRP_SIG:
        ptype = data[3]
        seqid = struct.unpack_from('>H', data, 4)[0]
        payload = data[6:]
        pname = HSTRP_TYPES.get(ptype, f'0x{ptype:02X}')
        info = f"HSTRP [{pname}] seq={seqid}"
        if len(payload) >= 5 and ptype in (0x00, 0x20):
            opcode = struct.unpack_from('<H', payload, 1)[0]
            oname  = RCP_OPCODES.get(opcode, f'RCP_0x{opcode:04X}')
            info  += f" | {oname}"
        return info
    elif len(data) >= 12 and (data[0] >> 6) == 2:
        pt = data[1] & 0x7F
        seq = struct.unpack_from('>H', data, 2)[0]
        return f"RTP pt={pt} seq={seq} len={len(data)}"
    else:
        return f"RAW {hex_short(data, 16)}"

# Sockets erstellen
sockets = {}
for port in PORTS:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        s.settimeout(0.5)
        sockets[port] = s
        print(f"  ✓ Lausche auf Port {port} ({PORTS[port]})")
    except Exception as e:
        print(f"  ✗ Port {port}: {e}")

print()
print("═" * 60)
print("  Warte auf Pakete... (Funke drücken zum Testen)")
print("  In 8 Sek. wird automatisch ein Test-PTT gesendet")
print("═" * 60)

peer_by_port = {}   # port -> addr des Repeaters

def listen(port, sock):
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            peer_by_port[port] = addr
            info = parse(data, port)
            with lock:
                print(f"[{ts()}] ← EMPFANGEN Port {port} ({PORTS[port]}) von {addr[0]}: {info}")
        except socket.timeout:
            continue
        except: break

for port, sock in sockets.items():
    t = threading.Thread(target=listen, args=(port, sock), daemon=True)
    t.start()

# Warte 8 Sekunden, dann sende Test-PTT
time.sleep(8)
print()
print("═" * 60)
print("  SENDE TEST-CC_TX_REQ auf Port 30009 (TS1, TG 1)...")
print("═" * 60)

def make_hstrp(ptype, seqid=0, payload=b''):
    return HSTRP_SIG + struct.pack('>BH', ptype, seqid) + payload

def send_cc_tx_req(sock, addr, active=True, tg=1, sender=100, call_type=1):
    status = 1 if active else 2
    data   = struct.pack('<IIII', call_type, tg, sender, status)
    rcp    = struct.pack('<BHH', 0x00, 0x0840, len(data)) + data
    pkt    = make_hstrp(0x00, 1, rcp)
    sock.sendto(pkt, addr)
    print(f"  → CC_TX_REQ gesendet an {addr[0]}:{addr[1]}, TG={tg}, Status={'START' if active else 'STOP'}")
    print(f"    Bytes: {hex_short(pkt)}")

cc_sock = sockets.get(30009)
if cc_sock:
    addr = peer_by_port.get(30009, (REPEATER_IP, 30009))
    send_cc_tx_req(cc_sock, addr, active=True, tg=1)
    
    # Warte 1 Sekunde auf ACK
    print()
    print("  Warte auf ACK vom Repeater...")
    time.sleep(3)
    
    # Sende auch einen STOP
    print()
    send_cc_tx_req(cc_sock, addr, active=False, tg=1)
else:
    print("  ✗ Kein Socket auf 30009 verfügbar!")

print()
print("Weiter lauschen... Strg+C zum Beenden")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nBeendet.")
    for s in sockets.values():
        try: s.close()
        except: pass
