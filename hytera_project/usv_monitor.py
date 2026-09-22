# -*- coding: utf-8 -*-
"""
usv_monitor.py
BlueWalker PowerWalker VFI 2000 ICR IoT  -  Modbus TCP Monitor

Register-Map durch Live-Scan ermittelt (2026-08-11):
  Block A (Input Reg 129-178): Spannungen, Frequenzen, Batterie
  Block B (Input Reg 225-249): Last, Temperatur, Status

Voraussetzung: Modbus TCP in USV aktivieren:
  LCD -> Settings -> Communication -> Modbus TCP -> ON

pip install pymodbus
"""
import threading, time, logging
log = logging.getLogger("usv")

# Block A: Messwerte (Offset relativ zu Adresse 129)
BLOCK_A_START = 129
BLOCK_A_COUNT = 50

REG_A = {
    "eingang_hz":    0,   # Reg 129: /10 -> Hz
    "eingang_volt":  3,   # Reg 132: /10 -> V
    "ausgang_hz":   15,   # Reg 144: /10 -> Hz
    "ausgang_volt": 18,   # Reg 147: /10 -> V
    "batterie_pct": 40,   # Reg 169: %
    "batterie_min": 41,   # Reg 170: Minuten
    "ausgang_v2":   47,   # Reg 176: /10 -> V (Kontrolle)
}

# Block B: Last/Status (Offset relativ zu Adresse 225)
BLOCK_B_START = 225
BLOCK_B_COUNT = 25

REG_B = {
    "last_pct":   0,   # Reg 225: %
    "temperatur": 1,   # Reg 226: C
    "last_pct2":  5,   # Reg 230: % (Kontrolle)
    "status_a":  10,   # Reg 235: Status-Flag (1=Alarm)
    "status_b":  11,   # Reg 236: Status-Flag (1=Netz OK)
    "status_a_alarm": 15,  # Reg 240: Alarm-Flag (0=OK, 1=Alarm)
}

NETZ_STATUS = {0: "Normal", 1: "Batterie!", 2: "Bypass", 3: "Booster", 4: "Fehler"}


class UPSData:
    def __init__(self):
        self.batterie_pct    = None
        self.batterie_min    = None
        self.batterie_status = None
        self.netz_status     = None
        self.eingang_volt    = None
        self.eingang_hz      = None
        self.ausgang_volt    = None
        self.ausgang_hz      = None
        self.last_pct        = None
        self.temperatur      = None
        self.alarme          = None
        self.batt_volt       = None   # Platzhalter (aus anderen Regs)
        self.erreichbar      = False
        self.letzte_abfrage  = None
        self.fehler          = None

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__dict__}


