"""
Hytera Command Center – Konfigurationsmodul
Alle Pfade, IP-Adressen, Ports und Default-Werte zentral verwaltet.
Netzwerk-Topologie:
  ZTE MF281 (Telekom Speedbox 2)   – LTE-Internet-Gateway
  TP-Link Omada ER606              – LAN-Router / Managed Switch
  Hytera HR1065                    – DMR-Repeater
  BlueWalker PowerWalker VFI 2000  – USV (Modbus TCP)
"""

import os
from typing import Dict, List

# ── Basisverzeichnisse (portabel, relativ zu dieser Datei) ──────────────────
MODULE_DIR    = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR   = MODULE_DIR
ROOT_DIR      = os.path.dirname(MODULE_DIR)
FRONTEND_DIR  = os.path.join(ROOT_DIR, "frontend")
STATIC_DIR    = os.path.join(FRONTEND_DIR, "static")
TEMPLATES_DIR = FRONTEND_DIR
ASSETS_DIR    = os.path.join(STATIC_DIR, "assets")
SYMBOLS_DIR   = os.path.join(ASSETS_DIR, "tactical_symbols")
UPLOADS_DIR   = os.path.join(STATIC_DIR, "uploads")
TILES_DIR     = os.path.join(STATIC_DIR, "tiles_cache")
RECORDINGS_DIR = os.path.join(STATIC_DIR, "recordings")
DB_PATH       = os.path.join(ROOT_DIR, "command_center.db")
IDS_JSON_PATH = os.path.join(ROOT_DIR, "ids.json")

# Verzeichnisse sicherstellen
for _d in [UPLOADS_DIR, TILES_DIR, RECORDINGS_DIR, SYMBOLS_DIR]:
    os.makedirs(_d, exist_ok=True)

# ── HTTP-Server ─────────────────────────────────────────────────────────────
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8000

# ── Netzwerk-Hardware ────────────────────────────────────────────────────────
# Hytera HR1065 DMR-Repeater
REPEATER_IP          = "192.168.0.230"
SNMP_TRAP_PORT       = 10162   # Repeater sendet Traps hierher
SNMP_PORT            = 161     # SNMPv1/v2c Polling-Port auf dem Repeater
SNMP_COMMUNITY       = "public"# Community String (laut HYTERA-REPEATER-MIB: public)
SNMP_POLL_INTERVAL_S = 30      # Telemetrie-Abfragezyklus (Sekunden)

# Schwellenwerte für Repeater-Telemetrie (LibreNMS-Standard)
RPT_WARN_VSWR_HIGH   = 2.0     # VSWR > 2.0 = Warnung
RPT_ALARM_VSWR_HIGH  = 2.8     # VSWR > 2.8 = Kritisch (Antennenschaden)
RPT_WARN_TEMP_HIGH   = 60.0    # PA-Temperatur > 60°C = Warnung
RPT_ALARM_TEMP_HIGH  = 75.0    # PA-Temperatur > 75°C = Kritisch
RPT_WARN_VOLT_LOW    = 12.0    # Spannung < 12.0 V = Warnung
RPT_WARN_VOLT_HIGH   = 15.2    # Spannung > 15.2 V = Überspannung

# BlueWalker PowerWalker VFI 2000 ICR IoT (USV)
UPS_IP               = "192.168.0.232"
UPS_MODBUS_PORT      = 502
UPS_POLL_INTERVAL_S  = 30

# ZTE MF281 (Telekom Speedbox 2) – LTE-Internet-Gateway
ZTE_IP               = "192.168.0.1"
ZTE_POLL_INTERVAL_S  = 15

# TP-Link Omada ER606 – LAN-Router / Managed Switch
OMADA_IP             = "192.168.0.1"     # Management-IP (oft gleich wie ZTE via NAT)
OMADA_CONTROLLER_PORT = 8043             # Omada Controller HTTPS-Port (falls lokal)
OMADA_POLL_INTERVAL_S = 60

# ── Hytera NAI UDP-Ports ─────────────────────────────────────────────────────
# Vollständige Port-Map aller NAI-Dienste des HR1065
UDP_PORT_MAP: Dict[int, str] = {
    30001: "RRS_TS1",    # Radio Registration Service – Timeslot 1
    30002: "RRS_TS2",    # Radio Registration Service – Timeslot 2
    30003: "GPS_TS1",    # GPS/GNSS Positionsdaten – Timeslot 1
    30004: "GPS_TS2",    # GPS/GNSS Positionsdaten – Timeslot 2
    30005: "TELE_TS1",   # Telemetrie / I/O – Timeslot 1
    30006: "TELE_TS2",   # Telemetrie / I/O – Timeslot 2
    30007: "SMS_TS1",    # TMP (Text Message Protocol) – Timeslot 1
    30008: "SMS_TS2",    # TMP (Text Message Protocol) – Timeslot 2
    30009: "CC_TS1",     # Call-Control – Timeslot 1
    30010: "CC_TS2",     # Call-Control – Timeslot 2
    30012: "AUDIO_TS1",  # RTP G.711 Audio – Timeslot 1
    30014: "AUDIO_TS2",  # RTP G.711 Audio – Timeslot 2
}

