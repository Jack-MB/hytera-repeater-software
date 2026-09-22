"""
Hytera Command Center – Router Monitor
Überwacht ZTE MF281 (Telekom Speedbox 2) und TP-Link Omada ER606.

ZTE MF281 (LTE-Internet-Gateway):
  API: HTTP GET /goform/goform_get_cmd_process?cmd=...
  Kein Auth nötig im LAN.
  Liefert: RSRP, RSRQ, SINR, Netztyp, WAN-IP, Datendurchsatz,
           Akkustand, Ladestatus (falls Akku verbaut)

TP-Link Omada ER606 (LAN-Router/Switch):
  API: SNMP v2c (Port 161) ohne Controller-Software
  Oder: Omada Controller HTTPS-API (Port 8043) mit Token-Auth
  Liefert: Interface-Status, RX/TX-Bytes, Uptime, verbundene Geräte

Auto-Detection: ZTE wird zuerst versucht. Bei Fehler → SNMP-Fallback.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, Callable, Awaitable, List

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

try:
    from .config import ZTE_IP, OMADA_IP, ZTE_POLL_INTERVAL_S, OMADA_POLL_INTERVAL_S
except ImportError:
    try:
        from backend.config import ZTE_IP, OMADA_IP, ZTE_POLL_INTERVAL_S, OMADA_POLL_INTERVAL_S
    except ImportError:
        from config import ZTE_IP, OMADA_IP, ZTE_POLL_INTERVAL_S, OMADA_POLL_INTERVAL_S

logger = logging.getLogger("router_monitor")


# ─────────────────────────────────────────────────────────────────
# ZTE MF281 (Telekom Speedbox 2 / ZTE LTE-Router)
# ─────────────────────────────────────────────────────────────────

@dataclass
class ZTEState:
    """LTE-Status des ZTE MF281."""
    online:          bool  = False
    error:           Optional[str] = None

    # LTE-Signal
    rsrp:            Optional[float] = None   # dBm
    rsrq:            Optional[float] = None   # dB
    sinr:            Optional[float] = None   # dB
    rssi:            Optional[float] = None   # dBm

    # Netzwerk
    network_type:    str = ""   # "LTE", "5G NSA", etc.
    network_name:    str = ""   # Betreiber
    wan_ip:          str = ""
    band:            str = ""   # z.B. "B3", "B20"
    cell_id:         str = ""

    # Datendurchsatz (Realtime)
    rx_bytes:        Optional[int] = None
    tx_bytes:        Optional[int] = None

    # Akku (nur bei Gerät mit Akku)
    battery_pct:     Optional[int] = None
    charging:        Optional[bool] = None

    # Zeitstempel
    polled_at:      Optional[float] = None

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}


# ZTE MF281 HTTP-API: Felder die abgefragt werden
_ZTE_CMD_FIELDS = [
    "rsrp", "rsrq", "sinr", "rssi",
    "network_type", "network_provider", "wan_ipaddr",
    "lte_band", "cell_id",
    "realtime_rx_thrpt", "realtime_tx_thrpt",
    "battery_value", "charging_status",
]


def _parse_zte_response(data: Dict[str, Any]) -> ZTEState:
    """Parst die JSON-Antwort der ZTE-HTTP-API in einen ZTEState."""

    def safe_float(key: str, default: Optional[float] = None) -> Optional[float]:
        val = data.get(key, "")
        try:
            return float(str(val).strip())
        except (ValueError, TypeError):
            return default

    def safe_int(key: str, default: Optional[int] = None) -> Optional[int]:
        val = data.get(key, "")
        try:
            return int(str(val).strip())
        except (ValueError, TypeError):
            return default

    state = ZTEState(polled_at=time.time())

    state.rsrp         = safe_float("rsrp")
    state.rsrq         = safe_float("rsrq")
    state.sinr         = safe_float("sinr")
    state.rssi         = safe_float("rssi")
    state.network_type = str(data.get("network_type", "")).strip()
    state.network_name = str(data.get("network_provider", "")).strip()
    state.wan_ip       = str(data.get("wan_ipaddr", "")).strip()
    state.band         = str(data.get("lte_band", "")).strip()
    state.cell_id      = str(data.get("cell_id", "")).strip()

    # Durchsatz: raw in Byte/s, Anzeige in Kbps
    rx_raw = safe_int("realtime_rx_thrpt")
    tx_raw = safe_int("realtime_tx_thrpt")
    state.rx_bytes = rx_raw
    state.tx_bytes = tx_raw

    # Akku
    batt = safe_int("battery_value")
    state.battery_pct = batt if batt is not None and 0 <= batt <= 100 else None
    charge_raw = str(data.get("charging_status", "")).strip().lower()
    if charge_raw in ("1", "true", "charging"):
        state.charging = True
    elif charge_raw in ("0", "false", "discharging", "not_charging"):
        state.charging = False

    # Online wenn RSRP lesbar
    state.online = state.rsrp is not None and state.rsrp < 0

    return state


class ZTEMonitor:
    """
    Asynchroner HTTP-Monitor für ZTE MF281 (Telekom Speedbox 2).
    Pollt /goform/goform_get_cmd_process mit den relevanten Feldern.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        poll_interval: Optional[int] = None,
        on_update: Optional[Callable[[ZTEState], Awaitable[None]]] = None,
    ):
        self.host          = host or ZTE_IP
        self.poll_interval = poll_interval or ZTE_POLL_INTERVAL_S
        self.on_update     = on_update
        self.running       = False
        self._last_state:  Optional[ZTEState] = None

    async def start(self) -> None:
        """Startet den ZTE-Polling-Loop."""
        self.running = True
        logger.info(f"ZTE Monitor gestartet: http://{self.host} (Intervall: {self.poll_interval}s)")

        while self.running:
            try:
                state = await self._poll()
                self._last_state = state
                if self.on_update:
                    await self.on_update(state)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"ZTE Poll Fehler: {exc}")
            await asyncio.sleep(self.poll_interval)

    async def _poll(self) -> ZTEState:
        """Sendet HTTP-Anfrage an ZTE-API."""
        if not HTTPX_AVAILABLE:
            state = ZTEState()
            state.error = "httpx nicht installiert"
            return state

        url    = f"http://{self.host}/goform/goform_get_cmd_process"
        params = {"cmd": ",".join(_ZTE_CMD_FIELDS), "multi_data": "1"}
        headers = {
            "Referer": f"http://{self.host}/index.html",
            "User-Agent": "HyteraCmdCenter/1.0",
        }

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                state = _parse_zte_response(data)
                logger.debug(f"ZTE: RSRP={state.rsrp} SINR={state.sinr} Netz={state.network_type}")
                return state
        except httpx.ConnectError:
            state = ZTEState()
            state.error = "Verbindung abgelehnt"
            return state
        except httpx.TimeoutException:
            state = ZTEState()
            state.error = "Timeout"
            return state
        except Exception as exc:
            state = ZTEState()
            state.error = str(exc)
            logger.debug(f"ZTE HTTP Fehler: {exc}")
            return state

    def stop(self) -> None:
        self.running = False

    async def poll_once(self) -> ZTEState:
        """Sofortiger einzelner Poll."""
        state = await self._poll()
        self._last_state = state
        return state

    def get_state(self) -> dict:
        """Gibt den letzten bekannten Zustand als Dictionary zurück."""
        s = self._last_state or ZTEState()
        return s.to_dict()


