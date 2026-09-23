"""
Hytera Command Center – USV Modbus TCP Monitor
BlueWalker PowerWalker VFI 2000 ICR IoT

Register-Map durch Live-Scan ermittelt (Vorprojekt 2026-08-11) und
durch Community-Templates (Loxone Library) erweitert:

  Block A  Input Reg 129–178: Eingang, Ausgang, Batterie, Frequenzen
  Block B  Input Reg 225–249: Last, Temperatur, Status-Flags

Alle Werte werden auf ihre physikalische Einheit skaliert:
  - Spannungen: /10 → Volt
  - Frequenzen: /10 → Hz
  - Prozent:    direkt (0–100)
  - Zeit:       direkt (Minuten)
  - Temperatur: direkt (°C)
  - Strom:      /10 → Ampere (falls unterstützt)

Voraussetzung: Modbus TCP in USV aktivieren:
  LCD → Settings → Communication → Modbus TCP → ON
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Callable, Awaitable

try:
    from .config import UPS_IP, UPS_MODBUS_PORT, UPS_POLL_INTERVAL_S
except ImportError:
    try:
        from backend.config import UPS_IP, UPS_MODBUS_PORT, UPS_POLL_INTERVAL_S
    except ImportError:
        from config import UPS_IP, UPS_MODBUS_PORT, UPS_POLL_INTERVAL_S

logger = logging.getLogger("ups_modbus")

# ─────────────────────────────────────────────────────────────────
# Modbus Register-Adressen (Basis: Input Registers, FC=04)
# ─────────────────────────────────────────────────────────────────

# Block A (Offset relativ zu Startadresse 129, d.h. Register 129 = Index 0)
_REG_A_START        = 129
_REG_A_COUNT        = 50

REG_A_INPUT_VOLT    = 3    # Eingangsspannung     /10 → V
REG_A_INPUT_FREQ    = 4    # Eingangsfrequenz     /10 → Hz
REG_A_INPUT_CURR    = 5    # Eingangsstrom        /10 → A
REG_A_BYPASS_VOLT   = 7    # Bypass-Spannung      /10 → V
REG_A_BYPASS_FREQ   = 8    # Bypass-Frequenz      /10 → Hz
REG_A_OUTPUT_VOLT   = 10   # Ausgangsspannung     /10 → V
REG_A_OUTPUT_FREQ   = 15   # Ausgangsfrequenz     /10 → Hz
REG_A_OUTPUT_CURR   = 12   # Ausgangsstrom        /10 → A
REG_A_OUTPUT_POWER_VA= 13  # Scheinleistung       VA (direkt)
REG_A_OUTPUT_POWER_W= 14   # Wirkleistung         W
REG_A_BATT_PCT      = 40   # Batterieladestand    % (0–100)
REG_A_BATT_RUNTIME  = 41   # Restlaufzeit Batterie Minuten
REG_A_BATT_VOLT     = 42   # Batteriespannung     /10 → V
REG_A_BATT_CURR     = 43   # Batteriestrom        /10 → A (neg = Entladen)
REG_A_TEMP_BATT     = 44   # Batterietemperatur   °C (direkt)

# Block B (Offset relativ zu Startadresse 225, d.h. Register 225 = Index 0)
_REG_B_START        = 225
_REG_B_COUNT        = 25

REG_B_LOAD_PCT      = 0    # Last                 % (0–100)
REG_B_TEMP_INTERN   = 1    # Innentemperatur      °C (direkt)
REG_B_NETZ_OK       = 10   # Netz-Status-Flag     1 = Netz OK, 0 = Batterie
REG_B_BYPASS_ACTIVE = 11   # Bypass aktiv         1 = ja
REG_B_BATT_LOW      = 12   # Batterie schwach     1 = ja
REG_B_FAULT         = 13   # Fehlercode aktiv     1 = ja
REG_B_TYPE          = 15   # UPS-Typ-Code
REG_B_FAULT_CODE    = 16   # Fehlercode
REG_B_OUTLET_1      = 18   # Steckdosen-Segment 1 1 = Ein, 0 = Aus
REG_B_OUTLET_2      = 19   # Steckdosen-Segment 2 1 = Ein, 0 = Aus

# Ungültig/nicht unterstützt Sentinel
_INVALID = 65535

# ─────────────────────────────────────────────────────────────────
# Dataclass für USV-Zustand
# ─────────────────────────────────────────────────────────────────

@dataclass
class UPSState:
    """Vollständiger Zustand der BlueWalker PowerWalker USV."""

    online:             bool  = False
    error:              Optional[str] = None

    # Eingang
    input_volt:         Optional[float] = None  # V
    input_freq:         Optional[float] = None  # Hz
    input_curr:         Optional[float] = None  # A

    # Ausgang
    output_volt:        Optional[float] = None  # V
    output_freq:        Optional[float] = None  # Hz
    output_curr:        Optional[float] = None  # A
    output_power_w:     Optional[float] = None  # Wirkleistung W
    output_power_va:    Optional[float] = None  # Scheinleistung VA
    power_factor:       Optional[float] = None  # cos phi (0.00 - 1.00)

    # Energie & Netzausfall-Zähler
    energy_kwh:         float = 0.0             # Kumulierter Verbrauch (kWh)
    transfers_to_battery: int = 0               # Anzahl Netzausfälle
    total_battery_seconds: float = 0.0          # Sekunden im Akkubetrieb
    last_power_fail_time: Optional[float] = None# Zeitstempel letzter Netzausfall

    # Batterie
    batt_pct:           Optional[int]   = None  # %
    batt_runtime_min:   Optional[int]   = None  # Minuten
    batt_volt_v:        Optional[float] = None  # V
    batt_curr_a:        Optional[float] = None  # A (neg = Entladen)
    batt_temp_c:        Optional[float] = None  # °C
    batt_status:        str = "Unbekannt"
    battery_soh_pct:    int = 100               # State of Health (%)

    # Last & Temperatur
    load_pct:           Optional[int]   = None  # %
    internal_temp_c:    Optional[int]   = None  # °C

    # Status-Flags & Segmente
    netz_ok:            bool = False
    bypass_active:      bool = False
    batt_low:           bool = False
    fault_active:       bool = False
    fault_code:         Optional[int] = None
    outlet_group_1:     bool = True             # Segment 1 Aktiv
    outlet_group_2:     bool = True             # Segment 2 Aktiv

    # Abgeleiteter Status
    netz_status:        str = "Unbekannt"

    # Rohdaten für Diagnose
    raw_block_a:        Optional[list] = field(default=None, repr=False)
    raw_block_b:        Optional[list] = field(default=None, repr=False)

    # Zeitstempel
    polled_at:          Optional[float] = None

    def to_dict(self) -> dict:
        """Serialisiert den State zu einem JSON-sicheren Dictionary (ohne Rohdaten)."""
        result = {}
        for fname in self.__dataclass_fields__:
            if fname in ("raw_block_a", "raw_block_b"):
                continue
            result[fname] = getattr(self, fname)
        return result


# ─────────────────────────────────────────────────────────────────
# Register-Parser
# ─────────────────────────────────────────────────────────────────

def _safe_val(regs: Optional[list], idx: int, scale: float = 1.0) -> Optional[float]:
    """Liest Register-Wert mit Bounds-Check und 0xFFFF-Filter."""
    if regs is None or idx >= len(regs):
        return None
    val = regs[idx]
    if val == _INVALID:
        return None
    return round(val * scale, 2) if scale != 1.0 else val


def classify_battery(pct: Optional[float], netz_ok: bool) -> str:
    """
    Klassifiziert den Batteriestatus anhand des Ladestands und Netz-Status.

    Args:
        pct:     Batterieladestand in Prozent (0–100) oder None.
        netz_ok: True wenn das Stromnetz vorhanden und stabil ist.

    Returns:
        Klassifizierungs-String: 'Laden', 'Normal', 'Gut', 'Schwach', 'Kritisch!', 'Leer!', 'Unbekannt'
    """
    if pct is None:
        return "Unbekannt"

    # Netz vorhanden → Batterie lädt (solange nicht 100%)
    if netz_ok and pct < 100:
        return "Laden"

    # Ladestand-Klassifizierung
    if pct >= 90:
        return "Normal"
    if pct >= 60:
        return "Gut"
    if pct >= 30:
        return "Schwach"
    if pct >= 10:
        return "Kritisch!"
    return "Leer!"


def _parse_registers(
    block_a: Optional[list],
    block_b: Optional[list],
) -> UPSState:
    """
    Parst die Modbus-Register-Blöcke A und B in einen strukturierten UPSState.

    Args:
        block_a: Liste von 50 Register-Werten (FC=4, Addr 129–178)
        block_b: Liste von 25 Register-Werten (FC=4, Addr 225–249)

    Returns:
        UPSState-Objekt mit allen verfügbaren Metriken.
    """
    state = UPSState(polled_at=time.time())

    if block_a is None:
        state.error = "Modbus Block A nicht verfügbar"
        return state

    try:
        # ── Eingang ──────────────────────────────────────────────
        raw_in_volt = _safe_val(block_a, REG_A_INPUT_VOLT)
        state.input_volt  = round(raw_in_volt / 10.0, 1) if raw_in_volt is not None else None
        raw_in_freq = _safe_val(block_a, REG_A_INPUT_FREQ)
        state.input_freq  = round(raw_in_freq / 10.0, 1) if raw_in_freq is not None else None
        raw_in_curr = _safe_val(block_a, REG_A_INPUT_CURR)
        state.input_curr  = round(raw_in_curr / 10.0, 1) if raw_in_curr is not None else None

        # ── Ausgang ───────────────────────────────────────────────
        raw_out_volt = _safe_val(block_a, REG_A_OUTPUT_VOLT)
        state.output_volt  = round(raw_out_volt / 10.0, 1) if raw_out_volt is not None else None
        raw_out_freq = _safe_val(block_a, REG_A_OUTPUT_FREQ)
        state.output_freq  = round(raw_out_freq / 10.0, 1) if raw_out_freq is not None else None
        raw_out_curr = _safe_val(block_a, REG_A_OUTPUT_CURR)
        state.output_curr  = round(raw_out_curr / 10.0, 1) if raw_out_curr is not None else None
        raw_out_va = _safe_val(block_a, REG_A_OUTPUT_POWER_VA)
        state.output_power_va = int(raw_out_va) if raw_out_va is not None else None
        raw_out_pwr = _safe_val(block_a, REG_A_OUTPUT_POWER_W)
        state.output_power_w = int(raw_out_pwr) if raw_out_pwr is not None else None
        if state.output_power_va and state.output_power_va > 0 and state.output_power_w is not None:
            state.power_factor = round(min(1.0, max(0.0, state.output_power_w / state.output_power_va)), 2)
        elif state.output_power_w is not None:
            state.power_factor = 1.0

        # ── Batterie ──────────────────────────────────────────────
        raw_batt_pct = _safe_val(block_a, REG_A_BATT_PCT)
        state.batt_pct = int(raw_batt_pct) if raw_batt_pct is not None else None

        raw_batt_rt = _safe_val(block_a, REG_A_BATT_RUNTIME)
        state.batt_runtime_min = int(raw_batt_rt) if raw_batt_rt is not None else None

        raw_batt_volt = _safe_val(block_a, REG_A_BATT_VOLT)
        state.batt_volt_v = round(raw_batt_volt / 10.0, 2) if raw_batt_volt is not None else None

        raw_batt_curr = _safe_val(block_a, REG_A_BATT_CURR)
        if raw_batt_curr is not None:
            # Signed int16: Werte > 32767 sind negativ (Entladen)
            signed = raw_batt_curr if raw_batt_curr < 32768 else raw_batt_curr - 65536
            state.batt_curr_a = round(signed / 10.0, 2)

        raw_batt_temp = _safe_val(block_a, REG_A_TEMP_BATT)
        state.batt_temp_c = int(raw_batt_temp) if raw_batt_temp is not None else None

        # ── Block B: Last & Status ────────────────────────────────
        if block_b is not None:
            raw_load = _safe_val(block_b, REG_B_LOAD_PCT)
            state.load_pct = int(raw_load) if raw_load is not None else None

            raw_temp = _safe_val(block_b, REG_B_TEMP_INTERN)
            state.internal_temp_c = int(raw_temp) if raw_temp is not None else None

            netz_flag = _safe_val(block_b, REG_B_NETZ_OK)
            state.netz_ok = bool(netz_flag) if netz_flag is not None else False

            bypass_flag = _safe_val(block_b, REG_B_BYPASS_ACTIVE)
            state.bypass_active = bool(bypass_flag) if bypass_flag is not None else False

            batt_low_flag = _safe_val(block_b, REG_B_BATT_LOW)
            state.batt_low = bool(batt_low_flag) if batt_low_flag is not None else False

            fault_flag = _safe_val(block_b, REG_B_FAULT)
            state.fault_active = bool(fault_flag) if fault_flag is not None else False

            fault_code = _safe_val(block_b, REG_B_FAULT_CODE)
            state.fault_code = int(fault_code) if fault_code is not None else None

            raw_out1 = _safe_val(block_b, REG_B_OUTLET_1)
            if raw_out1 is not None:
                state.outlet_group_1 = (raw_out1 != 0)

            raw_out2 = _safe_val(block_b, REG_B_OUTLET_2)
            if raw_out2 is not None:
                state.outlet_group_2 = (raw_out2 != 0)

        # ── Abgeleitete Statuswerte ───────────────────────────────
        state.netz_status = (
            "Normal" if state.netz_ok and not state.bypass_active
            else ("Bypass!" if state.bypass_active
            else ("Batteriebetrieb!" if not state.netz_ok
            else "Unbekannt"))
        )

        state.batt_status = classify_battery(state.batt_pct, state.netz_ok)

        # SOH-Abschätzung
        soh = 100
        if state.fault_active:
            soh = max(50, soh - 30)
        if state.batt_low and (state.load_pct is not None and state.load_pct < 40):
            soh = min(soh, 65)
        state.battery_soh_pct = soh

        # ── Rohdaten ──────────────────────────────────────────────
        state.raw_block_a = list(block_a) if block_a else None
        state.raw_block_b = list(block_b) if block_b else None

        # Online wenn mindestens die Ausgangsspannung lesbar
        state.online = state.output_volt is not None or state.batt_pct is not None

    except Exception as exc:
        logger.exception("Fehler beim Parsen der Modbus-Register")
        state.error = str(exc)
        state.online = False

    return state


# ─────────────────────────────────────────────────────────────────
# UPS Monitor (async polling)
# ─────────────────────────────────────────────────────────────────

class UPSMonitor:
    """
    Asynchroner Modbus-TCP-Monitor für BlueWalker PowerWalker VFI 2000 ICR IoT.
    Pollt alle UPS_POLL_INTERVAL_S Sekunden und ruft on_update() mit dem State auf.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        poll_interval: Optional[int] = None,
        on_update: Optional[Callable[[UPSState], Awaitable[None]]] = None,
    ):
        self.host          = host or UPS_IP
        self.port          = port or UPS_MODBUS_PORT
        self.poll_interval = poll_interval or UPS_POLL_INTERVAL_S
        self.on_update     = on_update
        self.running       = False
        self._last_state:  Optional[UPSState] = None
        self._loop         = None
        self._offline_logged = False

        # Zähler & Akkumulatoren
        self._energy_kwh: float = 0.0
        self._transfers_to_battery: int = 0
        self._total_battery_seconds: float = 0.0
        self._last_power_fail_time: Optional[float] = None
        self._last_poll_time: Optional[float] = None
        self._last_netz_ok: Optional[bool] = None

    def reset_counters(self) -> None:
        """Setzt Zähler (Energieverbrauch kWh und Netzausfälle) für einen neuen Einsatz zurück."""
        self._energy_kwh = 0.0
        self._transfers_to_battery = 0
        self._total_battery_seconds = 0.0
        self._last_power_fail_time = None

    async def start(self) -> None:
        """Startet den Polling-Loop."""
        self.running = True
        self._loop   = asyncio.get_running_loop()
        logger.info(f"UPS Monitor gestartet: {self.host}:{self.port} (Intervall: {self.poll_interval}s)")
        while self.running:
            try:
                state = await self._loop.run_in_executor(None, self._poll_sync)
                self._last_state = state
                if self.on_update:
                    await self.on_update(state)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"UPS Poll Fehler: {exc}")
            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        """Stoppt den Polling-Loop."""
        self.running = False

    async def poll_once(self) -> "UPSState":
        """Sofortiger einzelner Poll – wird in einem Thread-Pool ausgeführt."""
        loop = asyncio.get_running_loop()
        state = await loop.run_in_executor(None, self._poll_sync)
        self._last_state = state
        return state

    async def get_raw_scan(self) -> dict:
        """Vollständiger Register-Scan für Diagnose."""
        return await self.scan_all_registers()

    def get_state(self) -> dict:
        """Gibt den letzten bekannten Zustand als Dictionary zurück."""
        if self._last_state is None:
            return UPSState().to_dict() if hasattr(UPSState(), 'to_dict') else vars(UPSState())
        state = self._last_state
        if hasattr(state, 'to_dict'):
            return state.to_dict()
        # Dataclass manuelle Serialisierung
        result = {}
        for f in state.__dataclass_fields__:
            val = getattr(state, f)
            if isinstance(val, (list, bytes)):
                continue  # raw_block_a/b ausschliessen
            result[f] = val
        return result

    def _poll_sync(self) -> UPSState:
        """Synchroner Modbus-Poll – wird via run_in_executor im Thread-Pool ausgeführt."""
        try:
            from pymodbus.client import ModbusTcpClient
        except ImportError:
            logger.error("pymodbus nicht installiert – USV-Monitoring deaktiviert")
            state = UPSState()
            state.error = "pymodbus not installed"
            return state

        # pymodbus Logger drosseln um Timeout-Wiederholungs-Spam zu verhindern
        logging.getLogger("pymodbus").setLevel(logging.WARNING)

        client = ModbusTcpClient(
            host    = self.host,
            port    = self.port,
            timeout = 3,
        )
        block_a = None
        block_b = None

        try:
            if not client.connect():
                if not self._offline_logged:
                    logger.info(f"Modbus: USV ({self.host}:{self.port}) nicht erreichbar – Status: Offline")
                    self._offline_logged = True
                state = UPSState()
                state.error = "Verbindung fehlgeschlagen"
                return state

            if self._offline_logged:
                logger.info(f"Modbus: Verbindung zu USV ({self.host}:{self.port}) erfolgreich hergestellt")
                self._offline_logged = False

            # Block A: Input Register 129–178 (FC=4)
            result_a = client.read_input_registers(
                address = _REG_A_START - 1,  # 0-indexed
                count   = _REG_A_COUNT,
            )
            if not result_a.isError():
                block_a = result_a.registers

            # Block B: Input Register 225–249 (FC=4)
            result_b = client.read_input_registers(
                address = _REG_B_START - 1,
                count   = _REG_B_COUNT,
            )
            if not result_b.isError():
                block_b = result_b.registers

            logger.debug(f"Modbus OK: Block A={len(block_a) if block_a else 0} Block B={len(block_b) if block_b else 0} Register")

        except Exception as exc:
            logger.warning(f"Modbus Exception: {exc}")
            state = UPSState()
            state.error = str(exc)
            return state
        finally:
            client.close()

        state = _parse_registers(block_a, block_b)

        # Akkumulatoren & Netzausfall-Zähler berechnen
        now = time.time()
        if self._last_poll_time and state.output_power_w is not None and state.online:
            dt = now - self._last_poll_time
            if 0 < dt < 300:
                self._energy_kwh += (state.output_power_w * dt) / 3_600_000.0

        if state.online:
            if self._last_netz_ok is True and state.netz_ok is False:
                self._transfers_to_battery += 1
                self._last_power_fail_time = now

            if state.netz_ok is False and self._last_poll_time:
                dt = now - self._last_poll_time
                if 0 < dt < 300:
                    self._total_battery_seconds += dt

            self._last_netz_ok = state.netz_ok

        self._last_poll_time = now

        state.energy_kwh = round(self._energy_kwh, 4)
        state.transfers_to_battery = self._transfers_to_battery
        state.total_battery_seconds = round(self._total_battery_seconds, 1)
        state.last_power_fail_time = self._last_power_fail_time

        return state

    async def scan_all_registers(self) -> Dict[str, Any]:
        """
        Vollständiger Register-Scan für Diagnose-Zwecke.
        Liest alle Input-Register von 0–400 und gibt Rohdaten zurück.
        """
        try:
            from pymodbus.client import ModbusTcpClient
        except ImportError:
            return {"error": "pymodbus not installed"}

        result: Dict[str, Any] = {"host": self.host, "port": self.port, "registers": {}}
        client = ModbusTcpClient(host=self.host, port=self.port, timeout=5)

        if not client.connect():
            return {"error": "Verbindung fehlgeschlagen"}

        try:
            # In 50er-Blöcken lesen
            for start in range(0, 400, 50):
                try:
                    resp = client.read_input_registers(address=start, count=50)
                    if not resp.isError():
                        for i, val in enumerate(resp.registers):
                            if val != _INVALID:
                                result["registers"][str(start + i)] = val
                except Exception:
                    pass  # Block überspringen
        finally:
            client.close()

        return result

    @property
    def last_state(self) -> Optional[UPSState]:
        return self._last_state