# Primärer GPS-Port (für einfachen Listener-Modus)
DEFAULT_GPS_PORT = 30003

# HSTRP-Protokoll Konstanten
HSTRP_SIGNATURE    = b'\x32\x42\x00'
HSTRP_TO_RADIO     = 0x00
HSTRP_ACK          = 0x01
HSTRP_HEARTBEAT    = 0x02
HSTRP_SYNACK       = 0x05
HSTRP_FROM_RADIO   = 0x20
HSTRP_SYN          = 0x24

# RCP Opcode-Mapping (Radio Control Protocol, Little-Endian)
RCP_OPCODES: Dict[int, str] = {
    0x0001: "RRS_OFFLINE",
    0x0002: "RRS_QUERY",
    0x0003: "RRS_REGISTER",
    0x0004: "RRS_REGISTER_ACK",
    0x0004: "HR1065_NAI_CALL_STATUS",   # Primärer Call-Status-Opcode
    0x00A1: "TMP_PRIVATE_ACK",
    0x80A1: "TMP_PRIVATE_NO_ACK",
    0x00B1: "TMP_GROUP",
    0x0840: "CC_TX_REQ",
    0xB843: "CC_BROADCAST_TX_STATUS",
    0xB845: "CC_REPEATER_BROADCAST_TX_STATUS",
}

# ── Karte & Geografie ─────────────────────────────────────────────────────────
DEFAULT_MAP_CENTER  = [51.2325, 6.7800]
DEFAULT_MAP_ZOOM    = 15
DEFAULT_OVERLAY_BOUNDS = [
    [51.2250, 6.7700],   # Südwest
    [51.2400, 6.7900],   # Nordost
]

# ── Taktische Zeichen (DV 102 BOS) ──────────────────────────────────────────
TACTICAL_SYMBOLS: Dict[str, dict] = {
    "elw":             {"name": "ELW 1 (Einsatzleitung)",      "file": "elw.svg"},
    "hlf":             {"name": "HLF 20 (Löschfahrzeug)",       "file": "hlf.svg"},
    "rtw":             {"name": "RTW (Rettungsdienst)",         "file": "rtw.svg"},
    "sammelplatz":     {"name": "Sammelplatz",                  "file": "sammelplatz.svg"},
    "feuer":           {"name": "Brandstelle / Feuer",          "file": "feuer.svg"},
    "gefahrgut":       {"name": "Gefahrgut / Absperrung",       "file": "gefahrgut.svg"},
    "sperre":          {"name": "Straßensperre",                "file": "sperre.svg"},
    "bereitstellung":  {"name": "Bereitstellungsraum",          "file": "bereitstellung.svg"},
}

# ── Karten-Anbieter ──────────────────────────────────────────────────────────
MAP_PROVIDERS: Dict[str, str] = {
    "osm":              "OpenStreetMap (Offline-Cache)",
    "google_roadmap":   "Google Maps Straßenkarte",
    "google_satellite": "Google Maps Satellit",
    "google_hybrid":    "Google Maps Hybrid",
    "google_terrain":   "Google Maps Gelände",
    "mapbox":           "Mapbox Satellit & Straßen",
}

# ── Marker-Konfiguration ─────────────────────────────────────────────────────
MARKER_PRIORITIES: List[str] = ["low", "normal", "high", "critical"]
MARKER_TYPES: List[str] = [
    "incident", "hazard", "checkpoint", "medical",
    "roadblock", "base", "info", "fire"
]

# ── Mock / Demo-Modus ────────────────────────────────────────────────────────
DEFAULT_ENABLE_MOCK = False

# ── System Default-Einstellungen ─────────────────────────────────────────────
DEFAULT_SETTINGS: Dict = {
    "gps_tracking_enabled":    True,
    "show_no_gps_units":       True,
    "allow_manual_positioning": True,
    "ptt_pulse_enabled":       True,
    "auto_pan_ptt":            False,
    "breadcrumbs_enabled":     True,
    "audio_enabled":           True,
    "markers_visible":         True,
    "unit_labels_visible":     True,
    "rssi_layer_visible":      False,
    "theme":                   "professional_dark",
    "mock_active":             False,
    "active_floor":            "EG",
    "map_provider":            "osm",
    "map_api_key":             "",
    "map_center":              DEFAULT_MAP_CENTER,
    "map_zoom":                DEFAULT_MAP_ZOOM,
    # Hardware
    "hw_repeater_ip":          REPEATER_IP,
    "hw_repeater_trap_port":   SNMP_TRAP_PORT,
    "hw_usv_ip":               UPS_IP,
    "hw_usv_port":             UPS_MODBUS_PORT,
    "hw_zte_ip":               ZTE_IP,
    "hw_omada_ip":             OMADA_IP,
    "hw_simulation":           False,
    # Diagnose
    "packet_dump_enabled":     False,
    "packet_dump_max_buffer":  500,
}
