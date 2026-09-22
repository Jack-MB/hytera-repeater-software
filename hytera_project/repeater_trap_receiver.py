# -*- coding: utf-8 -*-
"""
repeater_trap_receiver.py
=========================
Empfaengt SNMP-Traps vom Hytera HR1065 Repeater.
Zeigt VSWR-Alarm und andere Alarme in einem eigenen Fenster an.

Der Repeater sendet Traps an UDP-Port 162 wenn ein Alarm auftritt.
Das funktioniert OHNE Enhanced-Lizenz, da es ein Push-Mechanismus ist.

Voraussetzung:
    pip install pysnmp

Verwendung:
    python repeater_trap_receiver.py
    (Laeuft parallel zum Dispatcher, oder als eigenstaendiges Fenster)
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import threading
import socket
import struct
import datetime
import tkinter as tk
from tkinter import scrolledtext

# ── Hytera OID → Alarm-Name Mapping ──────────────────────────────────────────
# Aus der Hytera REPEATER MIB (1.3.6.1.4.1.40297.1.2.1.1.x)
ALARM_OID_MAP = {
    "1.3.6.1.4.1.40297.1.2.1.1.1": "Spannungs-Alarm",
    "1.3.6.1.4.1.40297.1.2.1.1.2": "Temperatur-Alarm",
    "1.3.6.1.4.1.40297.1.2.1.1.3": "Luefter-Alarm",
    "1.3.6.1.4.1.40297.1.2.1.1.4": "Vorwaertsleistung-Alarm",
    "1.3.6.1.4.1.40297.1.2.1.1.5": "Rueckwaertsleistung-Alarm",
    "1.3.6.1.4.1.40297.1.2.1.1.6": "VSWR-Alarm",
    "1.3.6.1.4.1.40297.1.2.1.1.7": "TX-PLL-Alarm",
    "1.3.6.1.4.1.40297.1.2.1.1.8": "RX-PLL-Alarm",
    "1.3.6.1.4.1.40297.1.2.1.1.9": "Batterie-Alarm",
}

ALARM_VALUES = {0: "NORMAL", 1: "ALARM"}

# ── Farben ────────────────────────────────────────────────────────────────────
C = {
    "bg":    "#0a0a0f",
    "panel": "#0d0d1a",
    "acc":   "#fce300",
    "red":   "#ff003c",
    "grn":   "#39ff14",
    "cyan":  "#00fff7",
    "sub":   "#4a4a6a",
    "fg":    "#c8c8e0",
}


# ── SNMP Trap Parser (minimale Implementierung ohne externe Bibliothek) ───────

def _parse_ber_length(data, pos):
    """Liest BER-kodierte Laenge."""
    b = data[pos]
    pos += 1
    if b < 0x80:
        return b, pos
    n = b & 0x7f
    length = int.from_bytes(data[pos:pos+n], 'big')
    return length, pos + n


def _parse_ber_oid(data, pos, length):
    """Dekodiert BER-OID zu String."""
    end = pos + length
    first = data[pos]
    oid = [first // 40, first % 40]
    pos += 1
    val = 0
    while pos < end:
        b = data[pos]
        pos += 1
        val = (val << 7) | (b & 0x7f)
        if not (b & 0x80):
            oid.append(val)
            val = 0
    return ".".join(str(x) for x in oid)


def _parse_ber_integer(data, pos, length):
    """Dekodiert BER-Integer."""
    raw = data[pos:pos+length]
    val = int.from_bytes(raw, 'big', signed=True)
    return val


def parse_snmp_trap(data):
    """
    Minimaler SNMP v1/v2c Trap Parser.
    Gibt dict mit gefundenen OIDs und Werten zurueck.
    """
    results = {
        "version": None,
        "community": None,
        "enterprise": None,
        "alarm_oids": {},
        "raw_oids": {},
    }
    try:
        pos = 0
        # SEQUENCE
        if data[pos] != 0x30:
            return results
        pos += 1
        _, pos = _parse_ber_length(data, pos)

        # Version (INTEGER)
        if data[pos] != 0x02:
            return results
        pos += 1
        vlen, pos = _parse_ber_length(data, pos)
        version = _parse_ber_integer(data, pos, vlen)
        results["version"] = version  # 0=v1, 1=v2c
        pos += vlen

        # Community String (OCTET STRING)
        if data[pos] != 0x04:
            return results
        pos += 1
        clen, pos = _parse_ber_length(data, pos)
        community = data[pos:pos+clen].decode('ascii', errors='replace')
        results["community"] = community
        pos += clen

        # PDU type: 0xA4=v1 Trap, 0xA7=v2 Inform, 0xA2=GetResponse
        pdu_type = data[pos]
        pos += 1
        _, pos = _parse_ber_length(data, pos)

        if pdu_type == 0xA4:
            # v1 Trap: enterprise OID, agent-addr, generic-trap, specific-trap, time, varbinds
            # Enterprise OID
            if data[pos] == 0x06:
                pos += 1
                elen, pos = _parse_ber_length(data, pos)
                enterprise = _parse_ber_oid(data, pos, elen)
                results["enterprise"] = enterprise
                pos += elen
            # agent-addr (4 bytes IP)
            if data[pos] == 0x40:
                pos += 1
                alen, pos = _parse_ber_length(data, pos)
                pos += alen
            # generic-trap
            if data[pos] == 0x02:
                pos += 1
                glen, pos = _parse_ber_length(data, pos)
                pos += glen
            # specific-trap
            if data[pos] == 0x02:
                pos += 1
                slen, pos = _parse_ber_length(data, pos)
                pos += slen
            # time-stamp
            if data[pos] == 0x43:
                pos += 1
                tlen, pos = _parse_ber_length(data, pos)
                pos += tlen

        elif pdu_type in (0xA7, 0xA2, 0xA0, 0xA3):
            # v2c Trap/Inform: request-id, error-status, error-index
            for _ in range(3):
                if pos < len(data) and data[pos] == 0x02:
                    pos += 1
                    l, pos = _parse_ber_length(data, pos)
                    pos += l

        # VarBindList (SEQUENCE)
        if pos >= len(data):
            return results
        if data[pos] != 0x30:
            return results
        pos += 1
        _, pos = _parse_ber_length(data, pos)

        # Einzelne VarBinds
        while pos < len(data):
            if data[pos] != 0x30:
                break
            pos += 1
            _, pos = _parse_ber_length(data, pos)

            # OID
            if pos >= len(data) or data[pos] != 0x06:
                break
            pos += 1
            oid_len, pos = _parse_ber_length(data, pos)
            oid_str = _parse_ber_oid(data, pos, oid_len)
            pos += oid_len

            # Value
            if pos >= len(data):
                break
            vtype = data[pos]
            pos += 1
            vlen, pos = _parse_ber_length(data, pos)
            raw_val = data[pos:pos+vlen]
            pos += vlen

            # Wert decodieren
            if vtype == 0x02:  # INTEGER
                val = _parse_ber_integer(raw_val, 0, vlen)
                str_val = str(val)
            elif vtype == 0x04:  # OCTET STRING
                try:
                    str_val = raw_val.decode('utf-8', errors='replace')
                except Exception:
                    str_val = raw_val.hex()
            elif vtype == 0x06:  # OID
                str_val = _parse_ber_oid(raw_val, 0, vlen)
            elif vtype == 0x43:  # TimeTicks
                ticks = int.from_bytes(raw_val, 'big')
                str_val = f"{ticks // 100}s"
            else:
                str_val = raw_val.hex()

            results["raw_oids"][oid_str] = str_val

            # Alarm-OIDs identifizieren
            for alarm_oid, alarm_name in ALARM_OID_MAP.items():
                if oid_str == alarm_oid or oid_str.startswith(alarm_oid):
                    try:
                        int_val = int(str_val)
                        results["alarm_oids"][alarm_name] = int_val
                    except ValueError:
                        results["alarm_oids"][alarm_name] = str_val

    except Exception as e:
        results["parse_error"] = str(e)

    return results


# ── GUI ───────────────────────────────────────────────────────────────────────

class TrapReceiverApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HR1065 Alarm-Monitor  //  SNMP Trap Empfaenger")
        self.configure(bg=C["bg"])
        self.geometry("700x520")
        self.resizable(True, True)

        self._alarm_states = {}  # name -> 0=OK, 1=ALARM
        self._trap_count = 0
        self._build_ui()
        self._start_listener()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=C["bg"], pady=6)
        hdr.pack(fill="x", padx=10)
        tk.Label(hdr, text="// HR1065 ALARM-MONITOR //",
                 bg=C["bg"], fg=C["acc"],
                 font=("Consolas", 13, "bold")).pack(side="left")
        self._lbl_status = tk.Label(hdr, text="  [  Warte auf Traps...  ]",
                                    bg=C["bg"], fg=C["sub"],
                                    font=("Consolas", 9))
        self._lbl_status.pack(side="left", padx=10)
        self._lbl_count = tk.Label(hdr, text="0 Traps",
                                   bg=C["bg"], fg=C["sub"],
                                   font=("Consolas", 9))
        self._lbl_count.pack(side="right")

        # Alarm-Status-Panel
        alarm_frame = tk.Frame(self, bg=C["panel"], bd=1, relief="flat")
        alarm_frame.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(alarm_frame, text="  ALARM-STATUS  ",
                 bg=C["panel"], fg=C["acc"],
                 font=("Consolas", 8, "bold")).pack(anchor="w", pady=(4, 2))

        self._alarm_labels = {}
        grid = tk.Frame(alarm_frame, bg=C["panel"])
        grid.pack(fill="x", padx=6, pady=(0, 6))

        alarms = [
            ("VSWR",            "VSWR-Alarm"),
            ("TX-PLL",          "TX-PLL-Alarm"),
            ("RX-PLL",          "RX-PLL-Alarm"),
            ("Temperatur",      "Temperatur-Alarm"),
            ("Luefter",         "Luefter-Alarm"),
            ("Spannung",        "Spannungs-Alarm"),
            ("Vorwaertsleist.", "Vorwaertsleistung-Alarm"),
            ("Rueckwleist.",    "Rueckwaertsleistung-Alarm"),
            ("Batterie",        "Batterie-Alarm"),
        ]

        for i, (label, key) in enumerate(alarms):
            col = i % 3
            row = i // 3
            box = tk.Frame(grid, bg=C["bg"], padx=6, pady=4,
                           bd=1, relief="flat")
            box.grid(row=row, column=col, padx=3, pady=2, sticky="nsew")
            tk.Label(box, text=label, bg=C["bg"], fg=C["sub"],
                     font=("Consolas", 7)).pack()
            lbl = tk.Label(box, text="?  unbekannt",
                           bg=C["bg"], fg=C["sub"],
                           font=("Consolas", 9, "bold"))
            lbl.pack()
            self._alarm_labels[key] = (box, lbl)

        for c in range(3):
            grid.columnconfigure(c, weight=1)

        # Trap-Log
        log_frame = tk.Frame(self, bg=C["panel"])
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        tk.Label(log_frame, text="  TRAP-PROTOKOLL  ",
                 bg=C["panel"], fg=C["acc"],
                 font=("Consolas", 8, "bold")).pack(anchor="w", pady=(4, 2))

        self._log = scrolledtext.ScrolledText(
            log_frame, bg=C["bg"], fg=C["fg"],
            font=("Consolas", 8), state="disabled",
            relief="flat", bd=0, insertbackground=C["acc"])
        self._log.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self._log.tag_config("alarm", foreground=C["red"])
        self._log.tag_config("ok",    foreground=C["grn"])
        self._log.tag_config("info",  foreground=C["cyan"])
        self._log.tag_config("ts",    foreground=C["sub"])

        # Statuszeile
        foot = tk.Frame(self, bg=C["bg"])
        foot.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(foot, text="Lauscht auf UDP :162  (SNMP Traps)",
                 bg=C["bg"], fg=C["sub"],
                 font=("Consolas", 8)).pack(side="left")
        tk.Button(foot, text="Log leeren",
                  bg=C["panel"], fg=C["sub"],
                  font=("Consolas", 8), relief="flat",
                  command=self._clear_log).pack(side="right")

    def _log_write(self, text, tag="info"):
        self._log.configure(state="normal")
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log.insert("end", f"[{ts}] ", "ts")
        self._log.insert("end", text + "\n", tag)
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _update_alarm_box(self, alarm_name, value):
        if alarm_name not in self._alarm_labels:
            return
        box, lbl = self._alarm_labels[alarm_name]
        if value == 1:
            lbl.configure(text="[!] ALARM", fg=C["red"])
            box.configure(bg="#1a0005")
            lbl.configure(bg="#1a0005")
        elif value == 0:
            lbl.configure(text="[OK] Normal", fg=C["grn"])
            box.configure(bg=C["bg"])
            lbl.configure(bg=C["bg"])
        else:
            lbl.configure(text=f"[?] {value}", fg=C["sub"])

    def _on_trap(self, data, addr):
        """Wird aus Thread aufgerufen – GUI-Update via after()"""
        self._trap_count += 1
        parsed = parse_snmp_trap(data)
        src_ip = addr[0]

        def _gui_update():
            self._lbl_count.configure(
                text=f"{self._trap_count} Trap{'s' if self._trap_count != 1 else ''}")

            if parsed.get("alarm_oids"):
                for name, val in parsed["alarm_oids"].items():
                    self._alarm_states[name] = val
                    self._update_alarm_box(name, val)
                    status_txt = ALARM_VALUES.get(val, str(val))
                    tag = "alarm" if val == 1 else "ok"
                    self._log_write(
                        f"Trap von {src_ip}  ->  {name}: {status_txt}", tag)
                # Statusanzeige
                active = [n for n, v in self._alarm_states.items() if v == 1]
                if active:
                    self._lbl_status.configure(
                        text=f"  [!] ALARM: {', '.join(active)}",
                        fg=C["red"])
                else:
                    self._lbl_status.configure(
                        text="  [OK] Alle Alarme normal", fg=C["grn"])
            else:
                # Unbekannter Trap – Rohdaten anzeigen
                self._log_write(
                    f"Trap von {src_ip}  (unbekannt)  "
                    f"Enterprise: {parsed.get('enterprise', '?')}", "info")
                for oid, val in list(parsed.get("raw_oids", {}).items())[:5]:
                    self._log_write(f"   OID {oid} = {val}", "info")

        self.after(0, _gui_update)

    def _start_listener(self):
        """Startet UDP-Listener auf Port 162 in eigenem Thread."""
        def _listen():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("0.0.0.0", 162))
                self.after(0, lambda: self._log_write(
                    "Trap-Empfaenger aktiv auf UDP :162 -- warte auf HR1065...",
                    "info"))
                while True:
                    try:
                        data, addr = sock.recvfrom(4096)
                        self._on_trap(data, addr)
                    except Exception as e:
                        self.after(0, lambda e=e: self._log_write(
                            f"Empfangsfehler: {e}", "alarm"))
            except PermissionError:
                self.after(0, lambda: self._log_write(
                    "FEHLER: Port 162 erfordert Administrator-Rechte!\n"
                    "   -> Rechtsklick auf repeater_trap_receiver.py\n"
                    "   -> 'Als Administrator ausfuehren'", "alarm"))
            except OSError as e:
                self.after(0, lambda e=e: self._log_write(
                    f"Port 162 konnte nicht gebunden werden: {e}", "alarm"))

        t = threading.Thread(target=_listen, daemon=True)
        t.start()


if __name__ == "__main__":
    app = TrapReceiverApp()
    app.mainloop()
