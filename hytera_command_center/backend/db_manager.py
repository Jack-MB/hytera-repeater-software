"""
Hytera Command Center – Datenbankmanager
Asynchrone SQLite-Verwaltung (aiosqlite, WAL-Modus) für alle Systemdaten.

Tabellen:
  radios          – Funkgeräte (ID, Alias, Modell, GPS, Stockwerk, Notfall)
  gps_history     – GPS-Positionshistorie
  ptt_logs        – PTT-Protokoll (Funkverkehr)
  sms_messages    – SMS/TMP-Nachrichten
  radio_events    – RRS-Ereignisse (Online/Offline)
  markers         – Taktische Marker (DV 102)
  tactical_plans  – Lagepläne / Stockwerk-Pläne
  geofences       – Gefahrenzonen
  snmp_events     – SNMP-Alarmhistorie des Repeaters
  settings        – Persistente Systemeinstellungen
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

import aiosqlite

try:
    from .config import DB_PATH, IDS_JSON_PATH
except ImportError:
    from config import DB_PATH, IDS_JSON_PATH

logger = logging.getLogger("db_manager")


def get_current_iso_timestamp() -> str:
    """Gibt den aktuellen UTC-Zeitstempel im ISO-8601-Format zurück."""
    return datetime.now(timezone.utc).isoformat()


class DatabaseManager:
    """
    Verwaltet alle asynchronen SQLite-Datenbankoperationen.
    Nutzt WAL-Modus für maximale Parallelität und Robustheit.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = os.path.abspath(db_path or DB_PATH)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    @asynccontextmanager
    async def get_connection(self):
        """Asynchoner Kontextmanager mit WAL-Modus und Row-Factory."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute("PRAGMA foreign_keys=ON;")
            await conn.execute("PRAGMA busy_timeout=5000;")
            yield conn

    # ── Datenbank-Initialisierung ──────────────────────────────────────────

    async def init_db(self) -> None:
        """Erstellt alle Tabellen und führt Migrationen durch."""
        async with self.get_connection() as db:

            # 1. Funkgeräte
            await db.execute("""
                CREATE TABLE IF NOT EXISTS radios (
                    radio_id       INTEGER PRIMARY KEY,
                    alias          TEXT    NOT NULL DEFAULT '',
                    device_model   TEXT    DEFAULT '',
                    last_seen      TEXT,
                    has_gps        INTEGER DEFAULT 1,
                    fixed_lat      REAL    DEFAULT NULL,
                    fixed_lon      REAL    DEFAULT NULL,
                    floor_level    TEXT    DEFAULT 'EG',
                    is_emergency   INTEGER DEFAULT 0,
                    emergency_since TEXT   DEFAULT NULL,
                    emergency_type TEXT    DEFAULT NULL,
                    online         INTEGER DEFAULT 0,
                    last_rssi      REAL    DEFAULT NULL
                );
            """)
            # Migrationen für radios
            cur = await db.execute("PRAGMA table_info(radios);")
            r_cols = {row["name"] for row in await cur.fetchall()}
            for col, typ in [
                ("has_gps",         "INTEGER DEFAULT 1"),
                ("fixed_lat",       "REAL    DEFAULT NULL"),
                ("fixed_lon",       "REAL    DEFAULT NULL"),
                ("floor_level",     "TEXT    DEFAULT 'EG'"),
                ("is_emergency",    "INTEGER DEFAULT 0"),
                ("emergency_since", "TEXT    DEFAULT NULL"),
                ("emergency_type",  "TEXT    DEFAULT NULL"),
                ("online",          "INTEGER DEFAULT 0"),
                ("last_rssi",       "REAL    DEFAULT NULL"),
            ]:
                if col not in r_cols:
                    await db.execute(f"ALTER TABLE radios ADD COLUMN {col} {typ};")

            # 2. GPS-Historie
            await db.execute("""
                CREATE TABLE IF NOT EXISTS gps_history (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT    NOT NULL,
                    radio_id  INTEGER NOT NULL,
                    lat       REAL    NOT NULL,
                    lon       REAL    NOT NULL,
                    speed     REAL    DEFAULT 0.0,
                    heading   REAL    DEFAULT 0.0,
                    accuracy  REAL    DEFAULT 0.0,
                    rssi      REAL    DEFAULT NULL,
                    FOREIGN KEY (radio_id) REFERENCES radios (radio_id)
                );
            """)
            cur = await db.execute("PRAGMA table_info(gps_history);")
            g_cols = {row["name"] for row in await cur.fetchall()}
            if "rssi" not in g_cols:
                await db.execute("ALTER TABLE gps_history ADD COLUMN rssi REAL DEFAULT NULL;")

            # 3. PTT-Protokoll
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ptt_logs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    radio_id    INTEGER NOT NULL,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    typ         TEXT    DEFAULT 'Gruppe',
                    slot        TEXT    DEFAULT 'TS1',
                    rssi        REAL    DEFAULT NULL,
                    audio_url   TEXT    DEFAULT NULL,
                    FOREIGN KEY (radio_id) REFERENCES radios (radio_id)
                );
            """)
            cur = await db.execute("PRAGMA table_info(ptt_logs);")
            p_cols = {row["name"] for row in await cur.fetchall()}
            if "audio_url" not in p_cols:
                await db.execute("ALTER TABLE ptt_logs ADD COLUMN audio_url TEXT DEFAULT NULL;")

            # 4. SMS/TMP-Nachrichten (NEU)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sms_messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    sender_id   INTEGER NOT NULL,
                    target_id   INTEGER NOT NULL DEFAULT 0,
                    text        TEXT    NOT NULL DEFAULT '',
                    is_group    INTEGER DEFAULT 0,
                    ack         INTEGER DEFAULT 0,
                    direction   TEXT    DEFAULT 'incoming'
                );
            """)

            # 5. Radio-Ereignisse (RRS Online/Offline) (NEU)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS radio_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    radio_id    INTEGER NOT NULL,
                    event_type  TEXT    NOT NULL,
                    slot        TEXT    DEFAULT 'TS1'
                );
            """)

            # 6. Taktische Marker (DV 102)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS markers (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT    NOT NULL,
                    lat             REAL    NOT NULL,
                    lon             REAL    NOT NULL,
                    typ             TEXT    NOT NULL,
                    beschreibung    TEXT    NOT NULL DEFAULT '',
                    prioritaet      TEXT    NOT NULL DEFAULT 'normal',
                    author          TEXT    DEFAULT 'Operator',
                    tactical_symbol TEXT    DEFAULT ''
                );
            """)
            cur = await db.execute("PRAGMA table_info(markers);")
            m_cols = {row["name"] for row in await cur.fetchall()}
            if "tactical_symbol" not in m_cols:
                await db.execute("ALTER TABLE markers ADD COLUMN tactical_symbol TEXT DEFAULT '';")

            # 7. Lagepläne / Stockwerk-Verwaltung
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tactical_plans (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT    NOT NULL,
                    floor_level TEXT    NOT NULL,
                    image_url   TEXT    NOT NULL,
                    bounds      TEXT    NOT NULL,
                    opacity     REAL    DEFAULT 0.85,
                    is_active   INTEGER DEFAULT 0,
                    created_at  TEXT    NOT NULL
                );
            """)

            # 8. Geofences
            await db.execute("""
                CREATE TABLE IF NOT EXISTS geofences (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    name         TEXT    NOT NULL,
                    zone_type    TEXT    NOT NULL DEFAULT 'danger',
                    shape        TEXT    NOT NULL DEFAULT 'circle',
                    center_lat   REAL    DEFAULT NULL,
                    center_lon   REAL    DEFAULT NULL,
                    radius_m     REAL    DEFAULT 100.0,
                    polygon_json TEXT    DEFAULT '[]',
                    created_at   TEXT    NOT NULL
                );
            """)

            # 9. SNMP-Alarm-Verlauf (NEU)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS snmp_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    oid_key     TEXT    NOT NULL,
                    oid_label   TEXT    DEFAULT '',
                    value       TEXT    NOT NULL,
                    alarm_level TEXT    DEFAULT 'info'
                );
            """)

            # 10. Systemeinstellungen
            await db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

            # 11. Einsatz-Archiv
            await db.execute("""
                CREATE TABLE IF NOT EXISTS missions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_code    TEXT    NOT NULL UNIQUE,
                    title           TEXT    NOT NULL DEFAULT 'Einsatz',
                    leader          TEXT    DEFAULT '',
                    location        TEXT    DEFAULT '',
                    channel         TEXT    DEFAULT '',
                    status          TEXT    DEFAULT 'active',
                    notes           TEXT    DEFAULT '',
                    started_at      TEXT    NOT NULL,
                    ended_at        TEXT    DEFAULT NULL,
                    archived_at     TEXT    DEFAULT NULL,
                    ptt_count       INTEGER DEFAULT 0,
                    sms_count       INTEGER DEFAULT 0,
                    radio_count     INTEGER DEFAULT 0,
                    archive_path    TEXT    DEFAULT NULL
                );
            """)

            # mission_id als optionaler FK in Log-Tabellen (Migration)
            for tbl, fk_note in [
                ("ptt_logs",    "ptt"),
                ("sms_messages","sms"),
                ("markers",     "marker"),
                ("snmp_events", "snmp"),
                ("radio_events","rev"),
            ]:
                cur2 = await db.execute(f"PRAGMA table_info({tbl});")
                cols = {r["name"] for r in await cur2.fetchall()}
                if "mission_id" not in cols:
                    await db.execute(f"ALTER TABLE {tbl} ADD COLUMN mission_id INTEGER DEFAULT NULL;")

            # Indizes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_gps_radio_ts  ON gps_history (radio_id, timestamp);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_gps_ts        ON gps_history (timestamp);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_ptt_radio     ON ptt_logs    (radio_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_ptt_ts        ON ptt_logs    (timestamp);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_sms_sender    ON sms_messages (sender_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_revents_radio ON radio_events (radio_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_markers_ts    ON markers     (timestamp);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_snmp_ts       ON snmp_events (timestamp);")
            await db.commit()

        logger.info(f"Datenbank initialisiert: {self.db_path}")
        await self._import_ids_json()

    async def _import_ids_json(self) -> None:
        """Importiert ids.json beim ersten Start, falls vorhanden."""
        if not os.path.isfile(IDS_JSON_PATH):
            return
        try:
            with open(IDS_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            imported = 0
            for rid_str, info in data.items():
                try:
                    rid = int(rid_str)
                    alias = info.get("name", f"Radio {rid}")
                    model = info.get("geraet", "")
                    await self.upsert_radio(rid, alias, model)
                    imported += 1
                except (ValueError, TypeError):
                    continue
            if imported:
                logger.info(f"ids.json: {imported} Funkgeräte importiert.")
        except Exception as exc:
            logger.warning(f"ids.json Import fehlgeschlagen: {exc}")

    # ── Funkgeräte ─────────────────────────────────────────────────────────

    async def upsert_radio(
        self,
        radio_id: int,
        alias: str,
        device_model: str = "",
        last_seen: Optional[str] = None,
        has_gps: Optional[bool] = None,
        fixed_lat: Optional[float] = None,
        fixed_lon: Optional[float] = None,
        floor_level: Optional[str] = None,
    ) -> None:
        """Erstellt oder aktualisiert ein Funkgerät (Alias/Modell werden nur überschrieben wenn nicht leer)."""
        if last_seen is None:
            last_seen = get_current_iso_timestamp()
        async with self.get_connection() as db:
            cur = await db.execute(
                "SELECT has_gps, fixed_lat, fixed_lon, floor_level FROM radios WHERE radio_id = ?;",
                (radio_id,),
            )
            existing = await cur.fetchone()
            gps_val = int(has_gps) if has_gps is not None else (existing["has_gps"] if existing else 1)
            flat    = fixed_lat   if fixed_lat   is not None else (existing["fixed_lat"]  if existing else None)
            flon    = fixed_lon   if fixed_lon   is not None else (existing["fixed_lon"]  if existing else None)
            floor   = floor_level if floor_level is not None else (existing["floor_level"] if existing else "EG")
            await db.execute("""
                INSERT INTO radios (radio_id, alias, device_model, last_seen, has_gps, fixed_lat, fixed_lon, floor_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(radio_id) DO UPDATE SET
                    alias        = CASE WHEN excluded.alias != ''        THEN excluded.alias        ELSE radios.alias END,
                    device_model = CASE WHEN excluded.device_model != '' THEN excluded.device_model ELSE radios.device_model END,
                    last_seen    = excluded.last_seen,
                    has_gps      = excluded.has_gps,
                    fixed_lat    = excluded.fixed_lat,
                    fixed_lon    = excluded.fixed_lon,
                    floor_level  = excluded.floor_level;
            """, (radio_id, alias, device_model, last_seen, gps_val, flat, flon, floor))
            await db.commit()

    async def set_radio_online(self, radio_id: int, online: bool, slot: str = "TS1") -> None:
        """Setzt den Online-Status eines Funkgeräts und schreibt ein Radio-Event."""
        ts = get_current_iso_timestamp()
        async with self.get_connection() as db:
            await db.execute(
                "UPDATE radios SET online = ?, last_seen = ? WHERE radio_id = ?;",
                (int(online), ts, radio_id),
            )
            await db.execute(
                "INSERT INTO radio_events (timestamp, radio_id, event_type, slot) VALUES (?, ?, ?, ?);",
                (ts, radio_id, "online" if online else "offline", slot),
            )
            await db.commit()

    async def get_radios(self) -> List[Dict[str, Any]]:
        """
        Gibt alle Funkgeräte zurück, angereichert mit dem letzten GPS-Fix.
        Bug B-04 Fix: last_lat, last_lon, last_rssi per LEFT JOIN aus gps_history.
        """
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT
                    r.radio_id, r.alias, r.device_model, r.last_seen,
                    r.has_gps, r.fixed_lat, r.fixed_lon, r.floor_level,
                    r.is_emergency, r.emergency_since, r.emergency_type,
                    r.online, r.last_rssi,
                    g.lat       AS last_lat,
                    g.lon       AS last_lon,
                    g.speed     AS last_speed,
                    g.heading   AS last_heading,
                    g.timestamp AS last_gps_ts
                FROM radios r
                LEFT JOIN gps_history g ON g.id = (
                    SELECT id FROM gps_history
                    WHERE radio_id = r.radio_id
                    ORDER BY id DESC
                    LIMIT 1
                )
                ORDER BY r.radio_id ASC;
            """)
            rows = await cursor.fetchall()
        return [
            {
                "radio_id":       r["radio_id"],
                "alias":          r["alias"] or f"Radio {r['radio_id']}",
                "device_model":   r["device_model"] or "",
                "last_seen":      r["last_seen"],
                "has_gps":        bool(r["has_gps"]),
                "fixed_lat":      r["fixed_lat"],
                "fixed_lon":      r["fixed_lon"],
                "floor_level":    r["floor_level"] or "EG",
                "is_emergency":   bool(r["is_emergency"]),
                "emergency_since": r["emergency_since"],
                "emergency_type": r["emergency_type"],
                "online":         bool(r["online"]),
                "last_rssi":      r["last_rssi"],
                "last_lat":       r["last_lat"],
                "last_lon":       r["last_lon"],
                "last_speed":     r["last_speed"],
                "last_heading":   r["last_heading"],
                "last_gps_ts":    r["last_gps_ts"],
            }
            for r in rows
        ]

    async def get_radio(self, radio_id: int) -> Optional[Dict[str, Any]]:
        """Gibt ein einzelnes Funkgerät zurück oder None."""
        radios = await self.get_radios()
        return next((r for r in radios if r["radio_id"] == radio_id), None)

    async def delete_radio(self, radio_id: int) -> bool:
        async with self.get_connection() as db:
            cur = await db.execute("DELETE FROM radios WHERE radio_id = ?;", (radio_id,))
            await db.commit()
            return cur.rowcount > 0

    async def set_radio_gps(self, radio_id: int, has_gps: bool) -> None:
        async with self.get_connection() as db:
            await db.execute("UPDATE radios SET has_gps = ? WHERE radio_id = ?;", (int(has_gps), radio_id))
            await db.commit()

    async def set_radio_fixed_position(self, radio_id: int, lat: Optional[float], lon: Optional[float]) -> None:
        async with self.get_connection() as db:
            await db.execute(
                "UPDATE radios SET fixed_lat = ?, fixed_lon = ? WHERE radio_id = ?;",
                (lat, lon, radio_id),
            )
            await db.commit()

    async def set_radio_floor(self, radio_id: int, floor_level: str) -> None:
        async with self.get_connection() as db:
            await db.execute("UPDATE radios SET floor_level = ? WHERE radio_id = ?;", (floor_level, radio_id))
            await db.commit()

    async def set_radio_rssi(self, radio_id: int, rssi: float) -> None:
        async with self.get_connection() as db:
            await db.execute("UPDATE radios SET last_rssi = ? WHERE radio_id = ?;", (rssi, radio_id))
            await db.commit()

    async def trigger_radio_emergency(self, radio_id: int, emergency_type: str = "MAN-DOWN / NOTRUF") -> Dict[str, Any]:
        ts = get_current_iso_timestamp()
        async with self.get_connection() as db:
            await db.execute("""
                UPDATE radios SET is_emergency = 1, emergency_since = ?, emergency_type = ?
                WHERE radio_id = ?;
            """, (ts, emergency_type, radio_id))
            await db.commit()
            cur = await db.execute("SELECT alias, floor_level FROM radios WHERE radio_id = ?;", (radio_id,))
            row = await cur.fetchone()
        alias = row["alias"] if row else f"Radio {radio_id}"
        floor = row["floor_level"] if row else "EG"
        return {
            "radio_id":       radio_id,
            "alias":          alias,
            "floor_level":    floor,
            "is_emergency":   True,
            "emergency_since": ts,
            "emergency_type": emergency_type,
        }

    async def ack_radio_emergency(self, radio_id: int) -> Dict[str, Any]:
        async with self.get_connection() as db:
            await db.execute("""
                UPDATE radios SET is_emergency = 0, emergency_since = NULL, emergency_type = NULL
                WHERE radio_id = ?;
            """, (radio_id,))
            await db.commit()
        return {"radio_id": radio_id, "is_emergency": False}

    async def export_ids_json(self, target_path: Optional[str] = None) -> str:
        radios = await self.get_radios()
        out = {str(r["radio_id"]): {"name": r["alias"], "geraet": r["device_model"]} for r in radios}
        dest = target_path or IDS_JSON_PATH
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        logger.info(f"ids.json exportiert: {dest} ({len(out)} Einträge)")
        return dest

    # ── GPS ────────────────────────────────────────────────────────────────

    async def insert_gps(
        self,
        radio_id: int,
        lat: float,
        lon: float,
        speed: float = 0.0,
        heading: float = 0.0,
        accuracy: float = 0.0,
        rssi: Optional[float] = None,
        timestamp: Optional[str] = None,
    ) -> int:
        if timestamp is None:
            timestamp = get_current_iso_timestamp()
        async with self.get_connection() as db:
            cur = await db.execute("""
                INSERT INTO gps_history (timestamp, radio_id, lat, lon, speed, heading, accuracy, rssi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (timestamp, radio_id, lat, lon, speed, heading, accuracy, rssi))
            await db.execute("UPDATE radios SET last_seen = ? WHERE radio_id = ?;", (timestamp, radio_id))
            if rssi is not None:
                await db.execute("UPDATE radios SET last_rssi = ? WHERE radio_id = ?;", (rssi, radio_id))
            await db.commit()
            return cur.lastrowid

    async def get_recent_gps(self) -> List[Dict[str, Any]]:
        """Gibt den letzten GPS-Fix aller Funkgeräte zurück (inkl. Gerätedaten)."""
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT g.radio_id, r.alias, r.device_model, r.has_gps,
                       r.fixed_lat, r.fixed_lon, r.floor_level,
                       r.is_emergency, r.emergency_since, r.emergency_type, r.online,
                       g.lat, g.lon, g.speed, g.heading, g.accuracy, g.rssi, g.timestamp
                FROM gps_history g
                INNER JOIN (
                    SELECT radio_id, MAX(id) AS max_id FROM gps_history GROUP BY radio_id
                ) latest ON g.id = latest.max_id
                JOIN radios r ON g.radio_id = r.radio_id;
            """)
            rows = await cursor.fetchall()
        return [
            {
                "radio_id":       r["radio_id"],
                "alias":          r["alias"],
                "device_model":   r["device_model"],
                "has_gps":        bool(r["has_gps"]),
                "fixed_lat":      r["fixed_lat"],
                "fixed_lon":      r["fixed_lon"],
                "floor_level":    r["floor_level"] or "EG",
                "is_emergency":   bool(r["is_emergency"]),
                "emergency_since": r["emergency_since"],
                "emergency_type": r["emergency_type"],
                "online":         bool(r["online"]),
                "lat":            r["lat"],
                "lon":            r["lon"],
                "speed":          r["speed"],
                "heading":        r["heading"],
                "accuracy":       r["accuracy"],
                "rssi":           r["rssi"],
                "timestamp":      r["timestamp"],
            }
            for r in rows
        ]

    async def get_gps_history(self, radio_id: int, limit: int = 200) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT lat, lon, speed, heading, rssi, timestamp
                FROM gps_history WHERE radio_id = ?
                ORDER BY id DESC LIMIT ?;
            """, (radio_id, limit))
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_rssi_coverage_points(self, limit: int = 500) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT lat, lon, rssi, radio_id, timestamp
                FROM gps_history WHERE rssi IS NOT NULL
                ORDER BY id DESC LIMIT ?;
            """, (limit,))
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── PTT ────────────────────────────────────────────────────────────────

    async def insert_ptt(
        self,
        radio_id: int,
        duration_ms: int,
        typ: str = "Gruppe",
        slot: str = "TS1",
        rssi: Optional[float] = None,
        audio_url: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> int:
        if timestamp is None:
            timestamp = get_current_iso_timestamp()
        async with self.get_connection() as db:
            cur = await db.execute("""
                INSERT INTO ptt_logs (timestamp, radio_id, duration_ms, typ, slot, rssi, audio_url)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (timestamp, radio_id, duration_ms, typ, slot, rssi, audio_url))
            await db.execute("UPDATE radios SET last_seen = ? WHERE radio_id = ?;", (timestamp, radio_id))
            await db.commit()
            return cur.lastrowid

    async def update_ptt_audio(self, ptt_id: int, audio_url: str) -> None:
        async with self.get_connection() as db:
            await db.execute("UPDATE ptt_logs SET audio_url = ? WHERE id = ?;", (audio_url, ptt_id))
            await db.commit()

    async def get_recent_calls(self, limit: int = 20) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT p.id, p.timestamp, p.radio_id, r.alias, p.duration_ms,
                       p.typ, p.slot, p.rssi, p.audio_url
                FROM ptt_logs p
                LEFT JOIN radios r ON p.radio_id = r.radio_id
                ORDER BY p.id DESC LIMIT ?;
            """, (limit,))
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── SMS ────────────────────────────────────────────────────────────────

    async def insert_sms(
        self,
        sender_id: int,
        target_id: int,
        text: str,
        is_group: bool = False,
        ack: bool = False,
        direction: str = "incoming",
        timestamp: Optional[str] = None,
    ) -> int:
        if timestamp is None:
            timestamp = get_current_iso_timestamp()
        async with self.get_connection() as db:
            cur = await db.execute("""
                INSERT INTO sms_messages (timestamp, sender_id, target_id, text, is_group, ack, direction)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (timestamp, sender_id, target_id, text, int(is_group), int(ack), direction))
            await db.commit()
            return cur.lastrowid

    async def get_recent_sms(self, limit: int = 50) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT s.id, s.timestamp, s.sender_id, s.target_id, s.text,
                       s.is_group, s.ack, s.direction,
                       r.alias AS sender_alias
                FROM sms_messages s
                LEFT JOIN radios r ON s.sender_id = r.radio_id
                ORDER BY s.id DESC LIMIT ?;
            """, (limit,))
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── Radio-Ereignisse (RRS) ──────────────────────────────────────────────

    async def get_recent_radio_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT e.id, e.timestamp, e.radio_id, e.event_type, e.slot, r.alias
                FROM radio_events e
                LEFT JOIN radios r ON e.radio_id = r.radio_id
                ORDER BY e.id DESC LIMIT ?;
            """, (limit,))
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── SNMP-Alarm-Verlauf ──────────────────────────────────────────────────

    async def insert_snmp_event(
        self,
        oid_key: str,
        oid_label: str,
        value: str,
        alarm_level: str = "info",
        timestamp: Optional[str] = None,
    ) -> int:
        if timestamp is None:
            timestamp = get_current_iso_timestamp()
        async with self.get_connection() as db:
            cur = await db.execute("""
                INSERT INTO snmp_events (timestamp, oid_key, oid_label, value, alarm_level)
                VALUES (?, ?, ?, ?, ?);
            """, (timestamp, oid_key, oid_label, str(value), alarm_level))
            await db.commit()
            return cur.lastrowid

    async def get_snmp_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("""
                SELECT * FROM snmp_events ORDER BY id DESC LIMIT ?;
            """, (limit,))
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── Marker ─────────────────────────────────────────────────────────────

    async def insert_marker(
        self,
        lat: float,
        lon: float,
        typ: str,
        beschreibung: str,
        prioritaet: str = "normal",
        author: str = "Operator",
        tactical_symbol: str = "",
        timestamp: Optional[str] = None,
    ) -> int:
        if timestamp is None:
            timestamp = get_current_iso_timestamp()
        async with self.get_connection() as db:
            cur = await db.execute("""
                INSERT INTO markers (timestamp, lat, lon, typ, beschreibung, prioritaet, author, tactical_symbol)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (timestamp, lat, lon, typ, beschreibung, prioritaet, author, tactical_symbol))
            await db.commit()
            return cur.lastrowid

    async def get_markers(self) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("SELECT * FROM markers ORDER BY id DESC;")
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def delete_marker(self, marker_id: int) -> bool:
        async with self.get_connection() as db:
            cur = await db.execute("DELETE FROM markers WHERE id = ?;", (marker_id,))
            await db.commit()
            return cur.rowcount > 0

    # ── Lagepläne ──────────────────────────────────────────────────────────

    async def insert_plan(
        self,
        name: str,
        floor_level: str,
        image_url: str,
        bounds: List[List[float]],
        opacity: float = 0.85,
        is_active: bool = False,
    ) -> int:
        now = get_current_iso_timestamp()
        async with self.get_connection() as db:
            if is_active:
                await db.execute("UPDATE tactical_plans SET is_active = 0 WHERE floor_level = ?;", (floor_level,))
            cur = await db.execute("""
                INSERT INTO tactical_plans (name, floor_level, image_url, bounds, opacity, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (name, floor_level, image_url, json.dumps(bounds), opacity, int(is_active), now))
            await db.commit()
            return cur.lastrowid

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
            cur = await db.execute("DELETE FROM tactical_plans WHERE id = ?;", (plan_id,))
            await db.commit()
            return cur.rowcount > 0

    # ── Geofences ──────────────────────────────────────────────────────────

    async def insert_geofence(
        self,
        name: str,
        zone_type: str,
        shape: str,
        center_lat: Optional[float] = None,
        center_lon: Optional[float] = None,
        radius_m: Optional[float] = None,
        polygon_coords: Optional[List[List[float]]] = None,
    ) -> int:
        now = get_current_iso_timestamp()
        async with self.get_connection() as db:
            cur = await db.execute("""
                INSERT INTO geofences (name, zone_type, shape, center_lat, center_lon, radius_m, polygon_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (name, zone_type, shape, center_lat, center_lon, radius_m, json.dumps(polygon_coords or []), now))
            await db.commit()
            return cur.lastrowid

    async def get_geofences(self) -> List[Dict[str, Any]]:
        async with self.get_connection() as db:
            cursor = await db.execute("SELECT * FROM geofences ORDER BY id DESC;")
            rows = await cursor.fetchall()
        result = []
        for r in rows:
            item = dict(r)
            try:
                item["polygon_coords"] = json.loads(item.get("polygon_json") or "[]")
            except Exception:
                item["polygon_coords"] = []
            result.append(item)
        return result

    async def delete_geofence(self, geofence_id: int) -> bool:
        async with self.get_connection() as db:
            cur = await db.execute("DELETE FROM geofences WHERE id = ?;", (geofence_id,))
            await db.commit()
            return cur.rowcount > 0

    # ── Timeline & Replay ──────────────────────────────────────────────────

    async def get_timeline_events(
        self,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        where, params = [], []
        if from_ts:
            where.append("timestamp >= ?"); params.append(from_ts)
        if to_ts:
            where.append("timestamp <= ?"); params.append(to_ts)
        ws = f"WHERE {' AND '.join(where)}" if where else ""

        async with self.get_connection() as db:
            ptt_cur = await db.execute(f"""
                SELECT p.id, p.timestamp, p.duration_ms, p.typ, p.slot, p.radio_id, r.alias, p.audio_url
                FROM ptt_logs p
                LEFT JOIN radios r ON p.radio_id = r.radio_id
                {ws}
                ORDER BY p.timestamp DESC LIMIT ?;
            """, (*params, limit))
            ptt_rows = await ptt_cur.fetchall()

            mrk_cur = await db.execute(f"""
                SELECT id, timestamp, typ, beschreibung, prioritaet, author, tactical_symbol
                FROM markers {ws} ORDER BY timestamp DESC LIMIT ?;
            """, (*params, limit))
            mrk_rows = await mrk_cur.fetchall()

            sms_cur = await db.execute(f"""
                SELECT s.id, s.timestamp, s.sender_id, s.text, r.alias AS sender_alias
                FROM sms_messages s
                LEFT JOIN radios r ON s.sender_id = r.radio_id
                {ws} ORDER BY s.timestamp DESC LIMIT ?;
            """, (*params, limit))
            sms_rows = await sms_cur.fetchall()

        items: List[Dict[str, Any]] = []
        for r in ptt_rows:
            alias = r["alias"] or f"Radio {r['radio_id']}"
            items.append({
                "id":          f"ptt_{r['id']}",
                "ptt_id":      r["id"],
                "content":     f"🎙️ {alias} ({r['slot']}) – {r['duration_ms']}ms",
                "start":       r["timestamp"],
                "timestamp":   r["timestamp"],
                "className":   "tl-ptt",
                "group":       "radio",
                "audio_url":   r["audio_url"],
                "type":        "ptt",
                "radio_id":    r["radio_id"],
                "duration_ms": r["duration_ms"],
                "slot":        r["slot"],
            })
        for m in mrk_rows:
            sym = f"[{m['tactical_symbol'].upper()}] " if dict(m).get("tactical_symbol") else ""
            items.append({
                "id":            f"marker_{m['id']}",
                "content":       f"📍 {sym}{m['typ']}: {m['beschreibung']}",
                "start":         m["timestamp"],
                "timestamp":     m["timestamp"],
                "className":     f"tl-marker tl-pri-{m['prioritaet']}",
                "group":         "incident",
                "type":          "marker",
                "beschreibung": m["beschreibung"],
            })
        for s in sms_rows:
            alias = s["sender_alias"] or f"Radio {s['sender_id']}"
            items.append({
                "id":        f"sms_{s['id']}",
                "content":   f"💬 {alias}: {s['text'][:40]}",
                "start":     s["timestamp"],
                "timestamp": s["timestamp"],
                "className": "tl-sms",
                "group":     "sms",
                "type":      "sms",
                "text":      s["text"],
                "radio_id":  s["sender_id"],
            })
        items.sort(key=lambda x: x["start"], reverse=True)
        return items[:limit]

    async def get_state_at_timestamp(self, timestamp: str) -> Dict[str, Any]:
        """Gibt den Systemzustand zu einem bestimmten Zeitpunkt zurück (Replay)."""
        async with self.get_connection() as db:
            gps_cur = await db.execute("""
                SELECT g.radio_id, r.alias, g.lat, g.lon, g.speed, g.heading, g.rssi, g.timestamp
                FROM gps_history g
                INNER JOIN (
                    SELECT radio_id, MAX(timestamp) AS max_ts
                    FROM gps_history WHERE timestamp <= ? GROUP BY radio_id
                ) latest ON g.radio_id = latest.radio_id AND g.timestamp = latest.max_ts
                JOIN radios r ON g.radio_id = r.radio_id;
            """, (timestamp,))
            gps_rows = await gps_cur.fetchall()

            ptt_cur = await db.execute("""
                SELECT p.*, r.alias FROM ptt_logs p
                JOIN radios r ON p.radio_id = r.radio_id
                WHERE p.timestamp <= ? ORDER BY p.timestamp DESC LIMIT 1;
            """, (timestamp,))
            last_ptt = await ptt_cur.fetchone()

            mrk_cur = await db.execute(
                "SELECT * FROM markers WHERE timestamp <= ? ORDER BY id ASC;", (timestamp,)
            )
            mrk_rows = await mrk_cur.fetchall()

        return {
            "timestamp":  timestamp,
            "radios":     [dict(r) for r in gps_rows],
            "active_ptt": dict(last_ptt) if last_ptt else None,
            "markers":    [dict(m) for m in mrk_rows],
        }

    # ── Einstellungen ──────────────────────────────────────────────────────

    async def get_setting(self, key: str, default: Any = None) -> Any:
        async with self.get_connection() as db:
            cur = await db.execute("SELECT value FROM settings WHERE key = ?;", (key,))
            row = await cur.fetchone()
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
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """, (key, val_str))
            await db.commit()

    async def get_all_settings(self) -> Dict[str, Any]:
        async with self.get_connection() as db:
            cursor = await db.execute("SELECT key, value FROM settings;")
            rows = await cursor.fetchall()
        result: Dict[str, Any] = {}
        for r in rows:
            try:
                result[r["key"]] = json.loads(r["value"])
            except Exception:
                result[r["key"]] = r["value"]
        return result

    async def save_settings_batch(self, settings: Dict[str, Any]) -> None:
        async with self.get_connection() as db:
            for key, value in settings.items():
                val_str = json.dumps(value) if not isinstance(value, str) else value
                await db.execute("""
                    INSERT INTO settings (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """, (key, val_str))
            await db.commit()

    # ── Einsatz-Stammdaten ────────────────────────────────────────────────

    async def get_mission_data(self) -> Dict[str, Any]:
        """Gibt die aktuellen Einsatzdaten zurück (Settings-Blob + aktive Mission aus missions-Tabelle)."""
        mission = await self.get_setting("mission_data")
        now = get_current_iso_timestamp()
        defaults = {
            "mission_title":    "Neuer Einsatz",
            "mission_code":     f"EINSATZ-{now[:10].replace('-', '')}-01",
            "mission_leader":   "",
            "mission_location": "",
            "mission_channel":  "Kanal 1 / TS1 & TS2",
            "mission_status":   "preparation",
            "mission_start_time": now,
            "mission_notes":    "",
        }
        if isinstance(mission, dict):
            defaults.update(mission)
        return defaults

    async def save_mission_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        current = await self.get_mission_data()
        current.update(data)
        await self.save_setting("mission_data", current)
        return current

    # ── Einsatz-Archivierung ──────────────────────────────────────────────

    async def create_mission(self, title: str, code: str, leader: str = "",
                              location: str = "", channel: str = "", notes: str = "") -> int:
        """Legt einen neuen Einsatz in der missions-Tabelle an."""
        now = get_current_iso_timestamp()
        async with self.get_connection() as db:
            cur = await db.execute("""
                INSERT INTO missions (mission_code, title, leader, location, channel, notes, status, started_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                ON CONFLICT(mission_code) DO UPDATE SET
                    title=excluded.title, leader=excluded.leader,
                    location=excluded.location, channel=excluded.channel,
                    notes=excluded.notes, status='active'
            """, (code, title, leader, location, channel, notes, now))
            await db.commit()
            # Holt die ID (INSERT oder UPDATE)
            cur2 = await db.execute("SELECT id FROM missions WHERE mission_code = ?", (code,))
            row = await cur2.fetchone()
            mission_id = row["id"] if row else cur.lastrowid
        # Settings-Blob synchronisieren
        await self.save_setting("active_mission_id", mission_id)
        return mission_id

    async def get_active_mission_id(self) -> Optional[int]:
        """Gibt die ID der aktuell aktiven Mission zurück."""
        v = await self.get_setting("active_mission_id")
        return int(v) if v is not None else None

    async def list_missions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Alle Einsätze, neueste zuerst."""
        async with self.get_connection() as db:
            cur = await db.execute("""
                SELECT id, mission_code, title, leader, location, channel, status,
                       started_at, ended_at, archived_at, ptt_count, sms_count,
                       radio_count, archive_path
                FROM missions
                ORDER BY started_at DESC
                LIMIT ?
            """, (limit,))
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_mission(self, mission_id: int) -> Optional[Dict[str, Any]]:
        """Gibt einen einzelnen Einsatz zurück."""
        async with self.get_connection() as db:
            cur = await db.execute("SELECT * FROM missions WHERE id = ?", (mission_id,))
            row = await cur.fetchone()
        return dict(row) if row else None

    async def close_mission(self, mission_id: int) -> Dict[str, Any]:
        """
        Schließt einen Einsatz ab:
        1. Setzt status='closed', ended_at=jetzt
        2. Aktualisiert ptt_count, sms_count, radio_count
        3. Gibt Mission-Summary zurück
        """
        now = get_current_iso_timestamp()
        async with self.get_connection() as db:
            # Zähler berechnen
            cur = await db.execute(
                "SELECT COUNT(*) as n FROM ptt_logs WHERE mission_id = ?", (mission_id,))
            ptt = (await cur.fetchone())["n"]
            cur = await db.execute(
                "SELECT COUNT(*) as n FROM sms_messages WHERE mission_id = ?", (mission_id,))
            sms = (await cur.fetchone())["n"]
            cur = await db.execute(
                "SELECT COUNT(DISTINCT radio_id) as n FROM ptt_logs WHERE mission_id = ?", (mission_id,))
            radios = (await cur.fetchone())["n"]

            await db.execute("""
                UPDATE missions
                SET status='closed', ended_at=?, ptt_count=?, sms_count=?, radio_count=?
                WHERE id=?
            """, (now, ptt, sms, radios, mission_id))
            await db.commit()
            cur = await db.execute("SELECT * FROM missions WHERE id=?", (mission_id,))
            row = await cur.fetchone()
        return dict(row) if row else {}

    async def archive_mission(self, mission_id: int, archive_dir: str) -> str:
        """
        Erstellt einen vollständigen JSON-Archiv-Export eines Einsatzes.
        Gibt den Pfad zur Archivdatei zurück.
        Alle referenzierten Daten (Calls, SMS, GPS-Verlauf, Marker, SNMP-Events,
        Funkgeräte, Audiodateien) werden gesammelt.
        """
        import zipfile
        import shutil

        now = get_current_iso_timestamp()
        mission = await self.get_mission(mission_id)
        if not mission:
            raise ValueError(f"Mission {mission_id} nicht gefunden")

        code = mission["mission_code"]
        safe_code = "".join(c for c in code if c.isalnum() or c in "-_").rstrip()
        archive_name = f"EINSATZ_{safe_code}_{now[:10].replace('-', '')}.zip"
        archive_path = os.path.join(archive_dir, archive_name)
        os.makedirs(archive_dir, exist_ok=True)

        async with self.get_connection() as db:
            # PTT-Logs
            cur = await db.execute("""
                SELECT p.*, r.alias
                FROM ptt_logs p
                LEFT JOIN radios r ON p.radio_id = r.radio_id
                WHERE p.mission_id = ? OR ? IS NULL
                ORDER BY p.timestamp
            """, (mission_id, None if mission.get("status") == "active" else mission_id))
            ptt_rows = [dict(r) for r in await cur.fetchall()]

            # SMS
            cur = await db.execute("""
                SELECT s.*, r.alias as sender_alias
                FROM sms_messages s
                LEFT JOIN radios r ON s.sender_id = r.radio_id
                WHERE s.mission_id = ?
                ORDER BY s.timestamp
            """, (mission_id,))
            sms_rows = [dict(r) for r in await cur.fetchall()]

            # GPS-Verlauf (der Mission-Zeitraum)
            started = mission.get("started_at", "1970-01-01")
            ended = mission.get("ended_at") or now
            cur = await db.execute("""
                SELECT g.*, r.alias
                FROM gps_history g
                LEFT JOIN radios r ON g.radio_id = r.radio_id
                WHERE g.timestamp BETWEEN ? AND ?
                ORDER BY g.timestamp
            """, (started, ended))
            gps_rows = [dict(r) for r in await cur.fetchall()]

            # Marker
            cur = await db.execute("""
                SELECT * FROM markers
                WHERE (mission_id = ? OR mission_id IS NULL)
                  AND timestamp BETWEEN ? AND ?
                ORDER BY timestamp
            """, (mission_id, started, ended))
            marker_rows = [dict(r) for r in await cur.fetchall()]

            # SNMP-Events
            cur = await db.execute("""
                SELECT * FROM snmp_events
                WHERE timestamp BETWEEN ? AND ?
                ORDER BY timestamp
            """, (started, ended))
            snmp_rows = [dict(r) for r in await cur.fetchall()]

            # Funkgeräte
            cur = await db.execute("SELECT * FROM radios ORDER BY radio_id")
            radio_rows = [dict(r) for r in await cur.fetchall()]

        # ZIP erstellen
        archive_data = {
            "export_version":  "1.0",
            "exported_at":     now,
            "mission":         mission,
            "radios":          radio_rows,
            "ptt_logs":        ptt_rows,
            "sms_messages":    sms_rows,
            "gps_history":     gps_rows,
            "markers":         marker_rows,
            "snmp_events":     snmp_rows,
        }

        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Haupt-JSON
            zf.writestr("mission_data.json", json.dumps(archive_data, indent=2, ensure_ascii=False))

            # Audio-Dateien einpacken (falls vorhanden)
            audio_copied = 0
            for p in ptt_rows:
                url = p.get("audio_url")
                if url and os.path.isfile(url):
                    zf.write(url, f"audio/{os.path.basename(url)}")
                    audio_copied += 1

            # README
            readme = (
                f"Hytera Command Center – Einsatzarchiv\n"
                f"======================================\n"
                f"Einsatz:    {mission.get('title')}\n"
                f"Code:       {mission.get('mission_code')}\n"
                f"Einsatzleiter: {mission.get('leader')}\n"
                f"Beginn:     {mission.get('started_at')}\n"
                f"Ende:       {mission.get('ended_at')}\n"
                f"--------------------------------------\n"
                f"PTT-Logs:   {len(ptt_rows)}\n"
                f"SMS:        {len(sms_rows)}\n"
                f"GPS-Punkte: {len(gps_rows)}\n"
                f"Marker:     {len(marker_rows)}\n"
                f"Audiodateien: {audio_copied}\n"
                f"--------------------------------------\n"
                f"Exportiert: {now}\n"
            )
            zf.writestr("README.txt", readme)

        # archive_path in DB speichern
        async with self.get_connection() as db:
            await db.execute(
                "UPDATE missions SET archived_at=?, archive_path=?, status='archived' WHERE id=?",
                (now, archive_path, mission_id)
            )
            await db.commit()

        logger.info(f"Einsatz {code} archiviert: {archive_path}")
        return archive_path

    async def delete_mission_archive(self, mission_id: int) -> bool:
        """Löscht einen archivierten Einsatz (nur Datensatz, nicht die ZIP-Datei)."""
        async with self.get_connection() as db:
            cur = await db.execute("DELETE FROM missions WHERE id=? AND status='archived'", (mission_id,))
            await db.commit()
            return cur.rowcount > 0

    async def assign_mission_id_to_logs(self, mission_id: int, from_ts: str) -> None:
        """Weist allen Logs seit from_ts die mission_id zu (für laufenden Einsatz)."""
        async with self.get_connection() as db:
            for tbl in ("ptt_logs", "sms_messages", "markers", "snmp_events", "radio_events"):
                await db.execute(
                    f"UPDATE {tbl} SET mission_id=? WHERE mission_id IS NULL AND timestamp >= ?",
                    (mission_id, from_ts)
                )
            await db.commit()

    async def purge_operational_data(self, keep_ids_json: bool = True) -> Dict[str, Any]:
        """
        Bereinigt alle operativen Demo-/Testdaten (PTT, SMS, GPS, Marker, Events)
        und setzt Funkgeräte auf den sauberen Ausgangszustand aus ids.json zurück.
        """
        counts: Dict[str, Any] = {}
        async with self.get_connection() as db:
            for tbl in (
                "ptt_logs", "sms_messages", "gps_history", "radio_events",
                "markers", "tactical_plans", "geofences", "snmp_events"
            ):
                try:
                    cur = await db.execute(f"DELETE FROM {tbl};")
                    counts[tbl] = cur.rowcount
                except Exception as exc:
                    counts[tbl] = f"Error: {exc}"

            valid_ids = []
            if keep_ids_json and os.path.exists(IDS_JSON_PATH):
                try:
                    with open(IDS_JSON_PATH, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        valid_ids = [int(k) for k in data.keys()]
                except Exception as exc:
                    logger.warning(f"Fehler beim Lesen von {IDS_JSON_PATH}: {exc}")

            if valid_ids:
                placeholders = ",".join("?" * len(valid_ids))
                cur = await db.execute(
                    f"DELETE FROM radios WHERE radio_id NOT IN ({placeholders});",
                    valid_ids
                )
                counts["deleted_radios"] = cur.rowcount
                await db.execute("""
                    UPDATE radios SET
                        online = 0,
                        last_seen = NULL,
                        last_rssi = NULL,
                        is_emergency = 0,
                        emergency_since = NULL,
                        emergency_type = NULL
                """)
            else:
                cur = await db.execute("DELETE FROM radios;")
                counts["deleted_radios"] = cur.rowcount

            await db.commit()
            await db.execute("VACUUM;")

        logger.info(f"Operative Daten bereinigt: {counts}")
        return counts


# Singleton-Instanz
db_manager = DatabaseManager()

