"""
Hytera Lageplan & Tactical Timeline - UDP Listener & Mock Generator (AGENT 2: Backend Engineer)
Asynchroner UDP-Empfänger für Hytera HR1065 Binärpakete (GPS & PTT/Call-Status)
sowie realistischer Mock-Generator für Test- und Demonstrationsbetrieb.
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

from .config import DEFAULT_UDP_PORT, DEFAULT_OVERLAY_BOUNDS
from .db_manager import DatabaseManager, db_manager, get_current_iso_timestamp

logger = logging.getLogger("udp_listener")


class HyteraBinaryParser:
    """
    Parser für Hytera HR1065 Binärprotokoll:
    1. GNSS / GPS Positionspakete (Port 30003)
    2. NAI RCP Call-Status / PTT Pakete (Opcode 0x0004 & 0xB845)
    """

    CALL_TYPES = {
        0x01: "Privat",
        0x02: "Gruppe",
        0x03: "AllCall",
    }

    @staticmethod
    def parse_gps_packet(payload: bytes) -> Optional[Dict[str, Any]]:
        """
        Extrahiert Funkgeräte-ID und Koordinaten aus einem Hytera GNSS-UDP-Paket.
        Format:
          payload[0]: Nachrichtenkopf
          payload[1:3]: Opcode (<H)
          payload[3:5]: Datenlänge (<H)
          payload[5:]: Nutzdaten
            data[0:4]: Radio-IP (>I) -> Radio-ID = IP & 0xFFFFFF
            data[4:8]: Breitengrad (>i) int32 / 1e7
            data[8:12]: Längengrad (>i) int32 / 1e7
        """
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

            # Plausibilitätsprüfung der Koordinaten
            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and radio_id > 0:
                # Optionale Geschwindigkeit/Richtung auslesen falls Paket länger
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
            logger.debug(f"Fehler beim Parsen des GPS-Pakets: {e}")
        return None

    @staticmethod
    def parse_call_packet(payload: bytes) -> Optional[Dict[str, Any]]:
        """
        Parst Hytera HR1065 NAI RCP Call-Status Pakete (Opcode 0x0004 oder 0xB845).
        Erkennt Beginn und Ende von PTT-Übertragungen sowie Timeslot und Funktyp.
        """
        if len(payload) < 20:
            return None

        try:
            opcode = struct.unpack_from('<H', payload, 1)[0]

            # 0x0004: Standard HR1065 NAI Call-Status
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

                if is_active:
                    return {
                        "type": "ptt_start",
                        "radio_id": sender_id,
                        "slot": slot_name,
                        "call_type": call_type,
                        "rssi": rssi_dbm,
                        "timestamp": get_current_iso_timestamp()
                    }
                elif is_end:
                    return {
                        "type": "ptt_end",
                        "radio_id": sender_id,
                        "slot": slot_name,
                        "call_type": call_type,
                        "rssi": rssi_dbm,
                        "timestamp": get_current_iso_timestamp()
                    }

            # 0xB845: Legacy Repeater Broadcast TX Status
            elif opcode == 0xB845 and len(payload) >= 21:
                n_bytes = struct.unpack_from('<H', payload, 3)[0]
                data = payload[5:5 + n_bytes]
                if len(data) >= 16:
                    mode, status, svctype, call_type_id, target_id, sender_id = struct.unpack_from('<HHHH II', data)
                    if sender_id > 0:
                        is_active = (status == 1 or status == 2)
                        return {
                            "type": "ptt_start" if is_active else "ptt_end",
                            "radio_id": sender_id,
                            "slot": "TS1",
                            "call_type": HyteraBinaryParser.CALL_TYPES.get(call_type_id, "Gruppe"),
                            "timestamp": get_current_iso_timestamp()
                        }
        except Exception as e:
            logger.debug(f"Fehler beim Parsen des PTT-Pakets: {e}")
        return None


class HyteraUDPProtocol(asyncio.DatagramProtocol):
    """Asynchrones asyncio UDP-Protokoll zum Empfangen von Hytera-Paketen."""

    def __init__(self, on_event_cb: Callable[[Dict[str, Any]], Any]):
        self.on_event_cb = on_event_cb
        self.transport = None
        self._tx_start_times: Dict[int, float] = {}

    def connection_made(self, transport):
        self.transport = transport
        sockname = transport.get_extra_info("sockname")
        logger.info(f"Hytera UDP-Listener gebunden an: {sockname}")

    def datagram_received(self, data: bytes, addr):
        # 1. Zunächst auf GPS prüfen
        gps_event = HyteraBinaryParser.parse_gps_packet(data)
        if gps_event:
            asyncio.create_task(self.on_event_cb(gps_event))
            return

        # 2. Auf Call / PTT prüfen
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
        logger.error(f"UDP-Listener Fehler: {exc}")

    def connection_lost(self, exc):
        logger.info("UDP-Listener Verbindung getrennt.")


class MockTelemetryGenerator:
    """
    Simuliert realistischen Funk- und GPS-Betrieb für 5 DMR-Endgeräte (Hytera HR1065 Einsatzszenario).
    Generiert alle 3 Sekunden GPS- und PTT-Events für flüssige Kartendarstellung und Timeline-Tests.
    """

    def __init__(self, on_event_cb: Callable[[Dict[str, Any]], Any], db: DatabaseManager):
        self.on_event_cb = on_event_cb
        self.db = db
        self.running = False
        self._task: Optional[asyncio.Task] = None

        # Standard-Einsatzgebiet aus Config
        bounds = DEFAULT_OVERLAY_BOUNDS
        self.lat_min = min(bounds[0][0], bounds[1][0])
        self.lat_max = max(bounds[0][0], bounds[1][0])
        self.lon_min = min(bounds[0][1], bounds[1][1])
        self.lon_max = max(bounds[0][1], bounds[1][1])

        self.center_lat = (self.lat_min + self.lat_max) / 2.0
        self.center_lon = (self.lon_min + self.lon_max) / 2.0

        self.paused = False

        # 5 simulierte Einsatzkräfte:
        # Realistisches gemischtes Einsatzszenario: Manche mit GPS-Empfänger, manche Handfunkgeräte ohne GPS!
        self.units: Dict[int, Dict[str, Any]] = {
            1001: {
                "alias": "Jan2 (ELW 1)",
                "lat": self.center_lat - 0.0020,
                "lon": self.center_lon - 0.0030,
                "heading": 45.0,
                "speed": 8.5,
                "model": "HR1065 Fixed/ELW",
                "role": "command",
                "has_gps": True
            },
            1002: {
                "alias": "Mitarbeiter 2 (RT81 #2)",
                "lat": self.center_lat + 0.0025,
                "lon": self.center_lon + 0.0035,
                "heading": 190.0,
                "speed": 0.0,
                "model": "RT81 Portable (Kein GPS)",
                "role": "patrol",
                "has_gps": False  # Kleines Handfunkgerät ohne GPS
            },
            1003: {
                "alias": "Mario (Angriffstrupp)",
                "lat": self.center_lat + 0.0010,
                "lon": self.center_lon - 0.0020,
                "heading": 90.0,
                "speed": 3.6,
                "model": "HP785 MD655",
                "role": "tactical",
                "has_gps": True
            },
            1010: {
                "alias": "Jürgen (Wassertrupp)",
                "lat": self.center_lat - 0.0015,
                "lon": self.center_lon + 0.0015,
                "heading": 300.0,
                "speed": 0.0,
                "model": "RT81 Portable (Kein GPS)",
                "role": "tactical",
                "has_gps": False  # Handfunkgerät ohne GPS
            },
            1059: {
                "alias": "Tasnim (Sanitäter MTW)",
                "lat": self.center_lat + 0.0030,
                "lon": self.center_lon - 0.0010,
                "heading": 135.0,
                "speed": 12.0,
                "model": "HM785 Mobile",
                "role": "medical",
                "has_gps": True
            }
        }

    def toggle_pause(self) -> bool:
        """Pausiert oder reaktiviert die Mock-Simulation zur Laufzeit."""
        self.paused = not self.paused
        logger.info(f"Mock-Simulation Pausierungsstatus: {self.paused}")
        return not self.paused

    def is_active(self) -> bool:
        return self.running and not self.paused

    async def start(self):
        """Startet den asynchronen Simulations-Loop."""
        if self.running:
            return
        self.running = True
        self.paused = False

        # Initial Funkgeräte-Aliase & GPS-Fähigkeit in Datenbank registrieren
        for rid, info in self.units.items():
            fixed_lat = info["lat"] if not info["has_gps"] else None
            fixed_lon = info["lon"] if not info["has_gps"] else None
            await self.db.upsert_radio(
                radio_id=rid,
                alias=info["alias"],
                device_model=info["model"],
                has_gps=info["has_gps"],
                fixed_lat=fixed_lat,
                fixed_lon=fixed_lon
            )

        self._task = asyncio.create_task(self._run_loop())
        logger.info("Mock-Telemetrie-Generator gestartet (Intervall: 3s).")

    async def stop(self):
        """Stoppt den Simulations-Loop."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Mock-Telemetrie-Generator beendet.")

    async def _run_loop(self):
        """Periodische Generierung von GPS-Positionen und PTT-Funkprüchen."""
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

                # 1. Einheiten mit GPS bewegen und GPS-Updates senden
                for rid, unit in self.units.items():
                    if not unit.get("has_gps", True):
                        # Handfunkgeräte ohne GPS senden keine Bewegungskoordinaten
                        continue

                    # Kurs leicht variieren (-15 bis +15 Grad)
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

                    await self.db.insert_gps(
                        radio_id=rid,
                        lat=round(unit["lat"], 6),
                        lon=round(unit["lon"], 6),
                        speed=round(unit["speed"], 1),
                        heading=round(unit["heading"], 1),
                        accuracy=2.5,
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
                        "timestamp": now_iso
                    }
                    await self.on_event_cb(gps_event)

                # 2. PTT-Simulation (alle Einheiten, auch die OHNE GPS funken!)
                if active_ptt_rid is not None:
                    duration_ms = int((time.time() - ptt_start_time) * 1000)
                    slot = "TS1" if active_ptt_rid % 2 == 1 else "TS2"
                    call_type = "Gruppe"

                    await self.db.insert_ptt(
                        radio_id=active_ptt_rid,
                        duration_ms=duration_ms,
                        typ=call_type,
                        slot=slot,
                        rssi=-78.0,
                        timestamp=now_iso
                    )

                    end_event = {
                        "type": "ptt_end",
                        "radio_id": active_ptt_rid,
                        "alias": self.units[active_ptt_rid]["alias"],
                        "duration_ms": duration_ms,
                        "slot": slot,
                        "call_type": call_type,
                        "has_gps": self.units[active_ptt_rid].get("has_gps", True),
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

                    start_event = {
                        "type": "ptt_start",
                        "radio_id": active_ptt_rid,
                        "alias": self.units[active_ptt_rid]["alias"],
                        "slot": slot,
                        "call_type": call_type,
                        "has_gps": self.units[active_ptt_rid].get("has_gps", True),
                        "lat": self.units[active_ptt_rid].get("lat"),
                        "lon": self.units[active_ptt_rid].get("lon"),
                        "timestamp": now_iso
                    }
                    await self.on_event_cb(start_event)

                await asyncio.sleep(3.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Fehler in Mock-Telemetrie-Schleife: {e}")
                await asyncio.sleep(3.0)


class HyteraUDPManager:
    """
    Kombinierter Manager für den Hytera UDP-Server und optionalen Mock-Generator.
    """

    def __init__(
        self,
        event_callback: Callable[[Dict[str, Any]], Any],
        db: Optional[DatabaseManager] = None,
        port: int = DEFAULT_UDP_PORT,
        enable_mock: bool = True
    ):
        self.event_callback = event_callback
        self.db = db or db_manager
        self.port = port
        self.enable_mock = enable_mock

        self.transport = None
        self.protocol = None
        self.mock_generator: Optional[MockTelemetryGenerator] = None

    async def _handle_incoming_event(self, event: Dict[str, Any]):
        """Verarbeitet eingehende Events vom echten UDP-Port."""
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
            # Funkgeräte-Alias ermitteln
            radios = await self.db.get_radios()
            alias_map = {r["radio_id"]: r["alias"] for r in radios}
            event["type"] = "gps_update"
            event["alias"] = alias_map.get(rid, f"Radio {rid}")
            await self.event_callback(event)

        elif ev_type == "ptt_start":
            radios = await self.db.get_radios()
            alias_map = {r["radio_id"]: r["alias"] for r in radios}
            event["alias"] = alias_map.get(rid, f"Radio {rid}")
            await self.event_callback(event)

        elif ev_type == "ptt_end":
            duration_ms = event.get("duration_ms", 2500)
            await self.db.insert_ptt(
                radio_id=rid,
                duration_ms=duration_ms,
                typ=event.get("call_type", "Gruppe"),
                slot=event.get("slot", "TS1"),
                rssi=event.get("rssi"),
                timestamp=event.get("timestamp")
            )
            radios = await self.db.get_radios()
            alias_map = {r["radio_id"]: r["alias"] for r in radios}
            event["alias"] = alias_map.get(rid, f"Radio {rid}")
            await self.event_callback(event)

    async def start(self):
        """Startet den echten UDP-Socket-Listener und bei Bedarf den Mock-Generator."""
        loop = asyncio.get_running_loop()

        try:
            self.transport, self.protocol = await loop.create_datagram_endpoint(
                lambda: HyteraUDPProtocol(self._handle_incoming_event),
                local_addr=("0.0.0.0", self.port)
            )
            logger.info(f"Hytera UDP-Listener erfolgreich auf 0.0.0.0:{self.port} gestartet.")
        except Exception as e:
            logger.warning(f"Konnte UDP-Port {self.port} nicht öffnen (evtl. bereits durch Hauptsoftware belegt): {e}")

        # Mock-Generator starten, falls aktiviert
        if self.enable_mock:
            self.mock_generator = MockTelemetryGenerator(self.event_callback, self.db)
            await self.mock_generator.start()

    def toggle_mock(self) -> bool:
        """Pausiert oder reaktiviert den Mock-Generator."""
        if self.mock_generator:
            return self.mock_generator.toggle_pause()
        return False

    def is_mock_active(self) -> bool:
        if self.mock_generator:
            return self.mock_generator.is_active()
        return False

    async def stop(self):
        """Beendet UDP-Listener und Mock-Generator sauber."""
        if self.mock_generator:
            await self.mock_generator.stop()
        if self.transport:
            self.transport.close()
        logger.info("Hytera UDP-Manager sauber beendet.")
