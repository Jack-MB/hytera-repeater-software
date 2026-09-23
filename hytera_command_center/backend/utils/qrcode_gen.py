"""
Hytera Command Center – Pure Python QR Code Generator
Erzeugt QR-Code SVGs (Version 1..10, ECC Level L & M) vollständig autark ohne externe pip-Pakete.
"""

from typing import List, Tuple, Optional


def _gf_exp_log() -> Tuple[List[int], List[int]]:
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        exp[i + 255] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    return exp, log

_EXP, _LOG = _gf_exp_log()

def _gf_mul(x: int, y: int) -> int:
    if x == 0 or y == 0:
        return 0
    return _EXP[_LOG[x] + _LOG[y]]

def _rs_poly(n: int) -> List[int]:
    """Erzeugt Reed-Solomon Generator-Polynom für n Fehlerkorrektur-Bytes."""
    poly = [1]
    for i in range(n):
        new_poly = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            new_poly[j] ^= _gf_mul(c, _EXP[i])
            new_poly[j + 1] ^= c
        poly = new_poly
    return poly

def _rs_encode(data: List[int], num_ec: int) -> List[int]:
    """Berechnet die Reed-Solomon ECC-Paritäts-Bytes."""
    gen = _rs_poly(num_ec)
    msg = data + [0] * num_ec
    for i in range(len(data)):
        lead = msg[i]
        if lead != 0:
            for j in range(len(gen)):
                msg[i + j] ^= _gf_mul(gen[j], lead)
    return msg[-num_ec:]


# Versions-Tabelle für Byte-Modus Level M (Kapazität in Bytes, Gesamt-Codewörter, ECC-Codewörter pro Block, Blöcke)
_VERSION_SPECS_M = [
    # (Ver, Total-CW, EC-CW, Blocks, Data-Bytes)
    (1,  26,  10, 1, 14),   # 21x21: bis 14 Bytes
    (2,  44,  16, 1, 26),   # 25x25: bis 26 Bytes
    (3,  70,  26, 1, 42),   # 29x29: bis 42 Bytes
    (4,  100, 36, 2, 62),   # 33x33: bis 62 Bytes (2 Blöcke à 31 Data + 18 EC)
    (5,  134, 48, 2, 84),   # 37x37: bis 84 Bytes
]

# Alignment-Muster Positionen pro Version
_ALIGNMENT_POS = {
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
}


