"""
Hytera Command Center – FastAPI Hauptanwendung
Unified Backend für alle Systemkomponenten:

  /           → Frontend (index.html)
  /ws         → WebSocket (alle Echtzeit-Events)
  /api/...    → REST-Endpunkte
  /tiles/...  → Offline-Karten-Proxy (OSM)
  /uploads/.. → Hochgeladene Lagepläne

WebSocket Event-Typen (Server → Client):
  gps, ptt_start, ptt_end, emergency, rrs_register, rrs_offline,
  sms_received, packet_dump, raw_packet,
  repeater_update, repeater_alarm,
  ups_update, zte_update, omada_update,
  system_status
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles

try:
    from .config import (
        FRONTEND_DIR, STATIC_DIR, UPLOADS_DIR, TILES_DIR, RECORDINGS_DIR,
        DEFAULT_SETTINGS, UPS_IP, UPS_MODBUS_PORT, ZTE_IP, OMADA_IP,
        REPEATER_IP, SNMP_TRAP_PORT, HTTP_HOST, HTTP_PORT,
    )
    from .db_manager import DatabaseManager, db_manager
    from .protocol.manager import UDPManager
    from .hardware.snmp_trap import SNMPTrapMonitor
    from .hardware.ups_modbus import UPSMonitor
    from .hardware.router_monitor import ZTEMonitor, OmadaER606Monitor
    from .services.audio_recorder import AudioRecorder
except ImportError:
    try:
        from backend.config import (
            FRONTEND_DIR, STATIC_DIR, UPLOADS_DIR, TILES_DIR, RECORDINGS_DIR,
            DEFAULT_SETTINGS, UPS_IP, UPS_MODBUS_PORT, ZTE_IP, OMADA_IP,
            REPEATER_IP, SNMP_TRAP_PORT, HTTP_HOST, HTTP_PORT,
        )
        from backend.db_manager import DatabaseManager, db_manager
        from backend.protocol.manager import UDPManager
        from backend.hardware.snmp_trap import SNMPTrapMonitor
        from backend.hardware.ups_modbus import UPSMonitor
        from backend.hardware.router_monitor import ZTEMonitor, OmadaER606Monitor
        from backend.services.audio_recorder import AudioRecorder
    except ImportError:
        from config import (
            FRONTEND_DIR, STATIC_DIR, UPLOADS_DIR, TILES_DIR, RECORDINGS_DIR,
            DEFAULT_SETTINGS, UPS_IP, UPS_MODBUS_PORT, ZTE_IP, OMADA_IP,
            REPEATER_IP, SNMP_TRAP_PORT, HTTP_HOST, HTTP_PORT,
        )
        from db_manager import DatabaseManager, db_manager
        from protocol.manager import UDPManager
        from hardware.snmp_trap import SNMPTrapMonitor
        from hardware.ups_modbus import UPSMonitor
        from hardware.router_monitor import ZTEMonitor, OmadaER606Monitor
        from services.audio_recorder import AudioRecorder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


# ── WebSocket-Manager ────────────────────────────────────────────────────────

class WebSocketManager:
    """Verwaltet alle aktiven WebSocket-Verbindungen und Broadcasts."""

    def __init__(self):
        self._connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        logger.info(f"WebSocket verbunden ({len(self._connections)} aktiv)")

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        logger.info(f"WebSocket getrennt ({len(self._connections)} aktiv)")

    async def _send_safe(self, ws: WebSocket, data: Dict[str, Any]) -> Optional[WebSocket]:
        try:
            await ws.send_json(data)
            return None
        except Exception:
            return ws

    async def broadcast(self, data: Dict[str, Any]) -> None:
        if not self._connections:
            return
        conns = list(self._connections)
        results = await asyncio.gather(*[self._send_safe(ws, data) for ws in conns], return_exceptions=True)
        for res in results:
            if isinstance(res, WebSocket):
                self._connections.discard(res)

    def count(self) -> int:
        return len(self._connections)


ws_manager = WebSocketManager()

# Globale Hardware-Monitore (werden in lifespan konfiguriert)
_udp_manager:   Optional[UDPManager]       = None
_snmp_monitor:  Optional[SNMPTrapMonitor]  = None
_ups_monitor:   Optional[UPSMonitor]       = None
_zte_monitor:   Optional[ZTEMonitor]       = None
_omada_monitor: Optional[OmadaER606Monitor] = None
_audio_recorder: Optional[AudioRecorder]   = None


async def _broadcast_event(event: Dict[str, Any]) -> None:
    """
    Master-Callback: Empfängt Events von allen Subsystemen
    und persistiert/broadcastet sie.
    """
    ev_type = event.get("type", "")

    # GPS → Datenbank + WebSocket
    if ev_type == "gps":
        radio_id = event.get("radio_id")
        lat      = event.get("lat")
        lon      = event.get("lon")
        if radio_id and lat is not None and lon is not None:
            await db_manager.upsert_radio(radio_id, f"Radio {radio_id}")
            await db_manager.insert_gps(
                radio_id  = radio_id,
                lat       = lat,
                lon       = lon,
                speed     = event.get("speed", 0.0),
                heading   = event.get("heading", 0.0),
                rssi      = event.get("rssi"),
                timestamp = event.get("timestamp"),
            )
            if event.get("rssi") is not None:
                await db_manager.set_radio_rssi(radio_id, event["rssi"])
        await ws_manager.broadcast(event)

    # PTT-Start → WebSocket
    elif ev_type == "ptt_start":
        await ws_manager.broadcast(event)

    # PTT-Ende → Datenbank + WebSocket + mission_id zuweisen
    elif ev_type == "ptt_end":
        radio_id    = event.get("radio_id")
        duration_ms = event.get("duration_ms", 0)
        if radio_id and duration_ms > 0:
            await db_manager.upsert_radio(radio_id, f"Radio {radio_id}")
            mission_id = await db_manager.get_active_mission_id()
            ptt_id = await db_manager.insert_ptt(
                radio_id    = radio_id,
                duration_ms = duration_ms,
                typ         = event.get("call_type", "Gruppe"),
                slot        = event.get("slot", "TS1"),
                rssi        = event.get("rssi"),
            )
            # mission_id sofort taggen
            if mission_id and ptt_id:
                async with db_manager.get_connection() as _db:
                    await _db.execute(
                        "UPDATE ptt_logs SET mission_id=? WHERE id=?",
                        (mission_id, ptt_id)
                    )
                    await _db.commit()
            event["ptt_id"] = ptt_id
        await ws_manager.broadcast(event)

    # Notruf → Datenbank + WebSocket
    elif ev_type == "emergency":
        radio_id = event.get("radio_id")
        if radio_id:
            await db_manager.upsert_radio(radio_id, f"Radio {radio_id}")
            await db_manager.trigger_radio_emergency(radio_id, event.get("call_type", "NOTRUF"))
        await ws_manager.broadcast(event)

    # RRS Online
    elif ev_type == "rrs_register":
        radio_id = event.get("radio_id")
        if radio_id:
            await db_manager.upsert_radio(radio_id, f"Radio {radio_id}")
            await db_manager.set_radio_online(radio_id, True, event.get("slot", "TS1"))
        await ws_manager.broadcast(event)

    # RRS Offline
    elif ev_type == "rrs_offline":
        radio_id = event.get("radio_id")
        if radio_id:
            await db_manager.set_radio_online(radio_id, False, event.get("slot", "TS1"))
        await ws_manager.broadcast(event)

    # SMS → Datenbank + WebSocket + mission_id zuweisen
    elif ev_type == "sms_received":
        sender_id = event.get("sender_id")
        if sender_id:
            await db_manager.upsert_radio(sender_id, f"Radio {sender_id}")
            mission_id = await db_manager.get_active_mission_id()
            sms_id = await db_manager.insert_sms(
                sender_id = sender_id,
                target_id = event.get("target_id", 0),
                text      = event.get("text", ""),
                is_group  = event.get("is_group", False),
                ack       = False,
            )
            if mission_id and sms_id:
                async with db_manager.get_connection() as _db:
                    await _db.execute(
                        "UPDATE sms_messages SET mission_id=? WHERE id=?",
                        (mission_id, sms_id)
                    )
                    await _db.commit()
        await ws_manager.broadcast(event)

    # Repeater SNMP-Update → Datenbank + WebSocket
    elif ev_type == "repeater_update":
        await ws_manager.broadcast(event)

    # Repeater-Alarm → Datenbank + WebSocket
    elif ev_type == "repeater_alarm":
        await db_manager.insert_snmp_event(
            oid_key     = event.get("oid_key", ""),
            oid_label   = event.get("oid_label", ""),
            value       = str(event.get("value", "")),
            alarm_level = event.get("alarm_level", "warning"),
        )
        await ws_manager.broadcast(event)

    # USV-Update → WebSocket (kein DB, zu häufig)
    elif ev_type == "ups_update":
        await ws_manager.broadcast(event)

    # ZTE-Update → WebSocket
    elif ev_type == "zte_update":
        await ws_manager.broadcast(event)

    # Omada-Update → WebSocket
    elif ev_type == "omada_update":
        await ws_manager.broadcast(event)

    # Audio-RTP → an Recorder weiterleiten (kein Broadcast des rohen Payloads)
    elif ev_type == "audio_rtp":
        if _audio_recorder and _audio_recorder.enabled:
            raw  = bytes.fromhex(event.get("raw_hex", "")) if event.get("raw_hex") else b""
            port = event.get("port", 30012)
            rid  = event.get("radio_id", 0)
            if raw:
                await _audio_recorder.process_rtp_event(port, raw, rid)
        # Nur Stats broadcasten, nie rohen Payload
        if ws_manager.count() > 0:
            await ws_manager.broadcast({
                "type": "audio_rtp",
                "port": event.get("port"),
                "radio_id": event.get("radio_id", 0),
            })

    # Alles andere
    else:
        await ws_manager.broadcast(event)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startet/Stoppt alle Background-Services."""
    global _udp_manager, _snmp_monitor, _ups_monitor, _zte_monitor, _omada_monitor, _audio_recorder

    # DB initialisieren
    await db_manager.init_db()
    logger.info("Datenbank bereit.")

    # Settings laden
    settings = {**DEFAULT_SETTINGS, **await db_manager.get_all_settings()}

    # Hardware-IPs aus Settings
    ups_ip    = settings.get("hw_usv_ip", UPS_IP)
    zte_ip    = settings.get("hw_zte_ip", ZTE_IP)
    omada_ip  = settings.get("hw_omada_ip", OMADA_IP)

    # Protokoll-Manager
    mock_active = settings.get("mock_active", False)
    _udp_manager = UDPManager(
        event_callback = _broadcast_event,
        enable_mock    = mock_active,
    )
    await _udp_manager.start()

    # SNMP Trap Monitor
    async def _snmp_callback(trap_event) -> None:
        d = trap_event.to_dict() if hasattr(trap_event, "to_dict") else dict(trap_event)
        d["type"] = "repeater_alarm"
        await _broadcast_event(d)

    _snmp_monitor = SNMPTrapMonitor(
        port    = settings.get("hw_repeater_trap_port", SNMP_TRAP_PORT),
        on_trap = _snmp_callback,
    )
    asyncio.create_task(_snmp_monitor.start(), name="snmp_monitor")

    # USV Modbus Monitor
    if not settings.get("hw_simulation", False):
        async def _ups_callback(state) -> None:
            d = state.to_dict() if hasattr(state, "to_dict") else vars(state)
            d["type"] = "ups_update"
            await ws_manager.broadcast(d)

        _ups_monitor = UPSMonitor(
            host      = ups_ip,
            on_update = _ups_callback,
        )
        asyncio.create_task(_ups_monitor.start(), name="ups_monitor")

        # Router-Monitore
        async def _zte_callback(state) -> None:
            d = state.to_dict() if hasattr(state, "to_dict") else vars(state)
            d["type"] = "zte_update"
            await ws_manager.broadcast(d)

        _zte_monitor = ZTEMonitor(
            host      = zte_ip,
            on_update = _zte_callback,
        )
        asyncio.create_task(_zte_monitor.start(), name="zte_monitor")

        async def _omada_callback(state) -> None:
            d = state.to_dict() if hasattr(state, "to_dict") else vars(state)
            d["type"] = "omada_update"
            await ws_manager.broadcast(d)

        _omada_monitor = OmadaER606Monitor(
            host      = omada_ip,
            on_update = _omada_callback,
        )
        asyncio.create_task(_omada_monitor.start(), name="omada_monitor")

    # System-Status-Broadcast alle 60s
    async def _status_loop():
        while True:
            await asyncio.sleep(60)
            await ws_manager.broadcast({
                "type":         "system_status",
                "ws_clients":   ws_manager.count(),
                "mock_active":  _udp_manager.is_mock_active() if _udp_manager else False,
                "uptime":       time.time(),
                "port_stats":   _udp_manager.get_port_stats() if _udp_manager else [],
            })

    _status_task = asyncio.create_task(_status_loop(), name="system_status_loop")

    # Audio-Recorder starten
    async def _on_recording_done(radio_id: int, filepath: str, duration_s: float, ptt_id: Optional[int] = None) -> None:
        """Callback: Aufnahme fertig – audio_url in ptt_logs eintragen."""
        try:
            async with db_manager.get_connection() as _db:
                target_ptt_id = ptt_id
                if not target_ptt_id:
                    # Jüngstes PTT-Log dieses Radios ohne audio_url aktualisieren
                    cur = await _db.execute(
                        """SELECT id FROM ptt_logs
                           WHERE radio_id=? AND audio_url IS NULL
                           ORDER BY timestamp DESC LIMIT 1""",
                        (radio_id,)
                    )
                    row = await cur.fetchone()
                    if row:
                        target_ptt_id = row["id"]

                if target_ptt_id:
                    await _db.execute(
                        "UPDATE ptt_logs SET audio_url=? WHERE id=?",
                        (filepath, target_ptt_id)
                    )
                    await _db.commit()
                    logger.info(f"Audio verknüpft: PTT #{target_ptt_id} → {os.path.basename(filepath)}")
                    await ws_manager.broadcast({
                        "type":     "audio_ready",
                        "ptt_id":   target_ptt_id,
                        "radio_id": radio_id,
                        "duration": round(duration_s, 1),
                        "url":      f"/api/audio/{target_ptt_id}",
                    })
        except Exception as exc:
            logger.error(f"audio_url Update fehlgeschlagen: {exc}")

    _audio_recorder = AudioRecorder(
        recordings_dir = RECORDINGS_DIR,
        on_done        = _on_recording_done,
        enabled        = settings.get("audio_enabled", True),
    )
    await _audio_recorder.start()

    import socket
    local_lan_ip = "127.0.0.1"
    try:
        _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _s.connect(("8.8.8.8", 80))
        local_lan_ip = _s.getsockname()[0]
        _s.close()
    except Exception:
        pass

    logger.info("=" * 45)
    logger.info("  Hytera Command Center - bereit")
    logger.info(f"  Lokal: http://localhost:{HTTP_PORT}")
    logger.info(f"  LAN:   http://{local_lan_ip}:{HTTP_PORT}")
    logger.info("=" * 45)

    yield

    # Cleanup
    logger.info("Shutdown...")
    _status_task.cancel()
    if _audio_recorder: _audio_recorder.stop()
    if _udp_manager:   await _udp_manager.stop()
    if _snmp_monitor:  _snmp_monitor.stop()
    if _ups_monitor:   _ups_monitor.stop()
    if _zte_monitor:   _zte_monitor.stop()
    if _omada_monitor: _omada_monitor.stop()


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title        = "Hytera Command Center",
    description  = "ELW Führungsunterstützungssystem",
    version      = "1.0.0",
    lifespan     = lifespan,
    docs_url     = "/api/docs",
    redoc_url    = None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# Static Files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)

    # Initiale Daten nach Verbindungsaufbau senden
    try:
        radios   = await db_manager.get_radios()
        gps_data = await db_manager.get_recent_gps()
        calls    = await db_manager.get_recent_calls(20)
        markers  = await db_manager.get_markers()
        settings = {**DEFAULT_SETTINGS, **await db_manager.get_all_settings()}
        mission  = await db_manager.get_mission_data()
        snmp_evs = await db_manager.get_snmp_events(50)

        await ws.send_json({"type": "init", "radios": radios, "gps": gps_data,
                            "calls": calls, "markers": markers,
                            "settings": settings, "mission": mission,
                            "snmp_events": snmp_evs})

        # Aktueller Hardware-Status – sicher abfragen
        try:
            if _ups_monitor:
                await ws.send_json({"type": "ups_update", **_ups_monitor.get_state()})
        except Exception as e:
            logger.debug(f"WS Init UPS: {e}")
        try:
            if _zte_monitor:
                await ws.send_json({"type": "zte_update", **_zte_monitor.get_state()})
        except Exception as e:
            logger.debug(f"WS Init ZTE: {e}")
        try:
            if _omada_monitor:
                await ws.send_json({"type": "omada_update", **_omada_monitor.get_state()})
        except Exception as e:
            logger.debug(f"WS Init Omada: {e}")
        try:
            if _snmp_monitor:
                await ws.send_json({"type": "repeater_update", **_snmp_monitor.get_state()})
        except Exception as e:
            logger.debug(f"WS Init Repeater: {e}")

        # Verbindung offenhalten
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                # Client-Ping-Pong
                if msg == "ping":
                    await ws.send_text("pong")
            except asyncio.TimeoutError:
                await ws.send_text("ping")
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error(f"WebSocket Fehler: {exc}")
    finally:
        ws_manager.disconnect(ws)


