# -*- coding: utf-8 -*-
"""
zte_monitor.py  –  ZTE MF281 (Telekom Speedbox 2) Monitor
Pollt die interne goform-API des Routers per HTTP und liefert:
  - Akku-Ladestand + Ladestatus
  - LTE-Signal: RSRP, RSRQ, SINR, Signalbalken
  - Netztyp (LTE/4G/3G) + Netzanbieter
  - WAN IP + Verbindungsstatus
  - Live-Datendurchsatz (TX/RX)

Verwendung in app.py analog zu usv_monitor.py:
    mon = ZTEMonitor(host="192.168.0.1", password="admin",
                     callback=my_cb, interval=15)
    mon.start()
"""

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)

# ── Netztyp-Lookup ───────────────────────────────────────────
# Numerische Codes (einige Firmwares) UND direkte Strings (MF281 Telekom)
_NET_TYPES: dict = {
    # Numerische Codes
    "0":  "No Service",
    "1":  "GSM",
    "2":  "GPRS",
    "3":  "EDGE",
    "4":  "WCDMA",
    "5":  "HSDPA",
    "6":  "HSUPA",
    "7":  "HSPA",
    "8":  "CDMA",
    "9":  "EVDO",
    "10": "EVDO Rev.A",
    "11": "FDD-LTE",
    "12": "TDD-LTE",
    "13": "DC-HSPA+",
    "14": "LTE-CA",
    "15": "5G NSA",
    "16": "5G SA",
    "17": "LTE+5G",
    # Direkte Strings (MF281 / MF286 Telekom-Firmware)
    "LTE":        "FDD-LTE",
    "LTE-FDD":    "FDD-LTE",
    "LTE-TDD":    "TDD-LTE",
    "LTE_CA":     "LTE-CA",
    "LTECA":      "LTE-CA",
    "NR":         "5G SA",
    "NSA":        "5G NSA",
    "EN-DC":      "LTE+5G",
    "WCDMA":      "WCDMA",
    "HSPA":       "HSPA+",
    "HSPA+":      "HSPA+",
    "EDGE":       "EDGE",
    "GPRS":       "GPRS",
    "GSM":        "GSM",
    "NO SERVICE": "No Service",
    "NO_SERVICE": "No Service",
}

_CHARGE_STATES: dict = {
    "0": "Entladen",
    "1": "Lädt",
    "2": "Voll",
}


@dataclass
class ZTEData:
    """Snapshot aller ZTE MF281 Telemetriedaten."""
    reachable:        bool          = False
    timestamp:        float         = 0.0

    battery_pct:      Optional[int] = None   # 0–100 %
    battery_charge:   str           = "?"    # Entladen / Lädt / Voll

    rsrp:             Optional[int] = None   # dBm  (typ. -140…-44)
    rsrq:             Optional[int] = None   # dB   (typ. -20…-3)
    sinr:             Optional[int] = None   # dB
    signal_bars:      int           = 0      # 0–5

    network_type:     str           = "?"
    network_provider: str           = "?"
    wan_ip:           str           = ""
    wan_status:       str           = "?"

    dl_bps:           int           = 0      # Bit/s Download
    ul_bps:           int           = 0      # Bit/s Upload
    rx_bytes:         int           = 0
    tx_bytes:         int           = 0

    def signal_quality(self) -> str:
        if self.rsrp is None:
            return "–"
        if self.rsrp >= -80:  return "Ausgezeichnet"
        if self.rsrp >= -90:  return "Gut"
        if self.rsrp >= -100: return "Mäßig"
        if self.rsrp >= -110: return "Schwach"
        return "Sehr schwach"

    def dl_mbps(self) -> float:
        return self.dl_bps / 1_000_000

    def ul_mbps(self) -> float:
        return self.ul_bps / 1_000_000


