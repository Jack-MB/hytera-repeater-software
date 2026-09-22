"""
Hytera Tactical Suite - UDP Listener & Mock Generator
Empfängt echte Hytera HR1065 Binärpakete (GPS, PTT, Notruf)
und simuliert realistische Einsatzkräfte mit Notruffunktion, Stockwerken und Audio-Voice-Log.
"""

import os
import time
import math
import struct
import random
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional, Dict, Any, List

try:
    from .config import DEFAULT_UDP_PORT, DEFAULT_OVERLAY_BOUNDS
    from .db_manager import DatabaseManager, db_manager, get_current_iso_timestamp
    from .audio_manager import audio_manager
except (ImportError, ValueError):
    from config import DEFAULT_UDP_PORT, DEFAULT_OVERLAY_BOUNDS
    from db_manager import DatabaseManager, db_manager, get_current_iso_timestamp
    from audio_manager import audio_manager

logger = logging.getLogger("udp_listener")


def calculate_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine-Formel zur Distanzberechnung zweier GPS-Punkte in Metern."""
    R = 6371000.0  # Erdradius in Metern
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


class HyteraBinaryParser:
    """Parser für Hytera HR1065 Protokoll (GPS, Call-Status, Notruf)."""

    CALL_TYPES = {
        0x01: "Privat",
        0x02: "Gruppe",
        0x03: "AllCall",
        0x04: "Notruf",
    }

    @staticmethod
    def parse_gps_packet(payload: bytes) -> Optional[Dict[str, Any]]:
        if len(payload) < 20:
            return None
        try:
            opcode = struct.unpack_from('<H', payload, 1)[0]
            n_bytes = struct.unpack_from('<H', payload, 3)[0]
            data = payload[5:5 + n_bytes]
            if len(data) < 12:
                return None

            radio_ip = struct.unpack_from('>I', data, 0)[0]
            radio_id = radio_ip & 0xFFFFFF

            lat_raw = struct.unpack_from('>i', data, 4)[0]
            lon_raw = struct.unpack_from('>i', data, 8)[0]
            lat = lat_raw / 1e7
            lon = lon_raw / 1e7

            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and radio_id > 0:
                speed = 0.0
                heading = 0.0
                if len(data) >= 14:
                    speed = float(struct.unpack_from('>H', data, 12)[0]) / 10.0
                if len(data) >= 16:
                    heading = float(struct.unpack_from('>H', data, 14)[0])

                return {
                    "type": "gps",
                    "radio_id": radio_id,
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "speed": speed,
                    "heading": heading,
                    "accuracy": 3.0,
                    "timestamp": get_current_iso_timestamp()
                }
        except Exception as e:
            logger.debug(f"GPS Parsing Fehler: {e}")
        return None

    @staticmethod
    def parse_call_packet(payload: bytes) -> Optional[Dict[str, Any]]:
        if len(payload) < 20:
            return None
        try:
            opcode = struct.unpack_from('<H', payload, 1)[0]

            # 0x0004: HR1065 NAI Call-Status
            if opcode == 0x0004 and len(payload) >= 31:
                ts_byte = payload[8]
                ct_byte = payload[9]
                st_byte = payload[16]
                sender_id = struct.unpack_from('<I', payload, 26)[0]

                slot_name = "TS2" if ts_byte == 0x02 else "TS1"
                call_type = HyteraBinaryParser.CALL_TYPES.get(ct_byte, f"Typ {ct_byte}")

                is_active = (1 <= st_byte <= 4 and sender_id > 0)
                is_end = (st_byte == 5 or st_byte == 0)

                qual_byte = payload[30] if len(payload) > 30 else 5
                rssi_dbm = -124 + qual_byte * 6

                # Notruf-Kennung im Paket prüfen
                is_emergency = (ct_byte == 0x04 or st_byte == 0x04)

                ev_type = "ptt_start" if is_active else "ptt_end"
                return {
                    "type": ev_type,
                    "radio_id": sender_id,
                    "slot": slot_name,
                    "call_type": call_type,
                    "is_emergency": is_emergency,
                    "rssi": rssi_dbm,
                    "timestamp": get_current_iso_timestamp()
                }
        except Exception as e:
            logger.debug(f"PTT Parsing Fehler: {e}")
        return None


class HyteraUDPProtocol(asyncio.DatagramProtocol):
    """Asyncio Datagram-Protokoll für eingehende Hytera UDP-Pakete."""

    def __init__(self, on_event_cb: Callable[[Dict[str, Any]], Any]):
        self.on_event_cb = on_event_cb
        self.transport = None
        self._tx_start_times: Dict[int, float] = {}

    def connection_made(self, transport):
        self.transport = transport
        logger.info("UDP-Listener Socket geöffnet.")

    def datagram_received(self, data: bytes, addr):
        gps_event = HyteraBinaryParser.parse_gps_packet(data)
        if gps_event:
            asyncio.create_task(self.on_event_cb(gps_event))
            return

        call_event = HyteraBinaryParser.parse_call_packet(data)
        if call_event:
            rid = call_event["radio_id"]
            if call_event["type"] == "ptt_start":
                self._tx_start_times[rid] = time.time()
                asyncio.create_task(self.on_event_cb(call_event))
            elif call_event["type"] == "ptt_end":
                start_time = self._tx_start_times.pop(rid, None)
                duration_ms = int((time.time() - start_time) * 1000) if start_time else 2500
                call_event["duration_ms"] = max(500, duration_ms)
                asyncio.create_task(self.on_event_cb(call_event))

    def error_received(self, exc):
        logger.error(f"UDP-Fehler: {exc}")


class MockTelemetryGenerator:
    """
    Simuliert realistischen Funk- und GPS-Betrieb für 5 DMR-Endgeräte
    mit Notruffunktion, Signalpegel (RSSI), Audio-Voice-Log und Geofence-Prüfung.
    """

    def __init__(self, on_event_cb: Callable[[Dict[str, Any]], Any], db: DatabaseManager):
        self.on_event_cb = on_event_cb
        self.db = db
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self.paused = False

        bounds = DEFAULT_OVERLAY_BOUNDS
        self.lat_min = min(bounds[0][0], bounds[1][0])
        self.lat_max = max(bounds[0][0], bounds[1][0])
        self.lon_min = min(bounds[0][1], bounds[1][1])
        self.lon_max = max(bounds[0][1], bounds[1][1])

        self.center_lat = (self.lat_min + self.lat_max) / 2.0
        self.center_lon = (self.lon_min + self.lon_max) / 2.0

        # 5 realistische Einsatzkräfte
        self.units: Dict[int, Dict[str, Any]] = {}

    def toggle_pause(self) -> bool:
        self.paused = not self.paused
        return not self.paused

    def is_active(self) -> bool:
        return self.running and not self.paused

    async def start(self):
        if self.running:
            return
        self.running = True
        self.paused = False

        # Wenn Funkgeräte in der Datenbank existieren, diese für Bewegung nutzen
        db_radios = await self.db.get_radios()
        if db_radios:
            for r in db_radios:
                rid = r["radio_id"]
                if rid not in self.units:
                    self.units[rid] = {
                        "alias": r["alias"],
                        "lat": r.get("fixed_lat") or self.center_lat,
                        "lon": r.get("fixed_lon") or self.center_lon,
                        "heading": random.uniform(0, 360),
                        "speed": 4.0 if r.get("has_gps") else 0.0,
                        "model": r.get("device_model", ""),
                        "floor": r.get("floor_level", "EG"),
                        "has_gps": bool(r.get("has_gps")),
                        "is_emergency": False
                    }

        self._task = asyncio.create_task(self._run_loop())
        logger.info("Mock-Telemetrie-Generator gestartet.")

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self):
        cycle = 0
        active_ptt_rid: Optional[int] = None
        ptt_start_time = 0.0

        while self.running:
            try:
                if self.paused:
                    await asyncio.sleep(1.0)
                    continue

                cycle += 1
                now_iso = get_current_iso_timestamp()

                # Geofences für Alarmprüfung laden
                active_geofences = await self.db.get_geofences()

                # 1. GPS Einheiten bewegen
                for rid, unit in self.units.items():
                    if not unit.get("has_gps", True):
                        continue

                    unit["heading"] = (unit["heading"] + random.uniform(-15.0, 15.0)) % 360.0
                    step = (unit["speed"] / 3.6) * 3.0 / 111000.0
                    rad = math.radians(unit["heading"])
                    new_lat = unit["lat"] + step * math.cos(rad)
                    new_lon = unit["lon"] + (step * math.sin(rad)) / math.cos(math.radians(unit["lat"]))

                    if new_lat < self.lat_min or new_lat > self.lat_max:
                        unit["heading"] = (180.0 - unit["heading"]) % 360.0
                        new_lat = max(self.lat_min, min(self.lat_max, new_lat))

                    if new_lon < self.lon_min or new_lon > self.lon_max:
                        unit["heading"] = (360.0 - unit["heading"]) % 360.0
                        new_lon = max(self.lon_min, min(self.lon_max, new_lon))

                    unit["lat"] = new_lat
                    unit["lon"] = new_lon

                    # RSSI Pegel anhand Entfernung zum Repeater simulieren (-70 bis -110 dBm)
                    dist_to_center = calculate_distance_m(new_lat, new_lon, self.center_lat, self.center_lon)
                    sim_rssi = round(max(-115.0, min(-65.0, -70.0 - (dist_to_center / 35.0) + random.uniform(-3.0, 3.0))), 1)

                    await self.db.insert_gps(
                        radio_id=rid,
                        lat=round(unit["lat"], 6),
                        lon=round(unit["lon"], 6),
                        speed=round(unit["speed"], 1),
                        heading=round(unit["heading"], 1),
                        accuracy=2.5,
                        rssi=sim_rssi,
                        timestamp=now_iso
                    )

                    gps_event = {
                        "type": "gps_update",
                        "radio_id": rid,
                        "alias": unit["alias"],
                        "lat": round(unit["lat"], 6),
                        "lon": round(unit["lon"], 6),
                        "speed": round(unit["speed"], 1),
                        "heading": round(unit["heading"], 1),
                        "has_gps": True,
                        "floor_level": unit.get("floor", "EG"),
                        "rssi": sim_rssi,
                        "is_emergency": unit.get("is_emergency", False),
                        "timestamp": now_iso
                    }
                    await self.on_event_cb(gps_event)

                    # Geofence Überprüfung
                    for gf in active_geofences:
                        if gf["shape"] == "circle" and gf.get("center_lat") and gf.get("radius_m"):
                            d = calculate_distance_m(new_lat, new_lon, gf["center_lat"], gf["center_lon"])
                            if d <= gf["radius_m"]:
                                # Einheit befindet sich im Geofence!
                                gf_alert = {
                                    "type": "geofence_alert",
                                    "radio_id": rid,
                                    "alias": unit["alias"],
                                    "geofence_id": gf["id"],
                                    "geofence_name": gf["name"],
                                    "zone_type": gf["zone_type"],
                                    "timestamp": now_iso
                                }
                                await self.on_event_cb(gf_alert)

                # 2. PTT Funkspruch Simulation mit Audio-Generierung
                if active_ptt_rid is not None:
                    duration_ms = int((time.time() - ptt_start_time) * 1000)
                    slot = "TS1" if active_ptt_rid % 2 == 1 else "TS2"
                    call_type = "Gruppe"

                    unit = self.units[active_ptt_rid]
                    rssi_val = -78.0

                    ptt_id = await self.db.insert_ptt(
                        radio_id=active_ptt_rid,
                        duration_ms=duration_ms,
                        typ=call_type,
                        slot=slot,
                        rssi=rssi_val,
                        timestamp=now_iso
                    )

                    # Taktisches Funkspruch-Audio generieren
                    audio_url = audio_manager.generate_tactical_radio_audio(
                        call_id=ptt_id,
                        duration_sec=duration_ms / 1000.0,
                        alias=unit["alias"]
                    )
                    await self.db.update_ptt_audio(ptt_id, audio_url)

                    end_event = {
                        "type": "ptt_end",
                        "call_id": ptt_id,
                        "radio_id": active_ptt_rid,
                        "alias": unit["alias"],
                        "duration_ms": duration_ms,
                        "slot": slot,
                        "call_type": call_type,
                        "rssi": rssi_val,
                        "audio_url": audio_url,
                        "has_gps": unit.get("has_gps", True),
                        "timestamp": now_iso
                    }
                    await self.on_event_cb(end_event)
                    active_ptt_rid = None

                elif cycle % 2 == 0:
                    cand_rids = list(self.units.keys())
                    active_ptt_rid = random.choice(cand_rids)
                    ptt_start_time = time.time()
                    slot = "TS1" if active_ptt_rid % 2 == 1 else "TS2"
                    call_type = "Gruppe" if active_ptt_rid != 1001 else "AllCall"

                    unit = self.units[active_ptt_rid]
                    start_event = {
                        "type": "ptt_start",
                        "radio_id": active_ptt_rid,
                        "alias": unit["alias"],
                        "slot": slot,
                        "call_type": call_type,
                        "has_gps": unit.get("has_gps", True),
                        "lat": unit.get("lat"),
                        "lon": unit.get("lon"),
                        "floor_level": unit.get("floor", "EG"),
                        "timestamp": now_iso
                    }
                    await self.on_event_cb(start_event)

                await asyncio.sleep(3.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Fehler in Mock-Telemetrie: {e}")
                await asyncio.sleep(3.0)


class HyteraUDPManager:
    """Kombinierter Manager für UDP-Server und Mock-Generator."""

    def __init__(
        self,
        event_callback: Callable[[Dict[str, Any]], Any],
        db: Optional[DatabaseManager] = None,
        port: int = DEFAULT_UDP_PORT,
        enable_mock: bool = False
    ):
        self.event_callback = event_callback
        self.db = db or db_manager
        self.port = port
        self.enable_mock = enable_mock
        self.transport = None
        self.protocol = None
        self.mock_generator: Optional[MockTelemetryGenerator] = None

    async def _handle_incoming_event(self, event: Dict[str, Any]):
        ev_type = event.get("type")
        rid = event.get("radio_id")

        if ev_type == "gps":
            await self.db.insert_gps(
                radio_id=rid,
                lat=event["lat"],
                lon=event["lon"],
                speed=event.get("speed", 0.0),
                heading=event.get("heading", 0.0),
                accuracy=event.get("accuracy", 3.0),
                timestamp=event.get("timestamp")
            )
            radios = await self.db.get_radios()
            alias_map = {r["radio_id"]: r["alias"] for r in radios}
            event["type"] = "gps_update"
            event["alias"] = alias_map.get(rid, f"Radio {rid}")
            await self.event_callback(event)

        elif ev_type == "ptt_start":
            radios = await self.db.get_radios()
            alias_map = {r["radio_id"]: r["alias"] for r in radios}
            event["alias"] = alias_map.get(rid, f"Radio {rid}")
            if event.get("is_emergency"):
                em_info = await self.db.trigger_radio_emergency(rid, "DMR NOTRUF TASTE")
                await self.event_callback({"type": "emergency_alert", **em_info})
            await self.event_callback(event)

        elif ev_type == "ptt_end":
            duration_ms = event.get("duration_ms", 2500)
            ptt_id = await self.db.insert_ptt(
                radio_id=rid,
                duration_ms=duration_ms,
                typ=event.get("call_type", "Gruppe"),
                slot=event.get("slot", "TS1"),
                rssi=event.get("rssi"),
                timestamp=event.get("timestamp")
            )
            radios = await self.db.get_radios()
            alias_map = {r["radio_id"]: r["alias"] for r in radios}
            alias = alias_map.get(rid, f"Radio {rid}")
            event["alias"] = alias
            event["call_id"] = ptt_id

            audio_url = audio_manager.generate_tactical_radio_audio(ptt_id, duration_ms / 1000.0, alias)
            await self.db.update_ptt_audio(ptt_id, audio_url)
            event["audio_url"] = audio_url

            await self.event_callback(event)

    async def start(self):
        loop = asyncio.get_running_loop()
        try:
            self.transport, self.protocol = await loop.create_datagram_endpoint(
                lambda: HyteraUDPProtocol(self._handle_incoming_event),
                local_addr=("0.0.0.0", self.port)
            )
            logger.info(f"UDP-Listener auf 0.0.0.0:{self.port} aktiv (Echtbetrieb).")
        except Exception as e:
            logger.warning(f"Konnte UDP-Port {self.port} nicht binden (ggf. belegt): {e}")

        if self.enable_mock:
            self.mock_generator = MockTelemetryGenerator(self.event_callback, self.db)
            await self.mock_generator.start()

    async def toggle_mock(self) -> bool:
        if not self.mock_generator:
            self.mock_generator = MockTelemetryGenerator(self.event_callback, self.db)
            await self.mock_generator.start()
            return True
        return self.mock_generator.toggle_pause()

    def is_mock_active(self) -> bool:
        return self.mock_generator.is_active() if self.mock_generator else False

    async def stop(self):
        if self.mock_generator:
            await self.mock_generator.stop()
        if self.transport:
            self.transport.close()