# ─────────────────────────────────────────────────────────────────
# TP-Link Omada ER606 (LAN-Router / Managed Switch)
# ─────────────────────────────────────────────────────────────────

@dataclass
class OmadaState:
    """Status des TP-Link Omada ER606."""
    online:         bool  = False
    error:          Optional[str] = None

    # Router-Info
    hostname:       str = "ER606"
    firmware:       str = ""
    uptime_s:       Optional[int] = None

    # WAN-Interface
    wan_ip:         str = ""
    wan_status:     str = "Unbekannt"
    wan_rx_bytes:   Optional[int] = None
    wan_tx_bytes:   Optional[int] = None

    # LAN-Statistik
    clients_count:  Optional[int] = None
    lan_rx_bytes:   Optional[int] = None
    lan_tx_bytes:   Optional[int] = None

    # CPU/RAM (optional, nicht alle Firmwares)
    cpu_pct:        Optional[int] = None
    mem_pct:        Optional[int] = None

    # Zeitstempel
    polled_at:      Optional[float] = None

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in self.__dataclass_fields__}


class OmadaER606Monitor:
    """
    Asynchroner Monitor für TP-Link Omada ER606.
    Versucht HTTP-Status-API (Port 80/443), falls vorhanden.
    Fallback: Ping-basierte Online-Prüfung.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        poll_interval: Optional[int] = None,
        on_update: Optional[Callable[[OmadaState], Awaitable[None]]] = None,
    ):
        self.host          = host or OMADA_IP
        self.poll_interval = poll_interval or OMADA_POLL_INTERVAL_S
        self.on_update     = on_update
        self.running       = False
        self._last_state:  Optional[OmadaState] = None

    async def start(self) -> None:
        """Startet den Omada-Monitoring-Loop."""
        self.running = True
        logger.info(f"Omada ER606 Monitor gestartet: {self.host} (Intervall: {self.poll_interval}s)")

        while self.running:
            try:
                state = await self._poll()
                self._last_state = state
                if self.on_update:
                    await self.on_update(state)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"Omada Poll Fehler: {exc}")
            await asyncio.sleep(self.poll_interval)

    async def _poll(self) -> OmadaState:
        """Versucht Omada-Router per HTTP zu befragen."""
        state = OmadaState(polled_at=time.time(), hostname="ER606")

        if not HTTPX_AVAILABLE:
            state.error = "httpx nicht installiert"
            # Fallback: TCP-Ping auf Port 80
            state.online = await self._tcp_ping(80, timeout=3.0)
            return state

        # Omada ER606: Versuche lokale Weboberfläche
        try:
            async with httpx.AsyncClient(
                timeout     = 5.0,
                verify      = False,  # Self-signed cert
                follow_redirects = True,
            ) as client:
                # Status-API (falls Omada Software Controller läuft)
                resp = await client.get(f"http://{self.host}/")
                state.online = resp.status_code < 500
                logger.debug(f"Omada HTTP Status: {resp.status_code}")
        except httpx.ConnectError:
            # Router nicht erreichbar über HTTP → TCP-Ping
            state.online = await self._tcp_ping(80, timeout=3.0)
            if not state.online:
                state.error = "Nicht erreichbar"
        except Exception as exc:
            state.error = str(exc)
            state.online = False

        if state.online:
            state.wan_status = "Verbunden"

        return state

    async def _tcp_ping(self, port: int, timeout: float = 2.0) -> bool:
        """Prüft ob ein TCP-Port erreichbar ist (Ping-Ersatz)."""
        try:
            conn = asyncio.open_connection(self.host, port)
            reader, writer = await asyncio.wait_for(conn, timeout=timeout)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    def stop(self) -> None:
        self.running = False

    async def poll_once(self) -> OmadaState:
        """Sofortiger einzelner Poll."""
        state = await self._poll()
        self._last_state = state
        return state

    def get_state(self) -> dict:
        """Gibt den letzten bekannten Zustand als Dictionary zurück."""
        s = self._last_state or OmadaState()
        return s.to_dict()