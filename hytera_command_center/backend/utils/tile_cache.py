"""
Hytera Command Center – Offline-Karten Pre-Caching Manager
Ermöglicht das gezielte Herunterladen und Speichern von OSM-Kacheln für einen
Einsatzbereich (Radius um GPS-Koordinate), um vollständige Kartenautonomie
ohne Internetverbindung im Feld zu gewährleisten.
"""

import asyncio
import logging
import math
import os
from typing import Dict, Any, List, Tuple
import httpx

try:
    from ..config import TILES_DIR
except ImportError:
    from backend.config import TILES_DIR

logger = logging.getLogger("tile_cache")


def deg2num(lat_deg: float, lon_deg: float, zoom: int) -> Tuple[int, int]:
    """Berechnet die Web-Mercator X/Y Kachel-Indizes für gegebene Koordinaten und Zoomstufe."""
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return max(0, min(int(n) - 1, xtile)), max(0, min(int(n) - 1, ytile))


def get_tile_bounding_box(lat: float, lon: float, radius_km: float, zoom: int) -> Tuple[int, int, int, int]:
    """Berechnet (min_x, max_x, min_y, max_y) Kacheln für einen Kreisradius um eine Koordinate."""
    d_lat = radius_km / 111.0
    lat_rad = math.radians(lat)
    cos_lat = max(0.1, math.cos(lat_rad))
    d_lon = radius_km / (111.0 * cos_lat)

    north = min(85.0, lat + d_lat)
    south = max(-85.0, lat - d_lat)
    east  = min(180.0, lon + d_lon)
    west  = max(-180.0, lon - d_lon)

    x1, y1 = deg2num(north, west, zoom)
    x2, y2 = deg2num(south, east, zoom)

    min_x, max_x = min(x1, x2), max(x1, x2)
    min_y, max_y = min(y1, y2), max(y1, y2)
    return min_x, max_x, min_y, max_y


async def prefetch_tiles_for_area(
    lat: float,
    lon: float,
    radius_km: float = 3.0,
    min_zoom: int = 12,
    max_zoom: int = 16,
    max_tiles_limit: int = 600,
) -> Dict[str, Any]:
    """
    Lädt alle noch nicht lokal vorhandenen Kacheln für das definierte Einsatzgebiet herunter.
    """
    to_fetch: List[Tuple[int, int, int]] = []
    already_cached = 0

    for z in range(min_zoom, max_zoom + 1):
        min_x, max_x, min_y, max_y = get_tile_bounding_box(lat, lon, radius_km, z)
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                target_path = os.path.join(TILES_DIR, str(z), str(x), f"{y}.png")
                if os.path.isfile(target_path):
                    already_cached += 1
                else:
                    to_fetch.append((z, x, y))

    total_tiles = len(to_fetch) + already_cached
    if len(to_fetch) > max_tiles_limit:
        to_fetch = to_fetch[:max_tiles_limit]

    downloaded = 0
    failed = 0
    headers = {"User-Agent": "HyteraCommandCenter-OfflineCacher/1.0"}
    sem = asyncio.Semaphore(8)

    async def fetch_one(client: httpx.AsyncClient, z: int, x: int, y: int):
        nonlocal downloaded, failed
        url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        target_path = os.path.join(TILES_DIR, str(z), str(x), f"{y}.png")
        async with sem:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with open(target_path, "wb") as f:
                        f.write(resp.content)
                    downloaded += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

    if to_fetch:
        async with httpx.AsyncClient(timeout=6.0) as client:
            tasks = [fetch_one(client, z, x, y) for z, x, y in to_fetch]
            await asyncio.gather(*tasks, return_exceptions=True)

    cache_stats = get_cache_stats()
    return {
        "status": "ok",
        "lat": lat,
        "lon": lon,
        "radius_km": radius_km,
        "min_zoom": min_zoom,
        "max_zoom": max_zoom,
        "downloaded": downloaded,
        "already_cached": already_cached,
        "failed": failed,
        "total_requested": total_tiles,
        "current_cache": cache_stats,
    }


def get_cache_stats() -> Dict[str, Any]:
    """Berechnet die Anzahl und Gesamtgröße der gespeicherten Offline-Kacheln."""
    total_files = 0
    total_bytes = 0
    if os.path.isdir(TILES_DIR):
        for root, _, files in os.walk(TILES_DIR):
            for file in files:
                if file.endswith(".png"):
                    total_files += 1
                    try:
                        total_bytes += os.path.getsize(os.path.join(root, file))
                    except Exception:
                        pass

    return {
        "tile_count": total_files,
        "total_tiles_cached": total_files,
        "size_mb": round(total_bytes / (1024 * 1024), 2),
        "total_size_mb": round(total_bytes / (1024 * 1024), 2),
        "cache_dir": TILES_DIR,
    }


class TileCacheManager:
    """Manager-Klasse für Kachel-Caching."""

    @staticmethod
    async def cache_area_by_radius(
        lat: float,
        lon: float,
        radius_km: float = 3.0,
        min_zoom: int = 12,
        max_zoom: int = 16,
    ) -> Dict[str, Any]:
        res = await prefetch_tiles_for_area(lat, lon, radius_km, min_zoom, max_zoom)
        return {
            "status": res.get("status", "ok"),
            "total_needed": res.get("total_requested", 0),
            "already_cached": res.get("already_cached", 0),
            "downloaded": res.get("downloaded", 0),
            "failed": res.get("failed", 0),
        }

    @staticmethod
    def get_stats() -> Dict[str, Any]:
        return get_cache_stats()