# ── REST: Funkgeräte ──────────────────────────────────────────────────────────

@app.get("/api/radios")
async def get_radios():
    return await db_manager.get_radios()


@app.post("/api/radios")
async def create_radio(data: Dict[str, Any] = Body(...)):
    radio_id = int(data.get("radio_id", 0))
    if not radio_id:
        raise HTTPException(400, "radio_id erforderlich")
    alias = data.get("alias", f"Radio {radio_id}")
    model = data.get("device_model", "")
    await db_manager.upsert_radio(radio_id, alias, model)
    return {"ok": True, "radio_id": radio_id}


@app.put("/api/radios/{radio_id}")
async def update_radio(radio_id: int, data: Dict[str, Any] = Body(...)):
    await db_manager.upsert_radio(
        radio_id     = radio_id,
        alias        = data.get("alias", ""),
        device_model = data.get("device_model", ""),
        has_gps      = data.get("has_gps"),
        fixed_lat    = data.get("fixed_lat"),
        fixed_lon    = data.get("fixed_lon"),
        floor_level  = data.get("floor_level"),
    )
    return {"ok": True}


@app.delete("/api/radios/{radio_id}")
async def delete_radio(radio_id: int):
    ok = await db_manager.delete_radio(radio_id)
    return {"ok": ok}


