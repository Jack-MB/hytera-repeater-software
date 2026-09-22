"""
Hytera Tactical Suite - Datenbankmanager
Asynchrone SQLite-Verwaltung für:
- Funkgeräte (mit Notruf-Status, Stockwerk, No-GPS Support)
- GPS-Historie (inkl. RSSI Signalpegel)
- PTT-Protokolle (mit Audio-URL)
- Taktische Marker (mit DV 102 Symbolen)
- Multi-Plan & Etagen-Verwaltung
- Geofences & Gefahrenzonen
- Systemeinstellungen & Funktions-Toggles
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager
import aiosqlite

try:
    from .config import DEFAULT_DB_PATH, POSSIBLE_IDS_PATHS
except (ImportError, ValueError):
    from config import DEFAULT_DB_PATH, POSSIBLE_IDS_PATHS

logger = logging.getLogger("db_manager")
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(MODULE_DIR)


def get_current_iso_timestamp() -> str:
    """Gibt den aktuellen UTC-Zeitstempel im ISO-8601 Format zurück."""
    return datetime.now(timezone.utc).isoformat()


class DatabaseManager:
    """
    Verwaltet asynchrone SQLite-Operationen für die Hytera Tactical Suite.
    Arbeitet 100% autark und lokal im WAL-Modus.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            self.db_path = os.path.abspath(DEFAULT_DB_PATH)
        else:
            self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    @asynccontextmanager
    async def get_connection(self):
        """Asynchroner Kontextmanager für SQLite-Verbindungen mit Row-Factory und WAL-Modus."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            yield conn

    async def init_db(self) -> None:
        """Initialisiert alle Tabellen und Indizes und führt Migrationen durch."""
        async with self.get_connection() as db:
            # 1. Funkgeräte-Tabelle
            await db.execute("""
                CREATE TABLE IF NOT EXISTS radios (
                    radio_id INTEGER PRIMARY KEY,
                    alias TEXT NOT NULL,
                    device_model TEXT DEFAULT '',
                    last_seen TEXT,
                    has_gps INTEGER DEFAULT 1,
                    fixed_lat REAL DEFAULT NULL,
                    fixed_lon REAL DEFAULT NULL,
                    floor_level TEXT DEFAULT 'EG',
                    is_emergency INTEGER DEFAULT 0,
                    emergency_since TEXT DEFAULT NULL,
                    emergency_type TEXT DEFAULT NULL
                );
            """)

            # Migrationen für radios
            cursor = await db.execute("PRAGMA table_info(radios);")
            columns = [row["name"] for row in await cursor.fetchall()]
            if "has_gps" not in columns:
                await db.execute("ALTER TABLE radios ADD COLUMN has_gps INTEGER DEFAULT 1;")
            if "fixed_lat" not in columns:
                await db.execute("ALTER TABLE radios ADD COLUMN fixed_lat REAL DEFAULT NULL;")
            if "fixed_lon" not in columns:
                await db.execute("ALTER TABLE radios ADD COLUMN fixed_lon REAL DEFAULT NULL;")
            if "floor_level" not in columns:
                await db.execute("ALTER TABLE radios ADD COLUMN floor_level TEXT DEFAULT 'EG';")
            if "is_emergency" not in columns:
                await db.execute("ALTER TABLE radios ADD COLUMN is_emergency INTEGER DEFAULT 0;")
            if "emergency_since" not in columns:
                await db.execute("ALTER TABLE radios ADD COLUMN emergency_since TEXT DEFAULT NULL;")
            if "emergency_type" not in columns:
                await db.execute("ALTER TABLE radios ADD COLUMN emergency_type TEXT DEFAULT NULL;")

            # 2. GPS-Historie
            await db.execute("""
                CREATE TABLE IF NOT EXISTS gps_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    radio_id INTEGER NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    speed REAL DEFAULT 0.0,
                    heading REAL DEFAULT 0.0,
                    accuracy REAL DEFAULT 0.0,
                    rssi REAL DEFAULT NULL,
                    FOREIGN KEY (radio_id) REFERENCES radios (radio_id)
                );
            """)
            cursor = await db.execute("PRAGMA table_info(gps_history);")
            g_cols = [row["name"] for row in await cursor.fetchall()]
            if "rssi" not in g_cols:
                await db.execute("ALTER TABLE gps_history ADD COLUMN rssi REAL DEFAULT NULL;")

            # 3. PTT-Protokolle (Push-to-Talk)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ptt_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    radio_id INTEGER NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    typ TEXT DEFAULT 'Gruppe',
                    slot TEXT DEFAULT 'TS1',
                    rssi REAL,
                    audio_url TEXT DEFAULT NULL,
                    FOREIGN KEY (radio_id) REFERENCES radios (radio_id)
                );
            """)
            cursor = await db.execute("PRAGMA table_info(ptt_logs);")
            p_cols = [row["name"] for row in await cursor.fetchall()]
            if "audio_url" not in p_cols:
                await db.execute("ALTER TABLE ptt_logs ADD COLUMN audio_url TEXT DEFAULT NULL;")

            # 4. Taktische Vorfalls- und Einsatzmarker
            await db.execute("""
                CREATE TABLE IF NOT EXISTS markers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    typ TEXT NOT NULL,
                    beschreibung TEXT NOT NULL,
                    prioritaet TEXT NOT NULL,
                    author TEXT DEFAULT 'Operator',
                    tactical_symbol TEXT DEFAULT ''
                );
            """)
            cursor = await db.execute("PRAGMA table_info(markers);")
            m_cols = [row["name"] for row in await cursor.fetchall()]
            if "tactical_symbol" not in m_cols:
                await db.execute("ALTER TABLE markers ADD COLUMN tactical_symbol TEXT DEFAULT '';")

            # 5. Persistente Systemeinstellungen & Funktions-Toggles
            await db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

            # 6. Multi-Plan & Stockwerk-Verwaltung (tactical_plans)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tactical_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    floor_level TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    bounds TEXT NOT NULL,
                    opacity REAL DEFAULT 0.85,
                    is_active INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );
            """)

            # 7. Geofences & Sicherheitsbereiche
            await db.execute("""
                CREATE TABLE IF NOT EXISTS geofences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    zone_type TEXT NOT NULL,
                    shape TEXT NOT NULL,
                    center_lat REAL,
                    center_lon REAL,
                    radius_m REAL,
                    polygon_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
            """)

            # Indizes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_gps_time ON gps_history (timestamp);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_gps_radio_time ON gps_history (radio_id, timestamp);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_ptt_time ON ptt_logs (timestamp);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_ptt_radio ON ptt_logs (radio_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_markers_time ON markers (timestamp);")
            await db.commit()
            logger.info(f"Datenbank initialisiert unter: {self.db_path}")

        await self._import_existing_radios()

    async def _import_existing_radios(self) -> None:
        """Liest vorhandene ids.json ein, falls vorhanden."""
        for path in POSSIBLE_IDS_PATHS:
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for rid_str, info in data.items():
                        try:
                            rid = int(rid_str)
                            alias = info.get("name", f"Radio {rid}")
                            model = info.get("geraet", "")
                            await self.upsert_radio(rid, alias, model)
                        except (ValueError, TypeError):
                            continue
                    logger.info(f"Funkgeräte importiert aus: {path}")
                    break
                except Exception as e:
                    logger.warning(f"Konnte ids.json nicht einlesen: {e}")

    # ── Funkgeräte Methoden ──
    async def upsert_radio(
        self,
        radio_id: int,
        alias: str,
        device_model: str = "",
        last_seen: Optional[str] = None,
        has_gps: Optional[bool] = None,
        fixed_lat: Optional[float] = None,
        fixed_lon: Optional[float] = None,
        floor_level: Optional[str] = None
    ) -> None:
        if last_seen is None:
            last_seen = get_current_iso_timestamp()

        async with self.get_connection() as db:
            cur = await db.execute(
                "SELECT has_gps, fixed_lat, fixed_lon, floor_level FROM radios WHERE radio_id = ?;",
                (radio_id,)
            )
            existing = await cur.fetchone()

            gps_val = int(has_gps) if has_gps is not None else (existing["has_gps"] if existing else 1)
            flat = fixed_lat if fixed_lat is not None else (existing["fixed_lat"] if existing else None)
            flon = fixed_lon if fixed_lon is not None else (existing["fixed_lon"] if existing else None)
            floor = floor_level if floor_level is not None else (existing["floor_level"] if existing else "EG")

            await db.execute("""
                INSERT INTO radios (radio_id, alias, device_model, last_seen, has_gps, fixed_lat, fixed_lon, floor_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(radio_id) DO UPDATE SET
                    alias = CASE WHEN excluded.alias != '' THEN excluded.alias ELSE radios.alias END,
                    device_model = CASE WHEN excluded.device_model != '' THEN excluded.device_model ELSE radios.device_model END,
                    last_seen = excluded.last_seen,
                    has_gps = excluded.has_gps,
                    fixed_lat = excluded.fixed_lat,
                    fixed_lon = excluded.fixed_lon,
                    floor_level = excluded.floor_level;
            """, (radio_id, alias, device_model, last_seen, gps_val, flat, flon, floor))
            await db.commit()

    async def get_radios(self) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("SELECT * FROM radios ORDER BY radio_id ASC;")
            rows = await cursor.fetchall()
            return [
                {
                    "radio_id": r["radio_id"],
                    "alias": r["alias"],
                    "device_model": r["device_model"],
                    "last_seen": r["last_seen"],
                    "has_gps": bool(r["has_gps"]),
                    "fixed_lat": r["fixed_lat"],
                    "fixed_lon": r["fixed_lon"],
                    "floor_level": r["floor_level"] or "EG",
                    "is_emergency": bool(r["is_emergency"]),
                    "emergency_since": r["emergency_since"],
                    "emergency_type": r["emergency_type"]
                }
                for r in rows
            ]

    async def set_radio_gps(self, radio_id: int, has_gps: bool) -> None:
        async with self.get_connection() as db:
            await db.execute("UPDATE radios SET has_gps = ? WHERE radio_id = ?;", (int(has_gps), radio_id))
            await db.commit()

    async def set_radio_fixed_position(self, radio_id: int, lat: Optional[float], lon: Optional[float]) -> None:
        async with self.get_connection() as db:
            await db.execute(
                "UPDATE radios SET fixed_lat = ?, fixed_lon = ? WHERE radio_id = ?;",
                (lat, lon, radio_id)
            )
            await db.commit()

    async def set_radio_floor(self, radio_id: int, floor_level: str) -> None:
        async with self.get_connection() as db:
            await db.execute("UPDATE radios SET floor_level = ? WHERE radio_id = ?;", (floor_level, radio_id))
            await db.commit()

    async def delete_radio(self, radio_id: int) -> bool:
        """Löscht ein Funkgerät aus der Datenbank."""
        async with self.get_connection() as db:
            cur = await db.execute("DELETE FROM radios WHERE radio_id = ?;", (radio_id,))
            await db.commit()
            return cur.rowcount > 0

    async def export_ids_json(self, target_path: Optional[str] = None) -> str:
        """Exportiert alle Funkgeräte zurück in die ids.json zur Synchronisation."""
        radios = await self.get_radios()
        export_dict = {}
        for r in radios:
            export_dict[str(r["radio_id"])] = {
                "name": r["alias"] or f"Radio {r['radio_id']}",
                "geraet": r["device_model"] or ""
            }

        dest = target_path or os.path.join(MODULE_DIR, "ids.json")
        try:
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(export_dict, f, indent=2, ensure_ascii=False)
            logger.info(f"Funkgeräte in {dest} exportiert ({len(export_dict)} Einträge).")
            return dest
        except Exception as e:
            logger.error(f"Fehler beim Export von ids.json: {e}")
            return ""

    # ── Notruf & Totmann (Emergency) ──
    async def trigger_radio_emergency(self, radio_id: int, emergency_type: str = "MAN-DOWN / NOTRUF") -> Dict[str, Any]:
        now_iso = get_current_iso_timestamp()
        async with self.get_connection() as db:
            await db.execute("""
                UPDATE radios
                SET is_emergency = 1, emergency_since = ?, emergency_type = ?
                WHERE radio_id = ?;
            """, (now_iso, emergency_type, radio_id))
            await db.commit()
            cur = await db.execute("SELECT alias, floor_level FROM radios WHERE radio_id = ?;", (radio_id,))
            row = await cur.fetchone()
            alias = row["alias"] if row else f"Radio {radio_id}"
            floor = row["floor_level"] if row else "EG"

        return {
            "radio_id": radio_id,
            "alias": alias,
            "floor_level": floor,
            "is_emergency": True,
            "emergency_since": now_iso,
            "emergency_type": emergency_type
        }

    async def ack_radio_emergency(self, radio_id: int) -> Dict[str, Any]:
        async with self.get_connection() as db:
            await db.execute("""
                UPDATE radios
                SET is_emergency = 0, emergency_since = NULL, emergency_type = NULL
                WHERE radio_id = ?;
            """, (radio_id,))
            await db.commit()
        return {"radio_id": radio_id, "is_emergency": False}

    # ── GPS & PTT Logging ──
    async def insert_gps(
        self,
        radio_id: int,
        lat: float,
        lon: float,
        speed: float = 0.0,
        heading: float = 0.0,
        accuracy: float = 0.0,
        rssi: Optional[float] = None,
        timestamp: Optional[str] = None
    ) -> int:
        if timestamp is None:
            timestamp = get_current_iso_timestamp()

        async with self.get_connection() as db:
            cursor = await db.execute("""
                INSERT INTO gps_history (timestamp, radio_id, lat, lon, speed, heading, accuracy, rssi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (timestamp, radio_id, lat, lon, speed, heading, accuracy, rssi))
            await db.execute("UPDATE radios SET last_seen = ? WHERE radio_id = ?;", (timestamp, radio_id))
            await db.commit()
            return cursor.lastrowid

    async def insert_ptt(
        self,
        radio_id: int,
        duration_ms: int,
        typ: str = "Gruppe",
        slot: str = "TS1",
        rssi: Optional[float] = None,
        audio_url: Optional[str] = None,
        timestamp: Optional[str] = None
    ) -> int:
        if timestamp is None:
            timestamp = get_current_iso_timestamp()

        async with self.get_connection() as db:
            cursor = await db.execute("""
                INSERT INTO ptt_logs (timestamp, radio_id, duration_ms, typ, slot, rssi, audio_url)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (timestamp, radio_id, duration_ms, typ, slot, rssi, audio_url))
            await db.execute("UPDATE radios SET last_seen = ? WHERE radio_id = ?;", (timestamp, radio_id))
            await db.commit()
            return cursor.lastrowid

    async def update_ptt_audio(self, ptt_id: int, audio_url: str) -> None:
        async with self.get_connection() as db:
            await db.execute("UPDATE ptt_logs SET audio_url = ? WHERE id = ?;", (audio_url, ptt_id))
            await db.commit()

    async def get_recent_gps(self) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT g.radio_id, r.alias, r.device_model, r.has_gps, r.fixed_lat, r.fixed_lon,
                       r.floor_level, r.is_emergency, r.emergency_since, r.emergency_type,
                       g.lat, g.lon, g.speed, g.heading, g.accuracy, g.rssi, g.timestamp
                FROM gps_history g
                INNER JOIN (
                    SELECT radio_id, MAX(id) as max_id
                    FROM gps_history
                    GROUP BY radio_id
                ) latest ON g.id = latest.max_id
                JOIN radios r ON g.radio_id = r.radio_id;
            """)
            rows = await cursor.fetchall()
            return [
                {
                    "radio_id": r["radio_id"],
                    "alias": r["alias"],
                    "device_model": r["device_model"],
                    "has_gps": bool(r["has_gps"]),
                    "fixed_lat": r["fixed_lat"],
                    "fixed_lon": r["fixed_lon"],
                    "floor_level": r["floor_level"] or "EG",
                    "is_emergency": bool(r["is_emergency"]),
                    "emergency_since": r["emergency_since"],
                    "emergency_type": r["emergency_type"],
                    "lat": r["lat"],
                    "lon": r["lon"],
                    "speed": r["speed"],
                    "heading": r["heading"],
                    "accuracy": r["accuracy"],
                    "rssi": r["rssi"],
                    "timestamp": r["timestamp"]
                }
                for r in rows
            ]

    async def get_rssi_coverage_points(self, limit: int = 300) -> List[Dict[str, Any]]:
        """Gibt aggregierte HF-Pegelpunkte für die Funkabdeckungs-Heatmap zurück."""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT lat, lon, rssi, radio_id, timestamp
                FROM gps_history
                WHERE rssi IS NOT NULL
                ORDER BY id DESC
                LIMIT ?;
            """, (limit,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_recent_calls(self, limit: int = 15) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT p.id, p.timestamp, p.radio_id, r.alias, p.duration_ms, p.typ, p.slot, p.rssi, p.audio_url
                FROM ptt_logs p
                LEFT JOIN radios r ON p.radio_id = r.radio_id
                ORDER BY p.id DESC
                LIMIT ?;
            """, (limit,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Taktische Marker & DV 102 ──
    async def insert_marker(
        self,
        lat: float,
        lon: float,
        typ: str,
        beschreibung: str,
        prioritaet: str = "normal",
        author: str = "Operator",
        tactical_symbol: str = "",
        timestamp: Optional[str] = None
    ) -> int:
        if timestamp is None:
            timestamp = get_current_iso_timestamp()

        async with self.get_connection() as db:
            cursor = await db.execute("""
                INSERT INTO markers (timestamp, lat, lon, typ, beschreibung, prioritaet, author, tactical_symbol)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (timestamp, lat, lon, typ, beschreibung, prioritaet, author, tactical_symbol))
            await db.commit()
            return cursor.lastrowid

    async def get_markers(self) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("SELECT * FROM markers ORDER BY id DESC;")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def delete_marker(self, marker_id: int) -> bool:
        async with self.get_connection() as db:
            cursor = await db.execute("DELETE FROM markers WHERE id = ?;", (marker_id,))
            await db.commit()
            return cursor.rowcount > 0

    # ── Multi-Plan & Stockwerke ──
    async def insert_plan(
        self,
        name: str,
        floor_level: str,
        image_url: str,
        bounds: List[List[float]],
        opacity: float = 0.85,
        is_active: bool = False
    ) -> int:
        now_iso = get_current_iso_timestamp()
        bounds_json = json.dumps(bounds)
        async with self.get_connection() as db:
            if is_active:
                await db.execute("UPDATE tactical_plans SET is_active = 0 WHERE floor_level = ?;", (floor_level,))
            cursor = await db.execute("""
                INSERT INTO tactical_plans (name, floor_level, image_url, bounds, opacity, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (name, floor_level, image_url, bounds_json, opacity, int(is_active), now_iso))
            await db.commit()
            return cursor.lastrowid

    async def get_plans(self) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("SELECT * FROM tactical_plans ORDER BY id ASC;")
            rows = await cursor.fetchall()
            result = []
            for r in rows:
                item = dict(r)
                try:
                    item["bounds"] = json.loads(item["bounds"])
                except Exception:
                    item["bounds"] = []
                item["is_active"] = bool(item["is_active"])
                result.append(item)
            return result

    async def set_active_plan(self, plan_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as db:
            cur = await db.execute("SELECT floor_level FROM tactical_plans WHERE id = ?;", (plan_id,))
            row = await cur.fetchone()
            if not row:
                return None
            floor = row["floor_level"]
            await db.execute("UPDATE tactical_plans SET is_active = 0 WHERE floor_level = ?;", (floor,))
            await db.execute("UPDATE tactical_plans SET is_active = 1 WHERE id = ?;", (plan_id,))
            await db.commit()
            cur2 = await db.execute("SELECT * FROM tactical_plans WHERE id = ?;", (plan_id,))
            updated = await cur2.fetchone()
            res = dict(updated)
            res["bounds"] = json.loads(res["bounds"])
            res["is_active"] = True
            return res

    async def delete_plan(self, plan_id: int) -> bool:
        async with self.get_connection() as db:
            cursor = await db.execute("DELETE FROM tactical_plans WHERE id = ?;", (plan_id,))
            await db.commit()
            return cursor.rowcount > 0

    # ── Geofences & Gefahrenzonen ──
    async def insert_geofence(
        self,
        name: str,
        zone_type: str,
        shape: str,
        center_lat: Optional[float] = None,
        center_lon: Optional[float] = None,
        radius_m: Optional[float] = None,
        polygon_coords: Optional[List[List[float]]] = None
    ) -> int:
        now_iso = get_current_iso_timestamp()
        poly_json = json.dumps(polygon_coords or [])
        async with self.get_connection() as db:
            cursor = await db.execute("""
                INSERT INTO geofences (name, zone_type, shape, center_lat, center_lon, radius_m, polygon_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (name, zone_type, shape, center_lat, center_lon, radius_m, poly_json, now_iso))
            await db.commit()
            return cursor.lastrowid

    async def get_geofences(self) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("SELECT * FROM geofences ORDER BY id DESC;")
            rows = await cursor.fetchall()
            res = []
            for r in rows:
                item = dict(r)
                try:
                    item["polygon_coords"] = json.loads(item.get("polygon_json") or "[]")
                except Exception:
                    item["polygon_coords"] = []
                res.append(item)
            return res

    async def delete_geofence(self, geofence_id: int) -> bool:
        async with self.get_connection() as db:
            cursor = await db.execute("DELETE FROM geofences WHERE id = ?;", (geofence_id,))
            await db.commit()
            return cursor.rowcount > 0

    # ── Replay & Zeitstrahl ──
    async def get_state_at_timestamp(self, timestamp: str) -> Dict[str, Any]:
        async with self.get_connection() as db:
            cur_gps = await db.execute("""
                SELECT g.radio_id, r.alias, r.device_model, r.has_gps, r.fixed_lat, r.fixed_lon,
                       r.floor_level, r.is_emergency,
                       g.lat, g.lon, g.speed, g.heading, g.accuracy, g.rssi, g.timestamp
                FROM gps_history g
                INNER JOIN (
                    SELECT radio_id, MAX(timestamp) as max_ts
                    FROM gps_history
                    WHERE timestamp <= ?
                    GROUP BY radio_id
                ) latest ON g.radio_id = latest.radio_id AND g.timestamp = latest.max_ts
                JOIN radios r ON g.radio_id = r.radio_id;
            """, (timestamp,))
            gps_rows = await cur_gps.fetchall()

            cur_ptt = await db.execute("""
                SELECT p.*, r.alias
                FROM ptt_logs p
                JOIN radios r ON p.radio_id = r.radio_id
                WHERE p.timestamp <= ?
                ORDER BY p.timestamp DESC
                LIMIT 1;
            """, (timestamp,))
            last_ptt = await cur_ptt.fetchone()

            cur_markers = await db.execute("SELECT * FROM markers WHERE timestamp <= ? ORDER BY id ASC;", (timestamp,))
            marker_rows = await cur_markers.fetchall()

            return {
                "timestamp": timestamp,
                "radios": [dict(r) for r in gps_rows],
                "active_ptt": dict(last_ptt) if last_ptt else None,
                "markers": [dict(m) for m in marker_rows]
            }

    async def get_timeline_events(
        self,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            where_clauses = []
            params = []
            if from_ts:
                where_clauses.append("timestamp >= ?")
                params.append(from_ts)
            if to_ts:
                where_clauses.append("timestamp <= ?")
                params.append(to_ts)
            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            ptt_cur = await db.execute(f"""
                SELECT p.id, p.timestamp, p.duration_ms, p.typ, p.slot, p.radio_id, r.alias, p.audio_url
                FROM ptt_logs p
                JOIN radios r ON p.radio_id = r.radio_id
                {where_sql}
                ORDER BY p.timestamp DESC
                LIMIT ?;
            """, (*params, limit))
            ptt_rows = await ptt_cur.fetchall()

            marker_cur = await db.execute(f"""
                SELECT id, timestamp, typ, beschreibung, prioritaet, author, tactical_symbol
                FROM markers
                {where_sql}
                ORDER BY timestamp DESC
                LIMIT ?;
            """, (*params, limit))
            marker_rows = await marker_cur.fetchall()

            items = []
            for r in ptt_rows:
                items.append({
                    "id": f"ptt_{r['id']}",
                    "content": f"🎙️ {r['alias']} ({r['slot']})",
                    "start": r["timestamp"],
                    "className": "tl-item-ptt",
                    "group": "radio",
                    "audio_url": r["audio_url"]
                })
            for m in marker_rows:
                sym_badge = f"[{m['tactical_symbol'].upper()}] " if m.get("tactical_symbol") else ""
                items.append({
                    "id": f"marker_{m['id']}",
                    "content": f"📍 {sym_badge}{m['typ']}: {m['beschreibung']}",
                    "start": m["timestamp"],
                    "className": f"tl-item-marker tl-pri-{m['prioritaet']}",
                    "group": "incident"
                })
            return items

    # ── Einstellungen & Settings ──
    async def get_setting(self, key: str, default: Any = None) -> Any:
        async with self.get_connection() as db:
            cursor = await db.execute("SELECT value FROM settings WHERE key = ?;", (key,))
            row = await cursor.fetchone()
            if row:
                try:
                    return json.loads(row["value"])
                except Exception:
                    return row["value"]
            return default

    async def save_setting(self, key: str, value: Any) -> None:
        val_str = json.dumps(value) if not isinstance(value, str) else value
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """, (key, val_str))
            await db.commit()

    async def get_all_settings(self) -> Dict[str, Any]:
        async with self.get_connection() as db:
            cursor = await db.execute("SELECT key, value FROM settings;")
            rows = await cursor.fetchall()
            res = {}
            for r in rows:
                try:
                    res[r["key"]] = json.loads(r["value"])
                except Exception:
                    res[r["key"]] = r["value"]
            return res

    # ── Einsatz-Stammdaten & Missions-Verwaltung ──
    async def get_mission_data(self) -> Dict[str, Any]:
        """Liefert die aktuellen Einsatz-Stammdaten aus der Datenbank."""
        mission = await self.get_setting("mission_data")
        now_ts = get_current_iso_timestamp()
        default_mission = {
            "mission_title": "Neuer Einsatz",
            "mission_code": f"EINSATZ-{now_ts[:10].replace('-', '')}-01",
            "mission_leader": "",
            "mission_location": "",
            "mission_channel": "Kanal 1 / TS1 & TS2",
            "mission_status": "preparation",
            "mission_start_time": now_ts,
            "mission_notes": ""
        }
        if isinstance(mission, dict):
            default_mission.update(mission)
        return default_mission

    async def save_mission_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Speichert die Einsatz-Stammdaten."""
        current = await self.get_mission_data()
        current.update(data)
        await self.save_setting("mission_data", current)
        return current


db_manager = DatabaseManager()
