"""
Hytera Tactical Suite - Konfigurationsmodul
Verwaltet dynamische Pfade relativ zum Projektordner, Default-Werte,
Verzeichnisse für Uploads, Kachel-Cache, Audioaufnahmen und Lagepläne.
"""

import os
from typing import List

# Basisverzeichnisse dynamisch ermitteln (100% autark und portabel)
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(MODULE_DIR, "static")
TEMPLATES_DIR = os.path.join(MODULE_DIR, "templates")
ASSETS_DIR = os.path.join(STATIC_DIR, "assets")
SYMBOLS_DIR = os.path.join(ASSETS_DIR, "tactical_symbols")
UPLOADS_DIR = os.path.join(STATIC_DIR, "uploads")
TILES_CACHE_DIR = os.path.join(STATIC_DIR, "tiles_cache")
RECORDINGS_DIR = os.path.join(STATIC_DIR, "recordings")

# Verzeichnisse automatisch anlegen
for d in [UPLOADS_DIR, TILES_CACHE_DIR, RECORDINGS_DIR, SYMBOLS_DIR]:
    os.makedirs(d, exist_ok=True)

# Standard-Datenbankpfad (liegt direkt im Projektordner)
DEFAULT_DB_PATH = os.path.join(MODULE_DIR, "tactical_suite.db")

# Pfad zur ids.json (lokal im Tactical Suite Verzeichnis - 100% autark)
POSSIBLE_IDS_PATHS = [
    os.path.join(MODULE_DIR, "ids.json"),
]

# Standard Netzwerk-Ports
DEFAULT_HTTP_HOST = "0.0.0.0"
DEFAULT_HTTP_PORT = 8000
DEFAULT_UDP_PORT = 30003  # Hytera GPS UDP Port (HR1065 Standard)

# Taktisches Einsatzgebiet & Basiskarten-Zentrum
DEFAULT_MAP_CENTER = [51.2325, 6.7800]
DEFAULT_MAP_ZOOM = 15

# Zwei Eck-Koordinaten für Leaflet L.imageOverlay [[Süd, West], [Nord, Ost]]
DEFAULT_OVERLAY_BOUNDS = [
    [51.2250, 6.7700],  # Südwest
    [51.2400, 6.7900],  # Nordost
]
DEFAULT_OVERLAY_IMAGE = "/static/assets/tactical_overlay.png"

# Vorfalls-Prioritäten und Marker-Typen
MARKER_PRIORITIES = ["low", "normal", "high", "critical"]
MARKER_TYPES = ["incident", "hazard", "checkpoint", "medical", "roadblock", "base", "info"]

# Demo / Mock Telemetrie Modus (Standard: False = Echtbetrieb mit echten Hytera Funkgeräten)
DEFAULT_ENABLE_MOCK = False

# Unterstützte Basiskarten-Anbieter
MAP_PROVIDERS = {
    "osm": "OpenStreetMap / Lokaler Offline-Cache (Autark)",
    "google_roadmap": "Google Maps Straßenkarte",
    "google_satellite": "Google Maps Satellit (Luftbild)",
    "google_hybrid": "Google Maps Hybrid (Satellit + Straßen/Orte)",
    "google_terrain": "Google Maps Gelände (Topografie)",
    "mapbox": "Mapbox Satellit & Straßen"
}

# Taktische Zeichen (DV 102 BOS)
TACTICAL_SYMBOLS = {
    "elw": {"name": "ELW 1 (Einsatzleitung)", "file": "elw.svg"},
    "hlf": {"name": "HLF 20 (Löschfahrzeug)", "file": "hlf.svg"},
    "rtw": {"name": "RTW (Rettungsdienst)", "file": "rtw.svg"},
    "sammelplatz": {"name": "Sammelplatz", "file": "sammelplatz.svg"},
    "feuer": {"name": "Brandstelle / Feuer", "file": "feuer.svg"},
    "gefahrgut": {"name": "Gefahrgut / Absperrung", "file": "gefahrgut.svg"},
    "sperre": {"name": "Straßensperre", "file": "sperre.svg"},
    "bereitstellung": {"name": "Bereitstellungsraum", "file": "bereitstellung.svg"},
}