@app.post("/api/radios/{radio_id}/emergency")
async def trigger_emergency(radio_id: int, data: Dict[str, Any] = Body({})):
    result = await db_manager.trigger_radio_emergency(
        radio_id, data.get("emergency_type", "NOTRUF")
    )
    await ws_manager.broadcast({"type": "emergency", **result})
    return result


@app.post("/api/radios/{radio_id}/ack_emergency")
async def ack_emergency(radio_id: int):
    result = await db_manager.ack_radio_emergency(radio_id)
    await ws_manager.broadcast({"type": "emergency_ack", **result})
    return result


@app.put("/api/radios/{radio_id}/floor")
async def set_floor(radio_id: int, data: Dict[str, Any] = Body(...)):
    floor = data.get("floor_level", "EG")
    await db_manager.set_radio_floor(radio_id, floor)
    await ws_manager.broadcast({"type": "radio_floor_update", "radio_id": radio_id, "floor_level": floor})
    return {"ok": True}


@app.put("/api/radios/{radio_id}/position")
async def set_position(radio_id: int, data: Dict[str, Any] = Body(...)):
    lat = data.get("lat")
    lon = data.get("lon")
    await db_manager.set_radio_fixed_position(radio_id, lat, lon)
    if lat is not None and lon is not None:
        await ws_manager.broadcast({
            "type": "gps", "radio_id": radio_id, "lat": lat, "lon": lon,
            "speed": 0, "heading": 0, "rssi": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    return {"ok": True}


# ── REST: GPS ─────────────────────────────────────────────────────────────────

@app.get("/api/gps/recent")
async def get_recent_gps():
    return await db_manager.get_recent_gps()


@app.get("/api/gps/{radio_id}/history")
async def get_gps_history(radio_id: int, limit: int = 200):
    return await db_manager.get_gps_history(radio_id, limit)


@app.get("/api/gps/rssi_coverage")
async def get_rssi_coverage(limit: int = 500):
    return await db_manager.get_rssi_coverage_points(limit)


# ── REST: Anrufe/PTT ──────────────────────────────────────────────────────────

@app.get("/api/calls")
async def get_recent_calls(limit: int = 50):
    return await db_manager.get_recent_calls(limit)


# ── REST: SMS ─────────────────────────────────────────────────────────────────

@app.get("/api/sms")
async def get_sms(limit: int = 50):
    return await db_manager.get_recent_sms(limit)


@app.post("/api/sms/send")
async def send_sms(data: Dict[str, Any] = Body(...)):
    """Speichert ausgehende SMS (tatsächliches Senden erfordert separaten Sender)."""
    sender_id = data.get("sender_id", 0)
    target_id = data.get("target_id", 0)
    text      = data.get("text", "")
    if not text:
        raise HTTPException(400, "Text erforderlich")
    sms_id = await db_manager.insert_sms(
        sender_id = sender_id,
        target_id = target_id,
        text      = text,
        direction = "outgoing",
    )
    event = {
        "type":      "sms_received",
        "id":        sms_id,
        "sender_id": sender_id,
        "target_id": target_id,
        "text":      text,
        "direction": "outgoing",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await ws_manager.broadcast(event)
    return {"ok": True, "id": sms_id}


# ── REST: Radio-Ereignisse ────────────────────────────────────────────────────

@app.get("/api/radio_events")
async def get_radio_events(limit: int = 50):
    return await db_manager.get_recent_radio_events(limit)


# ── REST: Marker ──────────────────────────────────────────────────────────────

@app.get("/api/markers")
async def get_markers():
    return await db_manager.get_markers()


@app.post("/api/markers")
async def create_marker(data: Dict[str, Any] = Body(...)):
    lat = data.get("lat")
    lon = data.get("lon")
    if lat is None or lon is None:
        raise HTTPException(400, "lat/lon erforderlich")
    marker_id = await db_manager.insert_marker(
        lat             = lat,
        lon             = lon,
        typ             = data.get("typ", "info"),
        beschreibung    = data.get("beschreibung", ""),
        prioritaet      = data.get("prioritaet", "normal"),
        author          = data.get("author", "Operator"),
        tactical_symbol = data.get("tactical_symbol", ""),
    )
    marker = {"id": marker_id, "lat": lat, "lon": lon, **data}
    await ws_manager.broadcast({"type": "marker_added", "marker": marker})
    return {"ok": True, "id": marker_id, "marker": marker}


@app.delete("/api/markers/{marker_id}")
async def delete_marker(marker_id: int):
    ok = await db_manager.delete_marker(marker_id)
    if ok:
        await ws_manager.broadcast({"type": "marker_deleted", "id": marker_id})
    return {"ok": ok}


# ── REST: Lagepläne ──────────────────────────────────────────────────────────

@app.get("/api/plans")
async def get_plans():
    return await db_manager.get_plans()


@app.post("/api/plans")
async def upload_plan(
    name:        str         = Form(...),
    floor_level: str         = Form(...),
    sw_lat:      float       = Form(...),
    sw_lon:      float       = Form(...),
    ne_lat:      float       = Form(...),
    ne_lon:      float       = Form(...),
    opacity:     float       = Form(0.85),
    is_active:   bool        = Form(False),
    file:        UploadFile  = File(...),
):
    filename = f"{int(time.time())}_{file.filename}"
    dest     = os.path.join(UPLOADS_DIR, filename)
    content  = await file.read()
    with open(dest, "wb") as f:
        f.write(content)
    image_url = f"/uploads/{filename}"
    bounds    = [[sw_lat, sw_lon], [ne_lat, ne_lon]]
    plan_id   = await db_manager.insert_plan(name, floor_level, image_url, bounds, opacity, is_active)
    plan      = await db_manager.get_plans()
    return {"ok": True, "id": plan_id}


@app.post("/api/plans/{plan_id}/activate")
async def activate_plan(plan_id: int):
    plan = await db_manager.set_active_plan(plan_id)
    if not plan:
        raise HTTPException(404, "Plan nicht gefunden")
    await ws_manager.broadcast({"type": "plan_activated", "plan": plan})
    return {"ok": True, "plan": plan}


@app.delete("/api/plans/{plan_id}")
async def delete_plan(plan_id: int):
    ok = await db_manager.delete_plan(plan_id)
    return {"ok": ok}


# ── REST: Geofences ──────────────────────────────────────────────────────────

@app.get("/api/geofences")
async def get_geofences():
    return await db_manager.get_geofences()


@app.post("/api/geofences")
async def create_geofence(data: Dict[str, Any] = Body(...)):
    gf_id = await db_manager.insert_geofence(
        name           = data.get("name", "Geofence"),
        zone_type      = data.get("zone_type", "danger"),
        shape          = data.get("shape", "circle"),
        center_lat     = data.get("center_lat"),
        center_lon     = data.get("center_lon"),
        radius_m       = data.get("radius_m", 100.0),
        polygon_coords = data.get("polygon_coords"),
    )
    gf = {"id": gf_id, **data}
    await ws_manager.broadcast({"type": "geofence_added", "geofence": gf})
    return {"ok": True, "id": gf_id}


@app.delete("/api/geofences/{gf_id}")
async def delete_geofence(gf_id: int):
    ok = await db_manager.delete_geofence(gf_id)
    if ok:
        await ws_manager.broadcast({"type": "geofence_deleted", "id": gf_id})
    return {"ok": ok}


# ── REST: Timeline & Replay ───────────────────────────────────────────────────

@app.get("/api/timeline")
async def get_timeline(from_ts: Optional[str] = None, to_ts: Optional[str] = None, limit: int = 500):
    return await db_manager.get_timeline_events(from_ts, to_ts, limit)


@app.get("/api/replay/{timestamp}")
async def get_replay_state(timestamp: str):
    return await db_manager.get_state_at_timestamp(timestamp)


# ── REST: SNMP-Events ────────────────────────────────────────────────────────

@app.get("/api/snmp_events")
async def get_snmp_events(limit: int = 100):
    return await db_manager.get_snmp_events(limit)


# ── REST: Einstellungen ───────────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings():
    return {**DEFAULT_SETTINGS, **await db_manager.get_all_settings()}


@app.post("/api/settings")
async def save_settings(data: Dict[str, Any] = Body(...)):
    await db_manager.save_settings_batch(data)
    await ws_manager.broadcast({"type": "settings_updated", "settings": data})
    return {"ok": True}


@app.post("/api/settings/mock")
async def toggle_mock(data: Dict[str, Any] = Body(...)):
    enable = bool(data.get("enable", False))
    await db_manager.save_setting("mock_active", enable)
    return {"ok": True, "mock_active": enable, "note": "Neustart erforderlich für Änderung"}


@app.post("/api/settings/packet_dump")
async def toggle_packet_dump(data: Dict[str, Any] = Body(...)):
    enable = bool(data.get("enable", False))
    if _udp_manager and hasattr(_udp_manager, '_dump'):
        if enable:
            _udp_manager._dump.enable()
        else:
            _udp_manager._dump.disable()
    return {"ok": True, "enabled": enable}


@app.post("/api/system/reset-demo-data")
async def reset_demo_data(data: Dict[str, Any] = Body(default={})):
    """
    Bereinigt alle operativen Daten (PTT, SMS, GPS, Marker, Audio-Dateien).
    Setzt Funkgeräte auf die echten IDs aus ids.json zurück.
    """
    keep_ids = bool(data.get("keep_ids_json", True))
    counts = await db_manager.purge_operational_data(keep_ids_json=keep_ids)

    # Audio-Dateien bereinigen
    cleaned_audio = 0
    if os.path.exists(RECORDINGS_DIR):
        for root, dirs, files in os.walk(RECORDINGS_DIR, topdown=False):
            for name in files:
                p = os.path.join(root, name)
                try:
                    os.remove(p)
                    cleaned_audio += 1
                except Exception:
                    pass
            for d in dirs:
                dp = os.path.join(root, d)
                try:
                    os.rmdir(dp)
                except Exception:
                    pass
    counts["cleaned_audio_files"] = cleaned_audio

    # WebClients über State-Reset informieren
    radios = await db_manager.get_radios()
    await ws_manager.broadcast({
        "type": "system_reset",
        "counts": counts,
        "radios": radios,
    })
    return {"ok": True, "counts": counts}


# ── REST: Mission (Einsatz-Stammdaten) ────────────────────────────────────────

@app.get("/api/mission")
async def get_mission():
    return await db_manager.get_mission_data()


@app.post("/api/mission")
async def save_mission(data: Dict[str, Any] = Body(...)):
    result = await db_manager.save_mission_data(data)
    await ws_manager.broadcast({"type": "mission_updated", "mission": result})
    return {"ok": True, "mission": result}


# ── REST: Einsatz-Archiv ──────────────────────────────────────────────────────

@app.get("/api/missions")
async def list_missions(limit: int = 50):
    """Alle archivierten Einsätze."""
    return await db_manager.list_missions(limit)


@app.post("/api/missions/start")
async def start_mission(data: Dict[str, Any] = Body(...)):
    """
    Startet einen neuen Einsatz und verknüpft alle neuen Logs damit.
    Legt automatisch einen DB-Datensatz in missions an.
    """
    mission_data = await db_manager.get_mission_data()
    title    = data.get("title",    mission_data.get("mission_title",    "Neuer Einsatz"))
    code     = data.get("code",     mission_data.get("mission_code",     f"EINSATZ-{int(time.time())}"))
    leader   = data.get("leader",   mission_data.get("mission_leader",   ""))
    location = data.get("location", mission_data.get("mission_location", ""))
    channel  = data.get("channel",  mission_data.get("mission_channel",  ""))
    notes    = data.get("notes",    mission_data.get("mission_notes",    ""))

    mission_id = await db_manager.create_mission(title, code, leader, location, channel, notes)

    # Settings-Blob ebenfalls updaten
    await db_manager.save_mission_data({
        "mission_title":    title,
        "mission_code":     code,
        "mission_leader":   leader,
        "mission_location": location,
        "mission_channel":  channel,
        "mission_status":   "active",
        "mission_notes":    notes,
    })

    mission = await db_manager.get_mission(mission_id)
    await ws_manager.broadcast({"type": "mission_started", "mission": mission})
    return {"ok": True, "mission_id": mission_id, "mission": mission}


@app.get("/api/missions/{mission_id}")
async def get_mission_by_id(mission_id: int):
    m = await db_manager.get_mission(mission_id)
    if not m:
        raise HTTPException(404, "Einsatz nicht gefunden")
    return m


@app.post("/api/missions/{mission_id}/close")
async def close_mission(mission_id: int):
    """Schließt den Einsatz ab (setzt Status auf 'closed', berechnet Zähler)."""
    mission = await db_manager.close_mission(mission_id)
    if not mission:
        raise HTTPException(404, "Einsatz nicht gefunden")
    await ws_manager.broadcast({"type": "mission_closed", "mission": mission})
    return {"ok": True, "mission": mission}


@app.post("/api/missions/{mission_id}/archive")
async def archive_mission(mission_id: int):
    """
    Erstellt ein vollständiges ZIP-Archiv des Einsatzes.
    Enthält: mission_data.json (alle Logs, GPS, SMS, Marker, Radios) + Audiodateien.
    """
    archive_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "archives"
    )
    try:
        path = await db_manager.archive_mission(mission_id, archive_dir)
        filename = os.path.basename(path)
        return {
            "ok":           True,
            "archive_path": path,
            "download_url": f"/api/missions/{mission_id}/download",
            "filename":     filename,
        }
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.error(f"Archivierungsfehler Mission {mission_id}: {e}")
        raise HTTPException(500, f"Archivierung fehlgeschlagen: {e}")


@app.get("/api/missions/{mission_id}/download")
async def download_mission_archive(mission_id: int):
    """Lädt die ZIP-Archivdatei eines Einsatzes herunter."""
    mission = await db_manager.get_mission(mission_id)
    if not mission:
        raise HTTPException(404, "Einsatz nicht gefunden")
    path = mission.get("archive_path")
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Archivdatei nicht gefunden – zuerst /archive aufrufen")
    return FileResponse(
        path,
        filename=os.path.basename(path),
        media_type="application/zip",
    )


@app.delete("/api/missions/{mission_id}")
async def delete_mission(mission_id: int):
    """Löscht einen archivierten Einsatz aus der DB (ZIP-Datei bleibt erhalten)."""
    ok = await db_manager.delete_mission_archive(mission_id)
    return {"ok": ok}


# ── REST: Audio-Streaming ─────────────────────────────────────────────────────

@app.get("/api/audio/{ptt_id}")
async def serve_audio(ptt_id: int):
    """
    Streamt die WAV-Aufnahme eines PTT-Vorgangs.
    Unterstützt Range-Requests für HTML5 <audio>-Player.
    """
    async with db_manager.get_connection() as _db:
        cur = await _db.execute(
            "SELECT audio_url FROM ptt_logs WHERE id=?", (ptt_id,)
        )
        row = await cur.fetchone()

    if not row or not row["audio_url"]:
        raise HTTPException(404, "Keine Aufnahme für diesen PTT-Vorgang")

    filepath = row["audio_url"]
    if not os.path.isfile(filepath):
        raise HTTPException(404, f"Audiodatei nicht gefunden: {os.path.basename(filepath)}")

    return FileResponse(
        filepath,
        media_type="audio/wav",
        filename=os.path.basename(filepath),
    )


@app.get("/api/audio")
async def list_audio(limit: int = 50):
    """Gibt alle PTT-Vorgänge mit Audio-Aufnahmen zurück."""
    async with db_manager.get_connection() as _db:
        cur = await _db.execute("""
            SELECT p.id, p.timestamp, p.radio_id, p.duration_ms, p.slot, p.audio_url,
                   r.alias
            FROM ptt_logs p
            LEFT JOIN radios r ON p.radio_id = r.radio_id
            WHERE p.audio_url IS NOT NULL
            ORDER BY p.timestamp DESC
            LIMIT ?
        """, (limit,))
        rows = await cur.fetchall()
    return [
        {
            "ptt_id":      r["id"],
            "timestamp":   r["timestamp"],
            "radio_id":    r["radio_id"],
            "alias":       r["alias"] or f"Radio {r['radio_id']}",
            "duration_ms": r["duration_ms"],
            "slot":        r["slot"],
            "url":         f"/api/audio/{r['id']}",
        }
        for r in rows
    ]


@app.get("/api/audio/recorder/stats")
async def get_recorder_stats():
    """Gibt Statistiken des Audio-Recorders zurück (aktive Sessions etc.)."""
    if _audio_recorder:
        return _audio_recorder.get_stats()
    return {"active_sessions": 0, "sessions": [], "enabled": False}


@app.get("/api/audio/recorder/probe")
async def get_recorder_probe():
    """Gibt die Ergebnisse des Audio-Probe-Diagnosemodus zurück."""
    if _audio_recorder:
        return _audio_recorder.get_probe_data()
    return {"probe_active": False, "packet_count": 0, "samples": []}


@app.post("/api/audio/recorder/probe/start")
async def start_recorder_probe(max_packets: int = 50):
    """Startet die Paket-Diagnose für ankommende RTP-Streams."""
    if not _audio_recorder:
        raise HTTPException(503, "Audio-Recorder nicht aktiv")
    _audio_recorder.start_probe(max_packets=max_packets)
    return {"status": "ok", "message": f"Probe-Modus aktiv für bis zu {max_packets} Pakete"}


@app.post("/api/audio/recorder/probe/stop")
async def stop_recorder_probe():
    """Stoppt die Paket-Diagnose."""
    if not _audio_recorder:
        raise HTTPException(503, "Audio-Recorder nicht aktiv")
    _audio_recorder.stop_probe()
    return {"status": "ok", "message": "Probe-Modus gestoppt"}


# ── REST: System-Status ──────────────────────────────────────────────────────

@app.get("/api/status")
async def get_system_status():
    """System-Health-Endpoint – gibt einen Überblick über alle Subsysteme."""
    radios = await db_manager.get_radios()
    return {
        "status":      "ok",
        "version":     "1.0.0",
        "ws_clients":  ws_manager.count(),
        "radios_total": len(radios),
        "radios_online": sum(1 for r in radios if r.get("online")),
        "hardware": {
            "repeater": _snmp_monitor.get_state() if _snmp_monitor else {"online": False},
            "ups":      _ups_monitor.get_state()  if _ups_monitor  else {"online": False},
            "zte":      _zte_monitor.get_state()  if _zte_monitor  else {"online": False},
            "omada":    _omada_monitor.get_state() if _omada_monitor else {"online": False},
        },
        "uptime": time.time(),
    }


# ── REST: Hardware-Status ─────────────────────────────────────────────────────

@app.get("/api/hardware/status")
async def get_hardware_status():
    return {
        "repeater": _snmp_monitor.get_state() if _snmp_monitor else {"online": False},
        "ups":      _ups_monitor.get_state()  if _ups_monitor  else {"online": False},
        "zte":      _zte_monitor.get_state()  if _zte_monitor  else {"online": False},
        "omada":    _omada_monitor.get_state() if _omada_monitor else {"online": False},
    }


@app.post("/api/hardware/ups/poll")
async def poll_ups():
    if not _ups_monitor:
        raise HTTPException(503, "USV-Monitor nicht aktiv")
    state = await _ups_monitor.poll_once()
    return state.to_dict()


@app.get("/api/hardware/ups/scan")
async def ups_raw_scan():
    if not _ups_monitor:
        raise HTTPException(503, "USV-Monitor nicht aktiv")
    return await _ups_monitor.get_raw_scan()


@app.post("/api/hardware/zte/poll")
async def poll_zte():
    if not _zte_monitor:
        raise HTTPException(503, "ZTE-Monitor nicht aktiv")
    state = await _zte_monitor.poll_once()
    return state.to_dict() if hasattr(state, "to_dict") else vars(state)


@app.post("/api/hardware/omada/poll")
async def poll_omada():
    if not _omada_monitor:
        raise HTTPException(503, "Omada-Monitor nicht aktiv")
    state = await _omada_monitor.poll_once()
    return state.to_dict() if hasattr(state, "to_dict") else vars(state)


# ── REST: Paket-Diagnose ──────────────────────────────────────────────────────

@app.get("/api/diagnostics/packets")
async def get_packets(limit: int = 100):
    if not _udp_manager or not hasattr(_udp_manager, '_dump'):
        return []
    return _udp_manager._dump.get_buffer(limit)


@app.get("/api/diagnostics/port_stats")
async def get_port_stats():
    if not _udp_manager:
        return []
    return _udp_manager.get_port_stats()


@app.delete("/api/diagnostics/packets")
async def clear_packets():
    if _udp_manager and hasattr(_udp_manager, '_dump'):
        _udp_manager._dump.clear_buffer()
    return {"ok": True}


# ── REST: IDs-Export ─────────────────────────────────────────────────────────

@app.get("/api/export/ids_json")
async def export_ids_json():
    path = await db_manager.export_ids_json()
    return FileResponse(path, filename="ids.json", media_type="application/json")


# ── Karten-Proxy (Offline-Tiles) ─────────────────────────────────────────────

@app.get("/tiles/{z}/{x}/{y}.png")
async def get_tile(z: int, x: int, y: int):
    """
    Offline OSM-Kachel-Cache mit HTTP-Fallback.
    Kacheln werden lokal gespeichert und bei Netzausfall aus dem Cache bedient.
    """
    tile_path = os.path.join(TILES_DIR, str(z), str(x), f"{y}.png")
    if os.path.isfile(tile_path):
        return FileResponse(tile_path, media_type="image/png")

    # Online-Download
    try:
        import httpx
        url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        headers = {"User-Agent": "HyteraCommandCenter/1.0"}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                os.makedirs(os.path.dirname(tile_path), exist_ok=True)
                with open(tile_path, "wb") as f:
                    f.write(resp.content)
                return Response(resp.content, media_type="image/png")
    except Exception:
        pass

    # Fallback: transparentes PNG
    fallback = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06'
        b'\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
        b'\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    return Response(fallback, media_type="image/png")


# ── Datei-Uploads (Lagepläne) ─────────────────────────────────────────────────

@app.get("/uploads/{filename}")
async def serve_upload(filename: str):
    path = os.path.join(UPLOADS_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path)


# ── Frontend SPA ──────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    index = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    raise HTTPException(503, "Frontend nicht gebaut")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    # Statische Dateien im Frontend-Verzeichnis bedienen
    candidate = os.path.join(FRONTEND_DIR, full_path)
    if os.path.isfile(candidate):
        return FileResponse(candidate)
    # SPA-Fallback
    index = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    raise HTTPException(404)