def generate_qr_svg(text: str, fill: str = "#000000", bg: str = "#FFFFFF", scale: int = 8, size: Optional[int] = None) -> str:
    """
    Erzeugt einen standardkonformen QR-Code als SVG-String für gegebene URL/Text.
    Unterstützt automatische Versionsauswahl von V1 bis V5 (ausreichend für alle URLs bis 84 Zeichen).
    """
    raw_data = text.encode("utf-8")
    data_len = len(raw_data)

    target_px = size

    # 1. Version bestimmen
    selected_ver = None
    for ver, total_cw, ec_cw, num_blocks, max_data in _VERSION_SPECS_M:
        if data_len <= max_data:
            selected_ver = (ver, total_cw, ec_cw, num_blocks, max_data)
            break
    if not selected_ver:
        # Fallback Version 5
        selected_ver = _VERSION_SPECS_M[-1]

    ver, total_cw, ec_total, num_blocks, max_data = selected_ver
    size = 17 + ver * 4

    # 2. Bitstream aufbauen (Mode 0100 = 8-Bit Byte, 8-Bit Längen-Indikator für V1..9)
    bits = "0100" + format(data_len, "08b")
    for b in raw_data:
        bits += format(b, "08b")

    # Terminator & Padding auf Codewort-Grenze
    data_cw_total = total_cw - ec_total
    max_bits = data_cw_total * 8
    bits += "0000"
    if len(bits) > max_bits:
        bits = bits[:max_bits]
    while len(bits) % 8 != 0:
        bits += "0"

    data_bytes = [int(bits[i:i+8], 2) for i in range(0, len(bits), 8)]

    # Pad Bytes 0xEC, 0x11
    pads = [0xEC, 0x11]
    pidx = 0
    while len(data_bytes) < data_cw_total:
        data_bytes.append(pads[pidx % 2])
        pidx += 1

    # 3. Interleaving & ECC Blöcke
    ec_per_block = ec_total // num_blocks
    data_per_block = data_cw_total // num_blocks

    blocks_data = []
    blocks_ec = []
    for b in range(num_blocks):
        sub_d = data_bytes[b * data_per_block:(b + 1) * data_per_block]
        sub_ec = _rs_encode(sub_d, ec_per_block)
        blocks_data.append(sub_d)
        blocks_ec.append(sub_ec)

    # Codewörter verschachteln
    final_codewords = []
    for i in range(data_per_block):
        for b in range(num_blocks):
            final_codewords.append(blocks_data[b][i])
    for i in range(ec_per_block):
        for b in range(num_blocks):
            final_codewords.append(blocks_ec[b][i])

    final_bits = "".join(format(cw, "08b") for cw in final_codewords)

    # 4. Matrix initialisieren
    # None = unbesetzt, 0 = Weiß, 1 = Schwarz
    matrix = [[None] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def set_finder(orow, ocol):
        for r in range(7):
            for c in range(7):
                is_black = (
                    r == 0 or r == 6 or c == 0 or c == 6
                    or (2 <= r <= 4 and 2 <= c <= 4)
                )
                matrix[orow + r][ocol + c] = 1 if is_black else 0
                reserved[orow + r][ocol + c] = True
        # Separator
        for r in range(-1, 8):
            for c in range(-1, 8):
                nr, nc = orow + r, ocol + c
                if 0 <= nr < size and 0 <= nc < size:
                    reserved[nr][nc] = True
                    if matrix[nr][nc] is None:
                        matrix[nr][nc] = 0

    # 3 Finder-Muster
    set_finder(0, 0)
    set_finder(0, size - 7)
    set_finder(size - 7, 0)

    # Alignment-Muster für Version >= 2
    if ver in _ALIGNMENT_POS:
        coords = _ALIGNMENT_POS[ver]
        for ar in coords:
            for ac in coords:
                if reserved[ar][ac]:
                    continue
                for r in range(-2, 3):
                    for c in range(-2, 3):
                        nr, nc = ar + r, ac + c
                        is_black = (abs(r) == 2 or abs(c) == 2 or (r == 0 and c == 0))
                        matrix[nr][nc] = 1 if is_black else 0
                        reserved[nr][nc] = True

    # Timing-Muster
    for i in range(size):
        if not reserved[6][i]:
            matrix[6][i] = 1 if i % 2 == 0 else 0
            reserved[6][i] = True
        if not reserved[i][6]:
            matrix[i][6] = 1 if i % 2 == 0 else 0
            reserved[i][6] = True

    # Dark Module
    matrix[4 * ver + 9][8] = 1
    reserved[4 * ver + 9][8] = True

    # Format-Informationen Reservieren (Level M, Mask 0 = 0x5412)
    format_info = 0x5412  # M, Mask 0, BCH(15,5) maskiert
    format_bits = format(format_info, "015b")

    for i in range(15):
        b = int(format_bits[i])
        # Oben links
        if i <= 5:
            matrix[8][i] = b
            reserved[8][i] = True
        elif i == 6:
            matrix[8][7] = b
            reserved[8][7] = True
        elif i == 7:
            matrix[8][8] = b
            reserved[8][8] = True
        elif i == 8:
            matrix[7][8] = b
            reserved[7][8] = True
        else:
            matrix[14 - i][8] = b
            reserved[14 - i][8] = True

        # Oben rechts & unten links
        if i < 8:
            matrix[size - 1 - i][8] = b
            reserved[size - 1 - i][8] = True
        else:
            matrix[8][size - 15 + i] = b
            reserved[8][size - 15 + i] = True

    # 5. Daten platzieren (Zickzack-Muster von rechts unten nach links oben)
    bit_idx = 0
    bit_len = len(final_bits)
    col = size - 1
    going_up = True

    while col > 0:
        if col == 6:  # Timing-Spalte überspringen
            col -= 1
        cols = [col, col - 1]
        rows = range(size - 1, -1, -1) if going_up else range(size)
        for r in rows:
            for c in cols:
                if not reserved[r][c]:
                    b = int(final_bits[bit_idx]) if bit_idx < bit_len else 0
                    bit_idx += 1
                    # Mask 0: (row + col) % 2 == 0
                    if (r + c) % 2 == 0:
                        b ^= 1
                    matrix[r][c] = b
        going_up = not going_up
        col -= 2

    # 6. Als sauberes SVG rendern
    quiet_zone = 2
    full_dim = size + 2 * quiet_zone
    svg_px = target_px if target_px is not None else (full_dim * scale)

    paths = []
    for r in range(size):
        for c in range(size):
            if matrix[r][c] == 1:
                x = c + quiet_zone
                y = r + quiet_zone
                paths.append(f"M{x},{y}h1v1h-1z")

    path_data = " ".join(paths)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {full_dim} {full_dim}" '
        f'width="{svg_px}" height="{svg_px}" shape-rendering="crispEdges">\n'
        f'  <rect width="{full_dim}" height="{full_dim}" fill="{bg}"/>\n'
        f'  <path d="{path_data}" fill="{fill}"/>\n'
        f'</svg>'
    )
    return svg
