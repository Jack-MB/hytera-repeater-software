import socket, threading, struct, time, sys

REPEATER_IP = "192.168.1.100"
PORTS = {30009:"CC-TS1", 30010:"CC-TS2", 30012:"AUDIO-TS1", 30014:"AUDIO-TS2"}
HSTRP_SIG = b'\x32\x42\x00'
RCP_NAMES = {0x0840:"CC_TX_REQ", 0xB843:"CC_TX_STATUS", 0xB845:"CC_REPEATER_TX_STATUS"}
HSTRP_T = {0x00:"TO_RADIO",0x01:"ACK",0x02:"HB",0x05:"SYN-ACK",0x20:"FROM_RADIO",0x24:"SYN"}

def log(s): print(s, flush=True)

def parse(data):
    if len(data)>=6 and data[:3]==HSTRP_SIG:
        pt=data[3]; seq=struct.unpack_from('>H',data,4)[0]
        name=HSTRP_T.get(pt,f'0x{pt:02X}')
        s=f"HSTRP[{name}] seq={seq}"
        if len(data)>11 and pt in(0x00,0x20):
            op=struct.unpack_from('<H',data,7)[0]
            s+=f" RCP={RCP_NAMES.get(op,f'0x{op:04X}')}"
        return s
    elif len(data)>=12 and (data[0]>>6)==2:
        return f"RTP pt={data[1]&0x7F} seq={struct.unpack_from('>H',data,2)[0]} len={len(data)}"
    return f"RAW[{len(data)}] {data[:8].hex()}"

peers = {}
socks = {}

log("=== PTT Diagnose ===")
for p,n in PORTS.items():
    try:
        s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,1)
        s.bind(("0.0.0.0",p))
        s.settimeout(0.3)
        socks[p]=s
        log(f"OK Port {p} ({n})")
    except Exception as e:
        log(f"FEHLER Port {p}: {e}")

log("Warte auf Pakete - drück jetzt PTT am Funkgeraet...")
log("In 10 Sek: Test-CC_TX_REQ wird gesendet")

def listen(port, sock, name):
    while True:
        try:
            data,addr=sock.recvfrom(4096)
            peers[port]=addr
            log(f"<< Port {port}({name}) von {addr[0]}:{addr[1]}: {parse(data)}")
        except socket.timeout: continue
        except: break

for p,s in socks.items():
    threading.Thread(target=listen, args=(p,s,PORTS[p]), daemon=True).start()

time.sleep(10)

log("\n--- Sende Test CC_TX_REQ (TG=1, ID=100) ---")
cc = socks.get(30009)
if cc:
    addr = peers.get(30009, (REPEATER_IP, 30009))
    log(f"Ziel: {addr[0]}:{addr[1]}")
    data = struct.pack('<IIII', 1, 1, 100, 1)           # call_type=1, tg=1, sender=100, status=start
    rcp  = struct.pack('<BHH', 0x00, 0x0840, len(data)) + data
    pkt  = HSTRP_SIG + struct.pack('>BH', 0x00, 1) + rcp
    cc.sendto(pkt, addr)
    log(f">> CC_TX_REQ gesendet ({len(pkt)} Bytes): {pkt.hex()}")
    time.sleep(3)
    data2 = struct.pack('<IIII', 1, 1, 100, 2)          # status=stop
    rcp2  = struct.pack('<BHH', 0x00, 0x0840, len(data2)) + data2
    pkt2  = HSTRP_SIG + struct.pack('>BH', 0x00, 2) + rcp2
    cc.sendto(pkt2, addr)
    log(f">> CC_TX_REQ STOP gesendet")
else:
    log("FEHLER: Kein Socket auf Port 30009!")

log("\nLausche weiter... Strg+C zum Beenden")
try:
    while True: time.sleep(1)
except KeyboardInterrupt:
    log("Ende.")
