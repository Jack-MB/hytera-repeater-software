"""
Hytera Lageplan & Tactical Timeline - Datenbankmanager (AGENT 1: Database Architect)
Asynchrone SQLite-Verwaltung für Funkgeräte, GPS-Historie, PTT-Übertragungen und taktische Einsatzmarker.
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import aiosqlite

from .config import DEFAULT_DB_PATH, POSSIBLE_IDS_PATHS

from contextlib import asynccontextmanager

logger = logging.getLogger("db_manager")


def get_current_iso_timestamp() -> str:
    """Gibt den aktuellen UTC-Zeitstempel im ISO-8601 Format zurück."""
    return datetime.now(timezone.utc).isoformat()


class DatabaseManager:
    """
    Verwaltet asynchrone SQLite-Operationen für das Hytera Lageplan-Modul.
    Löst den Pfad zur .db-Datei dynamisch relativ zu dieser Datei auf.
    """

    def __init__(self, db_path: Optional[str] = None):
        # Dynamische Auflösung des Datenbankpfads
        if db_path is None:
            self.db_path = os.path.abspath(DEFAULT_DB_PATH)
        else:
            self.db_path = os.path.abspath(db_path)
        
        # Sicherstellen, dass das übergeordnete Verzeichnis existiert
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
        """
        Initialisiert alle benötigten Tabellen und Indizes.
        Führt automatische Schema-Migrationen durch und liest vorhandene Geräte aus ids.json ein.
        """
        async with self.get_connection() as db:
            # 1. Funkgeräte-Tabelle (mit Support für Geräte ohne GPS und manuelle feste Koordinaten)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS radios (
                    radio_id INTEGER PRIMARY KEY,
                    alias TEXT NOT NULL,
                    device_model TEXT DEFAULT '',
                    last_seen TEXT,
                    has_gps INTEGER DEFAULT 1,
                    fixed_lat REAL DEFAULT NULL,
                    fixed_lon REAL DEFAULT NULL
                );
            """)

            # Automatische Migration für bestehende Datenbanken
            cursor = await db.execute("PRAGMA table_info(radios);")
            columns = [row["name"] for row in await cursor.fetchall()]
            if "has_gps" not in columns:
                await db.execute("ALTER TABLE radios ADD COLUMN has_gps INTEGER DEFAULT 1;")
            if "fixed_lat" not in columns:
                await db.execute("ALTER TABLE radios ADD COLUMN fixed_lat REAL DEFAULT NULL;")
            if "fixed_lon" not in columns:
                await db.execute("ALTER TABLE radios ADD COLUMN fixed_lon REAL DEFAULT NULL;")

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
                    FOREIGN KEY (radio_id) REFERENCES radios (radio_id)
                );
            """)

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
                    FOREIGN KEY (radio_id) REFERENCES radios (radio_id)
                );
            """)

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
                    author TEXT DEFAULT 'Operator'
                );
            """)

            # 5. Persistente Systemeinstellungen & Funktions-Toggles
            await db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

            # Indizes für ultraschnelle Zeitreihen- und Replay-Abfragen
            await db.execute("CREATE INDEX IF NOT EXISTS idx_gps_time ON gps_history (timestamp);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_gps_radio_time ON gps_history (radio_id, timestamp);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_ptt_time ON ptt_logs (timestamp);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_ptt_radio ON ptt_logs (radio_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_markers_time ON markers (timestamp);")

            await db.commit()
            logger.info(f"Datenbank erfolgreich initialisiert unter: {self.db_path}")

        # Automatischer Import von ids.json
        await self._import_existing_radios()

    async def _import_existing_radios(self) -> None:
        """Prüft, ob eine ids.json existiert, und synchronisiert Funkgeräte-Rufnamen."""
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
                    logger.info(f"Funkgeräte erfolgreich importiert aus: {path}")
                    break
                except Exception as e:
                    logger.warning(f"Konnte ids.json unter {path} nicht einlesen: {e}")

    async def upsert_radio(
        self,
        radio_id: int,
        alias: str,
        device_model: str = "",
        last_seen: Optional[str] = None,
        has_gps: Optional[bool] = None,
        fixed_lat: Optional[float] = None,
        fixed_lon: Optional[float] = None
    ) -> None:
        """Fügt ein Funkgerät hinzu oder aktualisiert den Alias, Status und feste Koordinaten."""
        if last_seen is None:
            last_seen = get_current_iso_timestamp()

        async with self.get_connection() as db:
            # Prüfen ob bereits vorhanden
            cur = await db.execute("SELECT has_gps, fixed_lat, fixed_lon FROM radios WHERE radio_id = ?;", (radio_id,))
            existing = await cur.fetchone()

            gps_val = int(has_gps) if has_gps is not None else (existing["has_gps"] if existing else 1)
            flat = fixed_lat if fixed_lat is not None else (existing["fixed_lat"] if existing else None)
            flon = fixed_lon if fixed_lon is not None else (existing["fixed_lon"] if existing else None)

            await db.execute("""
                INSERT INTO radios (radio_id, alias, device_model, last_seen, has_gps, fixed_lat, fixed_lon)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(radio_id) DO UPDATE SET
                    alias = CASE WHEN excluded.alias != '' THEN excluded.alias ELSE radios.alias END,
                    device_model = CASE WHEN excluded.device_model != '' THEN excluded.device_model ELSE radios.device_model END,
                    last_seen = excluded.last_seen,
                    has_gps = excluded.has_gps,
                    fixed_lat = excluded.fixed_lat,
                    fixed_lon = excluded.fixed_lon;
            """, (radio_id, alias, device_model, last_seen, gps_val, flat, flon))
            await db.commit()

    async def set_radio_gps(self, radio_id: int, has_gps: bool) -> None:
        """Schaltet den GPS-Status eines Funkgeräts um (z.B. Handfunkgerät ohne GPS)."""
        async with self.get_connection() as db:
            await db.execute(
                "UPDATE radios SET has_gps = ? WHERE radio_id = ?;",
                (1 if has_gps else 0, radio_id)
            )
            await db.commit()

    async def set_radio_fixed_position(self, radio_id: int, lat: Optional[float], lon: Optional[float]) -> None:
        """Weist einem Funkgerät ohne GPS einen festen manuellen Einsatzstandort zu."""
        async with self.get_connection() as db:
            await db.execute(
                "UPDATE radios SET fixed_lat = ?, fixed_lon = ? WHERE radio_id = ?;",
                (lat, lon, radio_id)
            )
            await db.commit()

    async def get_radios(self) -> List[Dict[str, Any]]:
        """Gibt eine Liste aller bekannten Funkgeräte inkl. GPS-Status und fester Position zurück."""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT radio_id, alias, device_model, last_seen, has_gps, fixed_lat, fixed_lon
                FROM radios
                ORDER BY radio_id ASC;
            """)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_all_settings(self) -> Dict[str, Any]:
        """Liest alle persistenten Systemeinstellungen aus."""
        async with self.get_connection() as db:
            cursor = await db.execute("SELECT key, value FROM settings;")
            rows = await cursor.fetchall()
            res = {}
            for row in rows:
                try:
                    res[row["key"]] = json.loads(row["value"])
                except Exception:
                    res[row["key"]] = row["value"]
            return res

    async def save_setting(self, key: str, value: Any) -> None:
        """Speichert eine Systemeinstellung."""
        val_str = json.dumps(value)
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """, (key, val_str))
            await db.commit()

    async def insert_gps(
        self,
        radio_id: int,
        lat: float,
        lon: float,
        speed: float = 0.0,
        heading: float = 0.0,
        accuracy: float = 0.0,
        timestamp: Optional[str] = None
    ) -> int:
        """
        Speichert einen neuen GPS-Fix in der Historie und aktualisiert den Funkgeräte-Status.
        """
        if timestamp is None:
            timestamp = get_current_iso_timestamp()

        async with self.get_connection() as db:
            # 1. Sicherstellen, dass das Funkgerät existiert
            await db.execute("""
                INSERT INTO radios (radio_id, alias, device_model, last_seen)
                VALUES (?, ?, '', ?)
                ON CONFLICT(radio_id) DO UPDATE SET last_seen = excluded.last_seen;
            """, (radio_id, f"Radio {radio_id}", timestamp))

            # 2. GPS-Fix eintragen
            cursor = await db.execute("""
                INSERT INTO gps_history (timestamp, radio_id, lat, lon, speed, heading, accuracy)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (timestamp, radio_id, lat, lon, speed, heading, accuracy))
            row_id = cursor.lastrowid
            await db.commit()
            return row_id

    async def insert_ptt(
        self,
        radio_id: int,
        duration_ms: int,
        typ: str = "Gruppe",
        slot: str = "TS1",
        rssi: Optional[float] = None,
        timestamp: Optional[str] = None
    ) -> int:
        """
        Speichert einen abgeschlossenen PTT-Sprechvorgang.
        """
        if timestamp is None:
            timestamp = get_current_iso_timestamp()

        async with self.get_connection() as db:
            # Funkgerät aktualisieren
            await db.execute("""
                INSERT INTO radios (radio_id, alias, device_model, last_seen)
                VALUES (?, ?, '', ?)
                ON CONFLICT(radio_id) DO UPDATE SET last_seen = excluded.last_seen;
            """, (radio_id, f"Radio {radio_id}", timestamp))

            cursor = await db.execute("""
                INSERT INTO ptt_logs (timestamp, radio_id, duration_ms, typ, slot, rssi)
                VALUES (?, ?, ?, ?, ?, ?);
            """, (timestamp, radio_id, duration_ms, typ, slot, rssi))
            row_id = cursor.lastrowid
            await db.commit()
            return row_id

    async def get_recent_calls(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Gibt die letzten PTT-Funksprüche für den Leitstellen-Call-Log zurück."""
        async with self.get_connection() as db:
            query = """
                SELECT p.id, p.timestamp, p.radio_id, COALESCE(r.alias, 'Radio ' || p.radio_id) as alias,
                       p.duration_ms, p.typ, p.slot, p.rssi
                FROM ptt_logs p
                LEFT JOIN radios r ON p.radio_id = r.radio_id
                ORDER BY p.id DESC
                LIMIT ?;
            """
            cursor = await db.execute(query, (limit,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def insert_marker(
        self,
        lat: float,
        lon: float,
        typ: str,
        beschreibung: str,
        prioritaet: str,
        author: str = "Operator",
        timestamp: Optional[str] = None
    ) -> int:
        """Fügt einen neuen manuellen oder automatischen Vorfallsmarker hinzu."""
        if timestamp is None:
            timestamp = get_current_iso_timestamp()

        async with self.get_connection() as db:
            cursor = await db.execute("""
                INSERT INTO markers (timestamp, lat, lon, typ, beschreibung, prioritaet, author)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (timestamp, lat, lon, typ, beschreibung, prioritaet, author))
            row_id = cursor.lastrowid
            await db.commit()
            return row_id

    async def delete_marker(self, marker_id: int) -> bool:
        """Löscht einen Marker anhand seiner ID."""
        async with self.get_connection() as db:
            cursor = await db.execute("DELETE FROM markers WHERE id = ?;", (marker_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def get_markers(self, from_ts: Optional[str] = None, to_ts: Optional[str] = None) -> List[Dict[str, Any]]:
        """Gibt alle Marker zurück, optional gefiltert nach Zeitbereich."""
        query = "SELECT id, timestamp, lat, lon, typ, beschreibung, prioritaet, author FROM markers"
        params = []

        if from_ts and to_ts:
            query += " WHERE timestamp >= ? AND timestamp <= ?"
            params.extend([from_ts, to_ts])
        elif from_ts:
            query += " WHERE timestamp >= ?"
            params.append(from_ts)
        elif to_ts:
            query += " WHERE timestamp <= ?"
            params.append(to_ts)

        query += " ORDER BY timestamp ASC;"

        async with self.get_connection() as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_recent_gps(self) -> List[Dict[str, Any]]:
        """
        Ermittelt für jedes bekannte Funkgerät den aktuellsten GPS-Fix zusammen mit dem Funkgeräte-Alias.
        """
        query = """
            SELECT g.id, g.timestamp, g.radio_id, r.alias, r.device_model,
                   r.has_gps, r.fixed_lat, r.fixed_lon,
                   g.lat, g.lon, g.speed, g.heading, g.accuracy
            FROM gps_history g
            JOIN radios r ON g.radio_id = r.radio_id
            WHERE g.id IN (
                SELECT MAX(id) FROM gps_history GROUP BY radio_id
            )
            ORDER BY r.radio_id ASC;
        """
        async with self.get_connection() as db:
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            res = [dict(row) for row in rows]
            found_ids = {r["radio_id"] for r in res}

            # Auch Radios berücksichtigen, die feste manuelle Koordinaten haben
            fixed_cur = await db.execute("""
                SELECT radio_id, alias, device_model, last_seen, has_gps, fixed_lat, fixed_lon
                FROM radios
                WHERE fixed_lat IS NOT NULL AND fixed_lon IS NOT NULL;
            """)
            fixed_rows = await fixed_cur.fetchall()
            for f in fixed_rows:
                if f["radio_id"] not in found_ids:
                    res.append({
                        "id": None,
                        "timestamp": f["last_seen"] or get_current_iso_timestamp(),
                        "radio_id": f["radio_id"],
                        "alias": f["alias"],
                        "device_model": f["device_model"],
                        "has_gps": f["has_gps"],
                        "fixed_lat": f["fixed_lat"],
                        "fixed_lon": f["fixed_lon"],
                        "lat": f["fixed_lat"],
                        "lon": f["fixed_lon"],
                        "speed": 0.0,
                        "heading": 0.0,
                        "accuracy": 0.0
                    })
            return res

    async def get_radio_breadcrumbs(self, radio_id: int, limit: int = 40, before_ts: Optional[str] = None) -> List[Dict[str, Any]]:
        """Gibt die letzten N Positionen eines Funkgeräts zur Spurdarstellung zurück."""
        query = "SELECT timestamp, lat, lon, speed, heading FROM gps_history WHERE radio_id = ?"
        params: List[Any] = [radio_id]
        if before_ts:
            query += " AND timestamp <= ?"
            params.append(before_ts)
        query += " ORDER BY id DESC LIMIT ?;"
        params.append(limit)

        async with self.get_connection() as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            # In chronologischer Reihenfolge zurückgeben
            res = [dict(row) for row in rows]
            res.reverse()
            return res

    async def get_state_at_timestamp(self, target_iso_ts: str) -> Dict[str, Any]:
        """
        KERNFUNKTION FÜR DEN ZEITREISE- / REPLAY-MODUS ("Zurückspulen"):
        Ermittelt den exakten Gesamtzustand des Lageplans zum angegebenen historischen Zeitpunkt:
        1. Die jeweils letzte bekannte Position jedes Funkgeräts bis einschließlich target_iso_ts.
        2. Alle Einsatzmarker, die bis zu diesem Zeitpunkt angelegt wurden.
        3. Aktive PTT-Events zu diesem Zeitpunkt (innerhalb des Sendeintervalls).
        4. Bewegungspfade (Breadcrumbs) bis zu diesem Zeitpunkt.
        """
        async with self.get_connection() as db:
            # 1. Letzte Position jedes Funkgeräts vor oder am target_iso_ts
            gps_query = """
                SELECT g.timestamp, g.radio_id, r.alias, r.device_model,
                       r.has_gps, r.fixed_lat, r.fixed_lon,
                       g.lat, g.lon, g.speed, g.heading, g.accuracy
                FROM gps_history g
                JOIN radios r ON g.radio_id = r.radio_id
                WHERE g.id IN (
                    SELECT MAX(id)
                    FROM gps_history
                    WHERE timestamp <= ?
                    GROUP BY radio_id
                );
            """
            cursor = await db.execute(gps_query, (target_iso_ts,))
            gps_rows = await cursor.fetchall()
            radios_state = [dict(r) for r in gps_rows]

            # 2. Marker, die bis zu diesem Zeitpunkt existierten
            markers_query = """
                SELECT id, timestamp, lat, lon, typ, beschreibung, prioritaet, author
                FROM markers
                WHERE timestamp <= ?
                ORDER BY timestamp ASC;
            """
            cursor = await db.execute(markers_query, (target_iso_ts,))
            marker_rows = await cursor.fetchall()
            markers_state = [dict(r) for r in marker_rows]

            # 3. PTT-Events in einem 10-Sekunden-Fenster um den Zeitstempel
            ptt_query = """
                SELECT p.id, p.timestamp, p.radio_id, r.alias, p.duration_ms, p.typ, p.slot, p.rssi
                FROM ptt_logs p
                JOIN radios r ON p.radio_id = r.radio_id
                WHERE p.timestamp <= ?
                ORDER BY p.id DESC
                LIMIT 5;
            """
            cursor = await db.execute(ptt_query, (target_iso_ts,))
            ptt_rows = await cursor.fetchall()
            recent_ptt = [dict(r) for r in ptt_rows]

        # Spuren der Funkgeräte für diesen Zeitpunkt ermitteln
        breadcrumbs: Dict[int, List[Dict[str, Any]]] = {}
        for r in radios_state:
            rid = r["radio_id"]
            breadcrumbs[rid] = await self.get_radio_breadcrumbs(rid, limit=20, before_ts=target_iso_ts)

        return {
            "query_timestamp": target_iso_ts,
            "radios": radios_state,
            "markers": markers_state,
            "recent_ptt": recent_ptt,
            "breadcrumbs": breadcrumbs
        }

    async def get_timeline_events(self, from_ts: Optional[str] = None, to_ts: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        """
        Formatiert Ereignisse für vis.js Timeline.
        PTT-Events werden als Zeitbalken oder Boxen dargestellt, Marker als taktische Punkte/Flags.
        """
        timeline_items: List[Dict[str, Any]] = []

        async with self.get_connection() as db:
            # 1. PTT-Events abrufen
            ptt_sql = """
                SELECT p.id, p.timestamp, p.radio_id, r.alias, p.duration_ms, p.typ, p.slot
                FROM ptt_logs p
                JOIN radios r ON p.radio_id = r.radio_id
            """
            params: List[Any] = []
            if from_ts and to_ts:
                ptt_sql += " WHERE p.timestamp >= ? AND p.timestamp <= ?"
                params.extend([from_ts, to_ts])
            ptt_sql += " ORDER BY p.timestamp DESC LIMIT ?;"
            params.append(limit)

            cursor = await db.execute(ptt_sql, params)
            ptt_rows = await cursor.fetchall()

            for row in ptt_rows:
                # Dauer berücksichtigen für Timeline-Start und Ende
                start_iso = row["timestamp"]
                # Start und Endzeitpunkt berechnen
                try:
                    dt_start = datetime.fromisoformat(start_iso)
                    duration_sec = (row["duration_ms"] or 1000) / 1000.0
                    dt_end = datetime.fromtimestamp(dt_start.timestamp() + duration_sec, tz=dt_start.tzinfo)
                    end_iso = dt_end.isoformat()
                except Exception:
                    end_iso = start_iso

                timeline_items.append({
                    "id": f"ptt_{row['id']}",
                    "group": f"radio_{row['radio_id']}",
                    "content": f"🎙️ {row['alias']} ({row['typ']} / {row['slot']})",
                    "start": start_iso,
                    "end": end_iso,
                    "type": "range" if row["duration_ms"] and row["duration_ms"] > 1000 else "box",
                    "className": f"timeline-ptt slot-{row['slot'].lower()}",
                    "title": f"PTT Funkspruch: {row['alias']} (ID {row['radio_id']})\nDauer: {row['duration_ms']}ms\nSlot: {row['slot']}",
                    "radio_id": row["radio_id"]
                })

            # 2. Marker abrufen
            marker_sql = "SELECT id, timestamp, lat, lon, typ, beschreibung, prioritaet, author FROM markers"
            m_params: List[Any] = []
            if from_ts and to_ts:
                marker_sql += " WHERE timestamp >= ? AND timestamp <= ?"
                m_params.extend([from_ts, to_ts])
            marker_sql += " ORDER BY timestamp DESC LIMIT ?;"
            m_params.append(limit)

            cursor = await db.execute(marker_sql, m_params)
            marker_rows = await cursor.fetchall()

            for row in marker_rows:
                timeline_items.append({
                    "id": f"marker_{row['id']}",
                    "group": "markers",
                    "content": f"📍 [{row['typ'].upper()}] {row['beschreibung']}",
                    "start": row["timestamp"],
                    "type": "point",
                    "className": f"timeline-marker prio-{row['prioritaet'].lower()} type-{row['typ'].lower()}",
                    "title": f"Marker: {row['beschreibung']}\nPriorität: {row['prioritaet']}\nErstellt: {row['timestamp']}",
                    "marker_id": row["id"],
                    "lat": row["lat"],
                    "lon": row["lon"]
                })

        return timeline_items

    async def get_setting(self, key: str, default: Any = None) -> Any:
        """Liest eine Systemeinstellung aus der Datenbank."""
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
        """Speichert oder aktualisiert eine Systemeinstellung als JSON."""
        val_str = json.dumps(value) if not isinstance(value, str) else value
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """, (key, val_str))
            await db.commit()

    async def get_all_settings(self) -> Dict[str, Any]:
        """Gibt alle gespeicherten Systemeinstellungen zurück."""
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


# Globale Standard-Instanz
db_manager = DatabaseManager()