class USVMonitor(threading.Thread):
    MODBUS_PORT    = 502
    MODBUS_UNIT_ID = 1

    def __init__(self, ip, community="public", interval=30, callback=None):
        super().__init__(daemon=True, name="USV-Monitor")
        self.ip       = ip
        self.interval = interval
        self.callback = callback
        self._stop    = threading.Event()
        self.data     = UPSData()

    def stop(self): self._stop.set()

    def poll(self):
        d = UPSData()
        try:
            from pymodbus.client import ModbusTcpClient
            client = ModbusTcpClient(host=self.ip, port=self.MODBUS_PORT, timeout=4)

            if not client.connect():
                d.fehler = f"Verbindung zu {self.ip}:{self.MODBUS_PORT} fehlgeschlagen"
                log.warning(f"[USV] {d.fehler}")
                self.data = d
                return d

            def read_block(start, count):
                r = client.read_input_registers(
                    address=start, count=count, device_id=self.MODBUS_UNIT_ID)
                if r.isError(): return None
                return [None if v == 65535 else v for v in r.registers]

            ra = read_block(BLOCK_A_START, BLOCK_A_COUNT)
            rb = read_block(BLOCK_B_START, BLOCK_B_COUNT)
            client.close()

            if ra is None:
                d.fehler = "Modbus Lesefehler (Block A)"
                self.data = d
                return d

            def ga(name, div=1):
                idx = REG_A.get(name)
                if idx is None or idx >= len(ra) or ra[idx] is None: return None
                return round(ra[idx] / div, 2) if div != 1 else ra[idx]

            def gb(name, div=1):
                if rb is None: return None
                idx = REG_B.get(name)
                if idx is None or idx >= len(rb) or rb[idx] is None: return None
                return round(rb[idx] / div, 2) if div != 1 else rb[idx]

            d.eingang_hz   = ga("eingang_hz",   10)
            d.eingang_volt = ga("eingang_volt",  10)
            d.ausgang_hz   = ga("ausgang_hz",    10)
            d.ausgang_volt = ga("ausgang_volt",  10) or ga("ausgang_v2", 10)
            d.batterie_pct = ga("batterie_pct")
            d.batterie_min = ga("batterie_min")
            d.last_pct     = gb("last_pct") or gb("last_pct2")
            d.temperatur   = gb("temperatur")

            # Netz-Status: Reg 235/236=1 bedeutet Netz vorhanden, =0 bedeutet Batteriebetrieb
            st = gb("status_a")
            alarm = gb("status_a_alarm")  # Reg 240
            if st == 0:
                d.netz_status = "Batterie!"
                d.alarme = 1
            elif (alarm or 0) > 0:
                d.netz_status = "Alarm"
                d.alarme = 1
            else:
                d.netz_status = "Normal"
                d.alarme = 0

            # Batteriestatus
            pct = d.batterie_pct or 0
            if d.netz_status == "Normal" and pct < 100:
                d.batterie_status = "Laden"
            elif pct >= 80:
                d.batterie_status = "Normal"
            elif pct >= 30:
                d.batterie_status = "Schwach"
            else:
                d.batterie_status = "Leer!"

            d.erreichbar     = True
            d.letzte_abfrage = time.time()
            log.debug(f"[USV] Batt={d.batterie_pct}% {d.batterie_min}min "
                      f"Last={d.last_pct}% {d.netz_status} "
                      f"Ein={d.eingang_volt}V/{d.eingang_hz}Hz "
                      f"Aus={d.ausgang_volt}V/{d.ausgang_hz}Hz "
                      f"Temp={d.temperatur}C")

        except ImportError:
            d.fehler = "pymodbus nicht installiert: pip install pymodbus"
            log.error(d.fehler)
        except Exception as e:
            d.fehler = str(e)
            log.warning(f"[USV] Fehler: {e}")

        self.data = d
        return d

    def run(self):
        log.info(f"USV-Monitor (Modbus TCP) gestartet: {self.ip}:{self.MODBUS_PORT}")
        while not self._stop.is_set():
            try:
                d = self.poll()
                if self.callback: self.callback(d)
            except Exception as e:
                log.error(f"USV-Monitor: {e}")
            self._stop.wait(self.interval)
        log.info("USV-Monitor gestoppt")


def snmp_discover(ip, community="public"):
    """Kompatibilitaetsfunktion (jetzt Modbus-Test)."""
    mon = USVMonitor(ip)
    try:
        d = mon.poll()
        return (True, d) if d.erreichbar else (False, d.fehler or "Nicht erreichbar")
    except Exception as e:
        return False, str(e)


def modbus_scan(ip, port=502):
    """Rohe Register-Ausgabe fuer Diagnose."""
    try:
        from pymodbus.client import ModbusTcpClient
        client = ModbusTcpClient(host=ip, port=port, timeout=5)
        if not client.connect():
            return None, f"Verbindung fehlgeschlagen"
        r = client.read_input_registers(address=BLOCK_A_START, count=BLOCK_A_COUNT, device_id=1)
        client.close()
        return (None, str(r)) if r.isError() else (r.registers, None)
    except Exception as e:
        return None, str(e)


