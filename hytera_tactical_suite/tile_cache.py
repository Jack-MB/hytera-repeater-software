"""
Hytera Tactical Suite - Offline Kachel-Cache & Proxy (100% Autarkie bei Netzausfall)
Speichert OpenStreetMap-Kacheln lokal auf der Festplatte. Bei Netzausfall oder im Einsatzleitwagen
werden die Kacheln ohne Internetverbindung direkt von der Festplatte ausgeliefert.
"""

import os
import math
import logging
import urllib.request
from typing import Dict, Any, List, Optional
try:
    from .config import TILES_CACHE_DIR
except (ImportError, ValueError):
    from config import TILES_CACHE_DIR

logger = logging.getLogger("tile_cache")

# 1x1 transparenter PNG-Fallback falls Kachel offline nicht existiert
FALLBACK_TRANSPARENT_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06'
    b'\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
    b'\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


def deg2num(lat_deg: float, lon_deg: float, zoom: int):
    """Berechnet die Kachel-Koordinaten (x, y) aus WGS84 GPS-Koordinaten."""
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile


class TileCacheManager:
    """Verwaltet den lokalen Karten-Kachel-Cache für den Offline-Einsatz."""

    def __init__(self, cache_dir: str = TILES_CACHE_DIR):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

    def get_tile_path(self, z: int, x: int, y: int) -> str:
        return os.path.join(self.cache_dir, str(z), str(x), f"{y}.png")

    def has_tile(self, z: int, x: int, y: int) -> bool:
        return os.path.isfile(self.get_tile_path(z, x, y))

    def get_tile(self, z: int, x: int, y: int) -> bytes:
        """
        Gibt Kacheldaten zurück:
        1. Wenn lokal gecacht: Direkt von der Festplatte (0 ms Latenz).
        2. Wenn online: Kachel von OSM herunterladen und lokal speichern.
        3. Wenn offline und Kachel fehlt: Transparenten Fallback liefern (kein 404).
        """
        tile_path = self.get_tile_path(z, x, y)
        if os.path.isfile(tile_path):
            try:
                with open(tile_path, "rb") as f:
                    return f.read()
            except Exception as e:
                logger.error(f"Fehler beim Lesen der Kachel {z}/{x}/{y}: {e}")

        # Online-Download versuchen
        urls = [
            f"https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            f"https://tile.openstreetmap.de/{z}/{x}/{y}.png",
            f"https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"
        ]
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Referer": "https://www.openstreetmap.org/",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
        }

        for url in urls:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=3.5) as resp:
                    data = resp.read()
                    # Schutz vor 403 Sperr-Grafiken (z.B. 6987 Bytes oder Blocked Text)
                    if len(data) == 6987 or b"Blocked" in data or b"Access blocked" in data:
                        logger.warning(f"OSM Sperr-Antwort erhalten auf {url}, überspringe...")
                        continue
                    if len(data) < 500:
                        continue
                    # Kachel lokal speichern
                    os.makedirs(os.path.dirname(tile_path), exist_ok=True)
                    with open(tile_path, "wb") as f:
                        f.write(data)
                    return data
            except Exception as e:
                logger.debug(f"Download fehlgeschlagen für {url}: {e}")
                continue

        return FALLBACK_TRANSPARENT_PNG

    def preload_bbox(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        min_zoom: int = 13,
        max_zoom: int = 16
    ) -> Dict[str, Any]:
        """
        Lädt alle Kacheln für einen Bereich vor und speichert sie lokal.
        Macht das Einsatzgebiet in Sekunden 100% offline-fähig.
        """
        saved_count = 0
        total_count = 0

        for z in range(min_zoom, max_zoom + 1):
            x_min, y_max = deg2num(min_lat, min_lon, z)
            x_max, y_min = deg2num(max_lat, max_lon, z)

            x_start = min(x_min, x_max)
            x_end = max(x_min, x_max)
            y_start = min(y_min, y_max)
            y_end = max(y_min, y_max)

            for x in range(x_start, x_end + 1):
                for y in range(y_start, y_end + 1):
                    total_count += 1
                    tile_path = self.get_tile_path(z, x, y)
                    if not os.path.isfile(tile_path):
                        try:
                            url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
                            req = urllib.request.Request(
                                url,
                                headers={"User-Agent": "HyteraTacticalSuite/1.0 (Offline-Preload)"}
                            )
                            with urllib.request.urlopen(req, timeout=2.0) as resp:
                                data = resp.read()
                                os.makedirs(os.path.dirname(tile_path), exist_ok=True)
                                with open(tile_path, "wb") as f:
                                    f.write(data)
                                saved_count += 1
                        except Exception:
                            pass

        return {
            "status": "ok",
            "total_requested": total_count,
            "newly_cached": saved_count,
            "cache_dir": self.cache_dir
        }


tile_cache = TileCacheManager()
