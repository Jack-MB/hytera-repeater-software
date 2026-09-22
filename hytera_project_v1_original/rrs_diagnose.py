# -*- coding: utf-8 -*-
import sys, io, socket, threading, struct, time
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

"""
rrs_diagnose.py  -  Hytera RRS / Call-Control Paket-Analyse
Lauscht auf allen relevanten Ports und zeigt den kompletten Paket-Inhalt.
Strc+C zum Beenden.
"""

HSTRP_SIG = b'\x32\x42\x00'

WATCH_PORTS = {
    30001: "RRS  TS1",
    30002: "RRS  TS2",
    30009: "CC   TS1",
    30010: "CC   TS2",
    30003: "GPS  TS1",
    30004: "GPS  TS2",
    30012: "AUDIO TS1",
    30014: "AUDIO TS2",
}

HSTRP_TYPES = {
    0x00: "TO_RADIO",
    0x01: "ACK",
    0x02: "HEARTBEAT",
    0x05: "SYN-ACK",
    0x20: "FROM_RADIO",
    0x24: "SYN",
}

RCP_OPCODES = {
    0x0001: "RRS_OFFLINE",
    0x0002: "RRS_QUERY",
    0x0003: "RRS_REGISTER",
    0x0004: "RRS_REGISTER_ACK",
    0x0840: "CC_TX_REQ",
    0xB843: "CC_TX_STATUS",
    0xB845: "CC_REPEATER_TX_STATUS",
    0x00A1: "SMS_PRIVATE_ACK",
    0x80A1: "SMS_PRIVATE_NO_ACK",
    0x00B1: "SMS_GROUP",
}

lock = threading.Lock()

def hex_dump(data, indent=12):
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part  = ' '.join(f'{b:02X}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f"{' '*indent}{i:04X}:  {hex_part:<48}  |{ascii_part}|")
    return '\n'.join(lines)

def parse_hstrp(data):
    if len(data) < 6 or data[:3] != HSTRP_SIG:
        return None
    ptype   = data[3]
    seqid   = struct.unpack_from('>H', data, 4)[0]
    payload = data[6:]
    return {
        'ptype':      ptype,
        'ptype_name': HSTRP_TYPES.get(ptype, f'UNK(0x{ptype:02X})'),
        'seqid':      seqid,
        'payload':    payload,
    }

def parse_rcp(payload):
    if len(payload) < 5:
        return None
    msghdr  = payload[0]
    opcode  = struct.unpack_from('<H', payload, 1)[0]
    n_bytes = struct.unpack_from('<H', payload, 3)[0]
    data    = payload[5:5+n_bytes]
    return {
        'msghdr':      msghdr,
        'opcode':      opcode,
        'opcode_name': RCP_OPCODES.get(opcode, f'UNBEKANNT(0x{opcode:04X})'),
        'n_bytes':     n_bytes,
        'data':        data,
    }

def try_radio_ids(data):
    """Versucht Radio-IDs aus rohen Bytes zu lesen (verschiedene Interpretationen)."""
    results = []
    for label, fmt, offset in [
        ("BE[0]", '>I', 0), ("LE[0]", '<I', 0),
        ("BE[4]", '>I', 4), ("LE[4]", '<I', 4),
    ]:
        if len(data) >= offset + 4:
            val = struct.unpack_from(fmt, data, offset)[0]
            rid = val & 0xFFFFFF
            if 100 <= rid <= 16776415:
                results.append(f"{label}={rid}")
    return results

def listen(port, label):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        s.settimeout(1.0)
        with lock:
            print(f"  [OK]  Port {port}  [{label}]")
    except OSError as e:
        with lock:
            print(f"  [ERR] Port {port}  [{label}] : {e}")
        return

    while True:
        try:
            data, addr = s.recvfrom(4096)
            now = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            with lock:
                print()
                print("-" * 70)
                print(f"[{now}]  Port {port} [{label}]  Von:{addr[0]}:{addr[1]}  {len(data)} Bytes")

                hstrp = parse_hstrp(data)
                if hstrp:
                    print(f"  HSTRP  type=0x{hstrp['ptype']:02X} ({hstrp['ptype_name']})  seq={hstrp['seqid']}")
                    payload = hstrp['payload']

                    if hstrp['ptype'] in (0x20, 0x00):
                        rcp = parse_rcp(payload)
                        if rcp:
                            print(f"  RCP    opcode=0x{rcp['opcode']:04X} ({rcp['opcode_name']})  "
                                  f"msghdr=0x{rcp['msghdr']:02X}  n_bytes={rcp['n_bytes']}")
                            if rcp['data']:
                                ids = try_radio_ids(rcp['data'])
                                if ids:
                                    print(f"  >> Radio-ID Kandidaten: {' | '.join(ids)}")
                                print(f"  RCP-Payload ({len(rcp['data'])} Bytes):")
                                print(hex_dump(rcp['data']))
                        else:
                            print(f"  HSTRP-Payload ({len(payload)} Bytes):")
                            print(hex_dump(payload))
                    else:
                        if len(payload) > 0:
                            print(f"  Payload ({len(payload)} Bytes):")
                            print(hex_dump(payload))
                else:
                    # Kein HSTRP - koennte RTP oder roh sein
                    rtp_version = (data[0] >> 6) & 0x3 if data else -1
                    if rtp_version == 2:
                        print(f"  RTP-Paket  (V=2)  {len(data)} Bytes")
                    else:
                        print(f"  Unbekanntes Format:  {data[:4].hex()}...")
                    print(hex_dump(data[:64]))  # max. 64 Bytes

        except socket.timeout:
            pass
        except Exception as e:
            with lock:
                print(f"  [Fehler Port {port}] {e}")
            break


if __name__ == "__main__":
    print("=" * 70)
    print("  Hytera Paket-Diagnose  (RRS + Call-Control + GPS)")
    print()
    print("  ANLEITUNG:")
    print("  1. Dieses Script starten (Haupt-App dabei schliessen!)")
    print("  2. Funkgeraet einschalten oder PTT druecken")
    print("  3. Ausgabe unten ablesen und mir zeigen")
    print()
    print("  Strg+C zum Beenden.")
    print("=" * 70)
    print()

    threads = []
    for port, label in WATCH_PORTS.items():
        t = threading.Thread(target=listen, args=(port, label), daemon=True)
        t.start()
        threads.append(t)

    print()
    print("  Warte auf Pakete vom Repeater (192.168.1.100)...")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n  Diagnose beendet.")
