"""
Hytera Lageplan & Tactical Timeline - Konfigurationsmodul
Verwaltet dynamische Pfade relativ zur Datei, Default-Werte und Lageplan-Parameter.
"""

import os
from typing import List

# Basisverzeichnisse dynamisch ermitteln
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(MODULE_DIR, ".."))
STATIC_DIR = os.path.join(MODULE_DIR, "static")
TEMPLATES_DIR = os.path.join(MODULE_DIR, "templates")
ASSETS_DIR = os.path.join(STATIC_DIR, "assets")
UPLOADS_DIR = os.path.join(STATIC_DIR, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Standard-Datenbankpfad (immer relativ zum Modul-Verzeichnis)
DEFAULT_DB_PATH = os.path.join(MODULE_DIR, "tactical_map.db")

# Mögliche Pfade zur ids.json der bestehenden Hytera-Installation
POSSIBLE_IDS_PATHS = [
    os.path.join(ROOT_DIR, "hytera_project", "ids.json"),
    os.path.join(ROOT_DIR, "ids.json"),
    os.path.join(MODULE_DIR, "ids.json"),
]

# Standard Netzwerk-Ports
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
DEFAULT_UDP_PORT = 30003  # Hytera GPS UDP Port (HR1065 Standard)

# Taktisches Einsatzgebiet & Lageplan-Overlay
# Standard-Koordinaten (z.B. Werks-/Operationsgelände)
DEFAULT_MAP_CENTER = [51.2325, 6.7800]
DEFAULT_MAP_ZOOM = 15

# Zwei Eck-Koordinaten für Leaflet L.imageOverlay [[Süd, West], [Nord, Ost]]
DEFAULT_OVERLAY_BOUNDS = [
    [51.2250, 6.7700],  # Südwestliche Ecke
    [51.2400, 6.7900],  # Nordöstliche Ecke
]
DEFAULT_OVERLAY_IMAGE = "/static/assets/tactical_overlay.png"

# Vorfalls-Prioritäten und Marker-Typen
MARKER_PRIORITIES = ["low", "normal", "high", "critical"]
MARKER_TYPES = ["incident", "hazard", "checkpoint", "medical", "roadblock", "base", "info"]