class ZTEMonitor:
    """
    Hintergrund-Thread der den ZTE MF281 periodisch pollt.

    Args:
        host:      IP-Adresse des Routers  (Standard: 192.168.0.1)
        password:  Web-Interface Passwort  (Standard: admin)
        callback:  Funktion(ZTEData) – wird nach jedem Poll aufgerufen
        interval:  Abfrageintervall in Sekunden (Standard: 15)
        timeout:   HTTP-Timeout in Sekunden    (Standard: 5)
    """

    # Alle bekannten Feldnamen gleichzeitig abfragen.
    # Die MF281-Firmware (Telekom Speedbox 2) antwortet nur auf exakt bekannte Keys.
    # Nicht vorhandene Keys werden einfach weggelassen.
    _CMD = (
        # Akku
        "battery_vol_percent,battery_charging,"
        # Signal - Standard-Keys
        "signalbar,"
        "lte_rsrp,lte_rsrq,lte_sinr,"
        "rsrp,rsrq,sinr,"
        "lte_rsrp_dec,lte_rsrq_dec,lte_sinr_dec,"
        "rsrp_dec,rsrq_dec,sinr_dec,"
        # Signal - alternative Keys (verschiedene ZTE-Firmwares)
        "lte_rssi,lte_snr,lte_ecio,"
        "rssi,snr,ecio,"
        "nr5g_rsrp,nr5g_rsrq,nr5g_sinr,nr5g_snr,"
        "nr_rsrp,nr_rsrq,nr_sinr,"
        "cell_rsrp,cell_rsrq,cell_sinr,"
        "lte_ca_pcell_rsrp,lte_ca_pcell_rsrq,"
        "wan_lte_rsrp,wan_lte_rsrq,wan_lte_sinr,"
        # Zellinfo (hilft beim Debuggen)
        "lte_pci,lte_earfcn,lte_band,"
        "cell_id,lac,tac,nr_cell_id,"
        # Netz
        "network_type,network_provider,"
        "modem_main_state,ppp_status,wan_ipaddr,"
        # Durchsatz
        "realtime_tx_thrpt,realtime_rx_thrpt,"
        "realtime_tx_bytes,realtime_rx_bytes,"
        "currecnt_download_speed,currecnt_upload_speed,"
        "current_download_speed,current_upload_speed"
    )

    def __init__(
        self,
        host:     str = "192.168.0.1",
        password: str = "admin",
        callback: Optional[Callable[["ZTEData"], None]] = None,
        interval: int = 15,
        timeout:  int = 5,
    ):
        self._host      = host
        self._password  = password
        self._cb        = callback
        self._interval  = max(5, interval)
        self._timeout   = timeout
        self._stop      = threading.Event()
        self._thread:   Optional[threading.Thread] = None
        self._session   = None
        self._base_url  = f"http://{host}"
        self._logged_in = False
        self._keys_logged = False
        self.last_data  = ZTEData()

    # ── Öffentliche API ─────────────────────────────────────

    def start(self):
        """Startet den Polling-Thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="ZTEMonitor"
        )
        self._thread.start()
        log.info(f"[ZTE] Monitor gestartet: {self._base_url}  Interval={self._interval}s")

    def stop(self):
        """Stoppt den Polling-Thread sauber."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._timeout + 2)
        log.info("[ZTE] Monitor gestoppt.")

    # ── Interner Thread ─────────────────────────────────────

    def _run(self):
        import requests
        self._session = requests.Session()
        self._session.headers.update({
            "Referer": f"{self._base_url}/index.html",
            "Accept":  "application/json, text/javascript, */*",
        })

        while not self._stop.is_set():
            try:
                if not self._logged_in:
                    self._login()

                raw  = self._fetch_status()
                data = self._parse(raw)
                self.last_data = data

                if self._cb:
                    try:
                        self._cb(data)
                    except Exception as cb_err:
                        log.warning(f"[ZTE] Callback-Fehler: {cb_err}")

            except _SessionExpiredError:
                log.info("[ZTE] Session abgelaufen – neu einloggen...")
                self._logged_in  = False
                self._keys_logged = False

            except Exception as e:
                log.debug(f"[ZTE] Poll-Fehler: {e}")
                self.last_data = ZTEData(reachable=False, timestamp=time.time())
                if self._cb:
                    try:
                        self._cb(self.last_data)
                    except Exception:
                        pass

            self._stop.wait(self._interval)

    def _login(self):
        """
        Login am ZTE goform-Interface.
        MF281 erwartet SHA256-gehashtes Passwort (Großbuchstaben).
        Fallback: MD5 falls SHA256 abgelehnt wird.
        Kein Passwort gesetzt → kein Login nötig.
        """
        pw_sha256 = hashlib.sha256(self._password.encode()).hexdigest().upper()
        pw_md5    = hashlib.md5(self._password.encode()).hexdigest()

        login_url = f"{self._base_url}/goform/goform_set_cmd_process"

        for pw, label in ((pw_sha256, "SHA256"), (pw_md5, "MD5")):
            payload = {"isTest": "false", "goformId": "LOGIN", "password": pw}
            try:
                resp = self._session.post(login_url, data=payload, timeout=self._timeout)
                if resp.status_code == 200:
                    self._logged_in = True
                    log.info(f"[ZTE] Login OK ({label})")
                    return
            except Exception:
                pass

        # Ohne Passwort-Schutz kommt man ohne Login durch
        log.info("[ZTE] Login übersprungen (kein Passwort oder kein Schutz)")
        self._logged_in = True

    def _fetch_status(self) -> dict:
        """Fragt alle Statusfelder in einem einzigen HTTP-GET ab."""
        url = (
            f"{self._base_url}/goform/goform_get_cmd_process"
            f"?isTest=false&cmd={self._CMD}&multi_data=1"
            f"&_={int(time.time() * 1000)}"
        )
        resp = self._session.get(url, timeout=self._timeout)

        if resp.status_code == 403:
            raise _SessionExpiredError()
        resp.raise_for_status()

        try:
            return resp.json()
        except ValueError:
            return self._manual_parse(resp.text)

    @staticmethod
    def _manual_parse(text: str) -> dict:
        """Fallback-Parser für malformed JSON."""
        cleaned = text.strip().lstrip("\ufeff")
        try:
            return json.loads(cleaned)
        except ValueError:
            pass
        import re
        result = {}
        for m in re.finditer(r'"?(\w+)"?\s*:\s*"?([^",}\n]*)"?', cleaned):
            result[m.group(1).strip()] = m.group(2).strip().strip('"')
        return result

    def _parse(self, raw: dict) -> ZTEData:
        """Konvertiert den rohen API-Response in ein ZTEData-Objekt.
        Erster Aufruf loggt alle empfangenen Keys als Diagnose-Info."""
        d = ZTEData(reachable=True, timestamp=time.time())

        # Einmalige Diagnose-Ausgabe der tatsächlich vorhandenen Keys
        if not self._keys_logged:
            self._keys_logged = True
            non_empty = {k: v for k, v in raw.items()
                         if v not in ("", None, "N/A", "0", 0)}
            all_keys  = sorted(raw.keys())
            log.info(f"[ZTE] ALLE API-Keys vom Gerät: {all_keys}")
            log.info(f"[ZTE] Keys mit Werten: {sorted(non_empty.keys())}")
            log.info(f"[ZTE] Rohdaten: {non_empty}")

        # ── Akku ────────────────────────────────────────────
        try:
            d.battery_pct = int(raw.get("battery_vol_percent", ""))
        except (ValueError, TypeError):
            d.battery_pct = None

        d.battery_charge = _CHARGE_STATES.get(
            str(raw.get("battery_charging", "0")), "?"
        )

        # ── Signal: mehrere Feldnamen-Varianten ─────────────
        def _to_int(*keys) -> Optional[int]:
            """Gibt ersten gültigen Integer aus mehreren möglichen Feldnamen."""
            for key in keys:
                v = raw.get(key, "")
                if v in ("", None, "N/A", "--", "-"):
                    continue
                try:
                    return int(
                        str(v).strip()
                        .replace(" ", "")
                        .replace("dBm", "")
                        .replace("dB", "")
                    )
                except (ValueError, TypeError):
                    continue
            return None

        # Priorität: spezifischere Keys zuerst, dann generisch, dann Fallback
        # RSRP (Signalstärke in dBm)
        d.rsrp = _to_int(
            "lte_rsrp", "rsrp", "lte_rsrp_dec", "rsrp_dec",          # Standard
            "nr5g_rsrp", "nr_rsrp", "cell_rsrp",                       # 5G / Zelle
            "lte_ca_pcell_rsrp", "wan_lte_rsrp",                       # CA / WAN
            "lte_rssi", "rssi",                                        # Fallback: RSSI
        )
        # RSRQ (Signalqualität in dB)
        d.rsrq = _to_int(
            "lte_rsrq", "rsrq", "lte_rsrq_dec", "rsrq_dec",
            "nr5g_rsrq", "nr_rsrq", "cell_rsrq",
            "lte_ca_pcell_rsrq", "wan_lte_rsrq",
            "lte_ecio", "ecio",                                        # Fallback: Ec/Io
        )
        # SINR (Signal-Rausch-Abstand)
        d.sinr = _to_int(
            "lte_sinr", "sinr", "lte_sinr_dec", "sinr_dec",
            "nr5g_sinr", "nr5g_snr", "nr_sinr",
            "cell_sinr", "wan_lte_sinr",
            "lte_snr", "snr",                                          # Fallback: SNR
        )

        try:
            d.signal_bars = min(5, max(0, int(raw.get("signalbar", 0) or 0)))
        except (ValueError, TypeError):
            d.signal_bars = 0

        # ── Netztyp: numerisch + String ─────────────────────
        net_code = str(raw.get("network_type", "")).strip()
        d.network_type = (
            _NET_TYPES.get(net_code)
            or _NET_TYPES.get(net_code.upper())
            or (net_code if net_code else "?")
        )
        d.network_provider = str(raw.get("network_provider", "")).strip() or "?"
        d.wan_ip           = str(raw.get("wan_ipaddr", "")).strip()
        d.wan_status       = str(raw.get("ppp_status", "")).strip()

        # ── Durchsatz: mehrere Feldnamen + KB/s vs Bytes/s ──
        def _speed_to_bps(*keys) -> int:
            """Erkennt automatisch KB/s (< 100000) vs Bytes/s (>= 100000)."""
            for key in keys:
                v = raw.get(key, "")
                if v in ("", None, "N/A", "0", 0):
                    continue
                try:
                    val = float(str(v).strip())
                    if val <= 0:
                        continue
                    # Heuristik: < 100 000 = KB/s, sonst Bytes/s
                    return int(val * 1000 * 8) if val < 100_000 else int(val * 8)
                except (ValueError, TypeError):
                    continue
            return 0

        d.dl_bps = _speed_to_bps(
            "realtime_rx_thrpt",
            "currecnt_download_speed",  # ZTE-Tippfehler in vielen Firmwares
            "current_download_speed",
        )
        d.ul_bps = _speed_to_bps(
            "realtime_tx_thrpt",
            "currecnt_upload_speed",
            "current_upload_speed",
        )

        def _int_val(val) -> int:
            try:
                return int(float(str(val).strip()))
            except (ValueError, TypeError):
                return 0

        d.rx_bytes = _int_val(raw.get("realtime_rx_bytes", 0))
        d.tx_bytes = _int_val(raw.get("realtime_tx_bytes", 0))

        log.debug(
            f"[ZTE] Akku={d.battery_pct}% {d.battery_charge}  "
            f"RSRP={d.rsrp}dBm  RSRQ={d.rsrq}dB  SINR={d.sinr}dB  "
            f"Netz={d.network_type}  DL={d.dl_mbps():.1f}Mbps"
        )
        return d


class _SessionExpiredError(Exception):
    """Internes Signal: HTTP 403 → Session neu aufbauen."""
    pass


# ── Standalone-Test ─────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    host     = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.1"
    password = sys.argv[2] if len(sys.argv) > 2 else "admin"

    def _print(data: ZTEData):
        bar  = "▓" * data.signal_bars + "░" * (5 - data.signal_bars)
        batt = f"{data.battery_pct}% ({data.battery_charge})" \
               if data.battery_pct is not None else "n/v"
        print(
            f"\n{'='*55}\n"
            f"  📶  [{bar}]  {data.network_type}  {data.network_provider}\n"
            f"  RSRP: {data.rsrp} dBm  RSRQ: {data.rsrq} dB  SINR: {data.sinr} dB\n"
            f"  Qualität: {data.signal_quality()}\n"
            f"  🔋  Akku: {batt}\n"
            f"  🌐  WAN: {data.wan_ip}  Status: {data.wan_status}\n"
            f"  ↓ {data.dl_mbps():.2f} Mbps   ↑ {data.ul_mbps():.2f} Mbps\n"
        )

    mon = ZTEMonitor(host=host, password=password, callback=_print, interval=10)
    mon.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        mon.stop()
