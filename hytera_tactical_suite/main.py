"""
Hytera Tactical Suite - FastAPI Backend Server
Zentraler Webserver für REST-APIs, WebSocket-Echtzeit-Push, Offline-Kacheln,
Notruf-Management, Multi-Floor-Verwaltung, Geofencing, Audio-Voice-Log und Einsatzbericht.
"""

import os
import io
import json
import uuid
import logging
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager
import re
import httpx

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, UploadFile, File, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field
import pypdfium2
from PIL import Image

try:
    from .config import (
        MODULE_DIR, STATIC_DIR, TEMPLATES_DIR, UPLOADS_DIR,
        DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM,
        DEFAULT_OVERLAY_BOUNDS, DEFAULT_OVERLAY_IMAGE,
        DEFAULT_UDP_PORT, TACTICAL_SYMBOLS
    )
    from .db_manager import db_manager, get_current_iso_timestamp
    from .udp_listener import HyteraUDPManager
    from .hardware_monitor import hw_monitor
    from .tile_cache import tile_cache
    from .audio_manager import audio_manager
    from .report_generator import ReportGenerator
    from .tailscale_helper import get_tailscale_status, get_local_lan_ip
except (ImportError, ValueError):
    from config import (
        MODULE_DIR, STATIC_DIR, TEMPLATES_DIR, UPLOADS_DIR,
        DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM,
        DEFAULT_OVERLAY_BOUNDS, DEFAULT_OVERLAY_IMAGE,
        DEFAULT_UDP_PORT, TACTICAL_SYMBOLS
    )
    from db_manager import db_manager, get_current_iso_timestamp
    from udp_listener import HyteraUDPManager
    from hardware_monitor import hw_monitor
    from tile_cache import tile_cache
    from audio_manager import audio_manager
    from report_generator import ReportGenerator
    from tailscale_helper import get_tailscale_status, get_local_lan_ip

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s"
)
logger = logging.getLogger("tactical_server")

# In-Memory Cache für Geocoding-Suchanfragen (Ort/Adresse/Koordinaten)
GEOCODE_CACHE: Dict[str, Any] = {}


# ── Pydantic Datenmodelle ─────────────────────────────────
class MarkerCreateRequest(BaseModel):
    lat: float
    lon: float
    typ: str = "incident"
    beschreibung: str
    prioritaet: str = "normal"
    author: Optional[str] = "Operator"
    tactical_symbol: Optional[str] = ""


class RadioUpdateRequest(BaseModel):
    radio_id: int
    alias: str
    device_model: Optional[str] = ""
    has_gps: Optional[bool] = None
    floor_level: Optional[str] = "EG"


class RadioPositionRequest(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None


class RadioGpsToggleRequest(BaseModel):
    has_gps: bool


class RadioFloorRequest(BaseModel):
    floor_level: str


class EmergencyTriggerRequest(BaseModel):
    emergency_type: Optional[str] = "MAN-DOWN / NOTRUF"


class PlanCreateRequest(BaseModel):
    name: str
    floor_level: str = "EG"
    image_url: str
    bounds: List[List[float]]
    opacity: Optional[float] = 0.85
    is_active: Optional[bool] = False


class GeofenceCreateRequest(BaseModel):
    name: str
    zone_type: str = "danger"  # danger, staging, perimeter
    shape: str = "circle"      # circle, polygon
    center_lat: Optional[float] = None
    center_lon: Optional[float] = None
    radius_m: Optional[float] = 100.0
    polygon_coords: Optional[List[List[float]]] = None


class PreloadTilesRequest(BaseModel):
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float
    min_zoom: Optional[int] = 13
    max_zoom: Optional[int] = 16


class SettingsUpdateRequest(BaseModel):
    settings: Dict[str, Any]


class MissionUpdateRequest(BaseModel):
    mission_title: Optional[str] = None
    mission_code: Optional[str] = None
    mission_leader: Optional[str] = None
    mission_location: Optional[str] = None
    mission_channel: Optional[str] = None
    mission_status: Optional[str] = None
    mission_notes: Optional[str] = None
    mission_start_time: Optional[str] = None


class OverlayConfigRequest(BaseModel):
    image_url: Optional[str] = DEFAULT_OVERLAY_IMAGE
    bounds: List[List[float]] = DEFAULT_OVERLAY_BOUNDS
    opacity: Optional[float] = 0.85
    visible: Optional[bool] = True


DEFAULT_SYSTEM_SETTINGS = {
    "gps_tracking_enabled": True,
    "show_no_gps_units": True,
    "allow_manual_positioning": True,
    "overlay_visible": True,
    "overlay_opacity": 0.85,
    "ptt_pulse_enabled": True,
    "auto_pan_ptt": False,
    "breadcrumbs_enabled": True,
    "audio_enabled": True,
    "markers_visible": True,
    "unit_labels_visible": True,
    "timeline_visible": True,
    "rssi_layer_visible": False,
    "theme": "business",
    "mock_active": False,
    "active_floor": "EG",
    "map_provider": "osm",
    "map_api_key": "",
    "map_center": [51.2277, 6.7735],
    "map_zoom": 14,
    "hw_repeater_ip": "192.168.0.230",
    "hw_repeater_trap_port": 10162,
    "hw_usv_ip": "192.168.0.232",
    "hw_usv_port": 502,
    "hw_router_ip": "192.168.0.1",
    "hw_simulation": False
}


# ── WebSocket Verbindungs-Manager ───────────────────────────
class WebSocketConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket Client verbunden. Gesamt: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket Client getrennt. Verbleibend: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        if not self.active_connections:
            return
        payload = json.dumps(message)
        dead = []
        for conn in self.active_connections:
            try:
                await conn.send_text(payload)
            except Exception:
                dead.append(conn)
        for d in dead:
            self.disconnect(d)


ws_manager = WebSocketConnectionManager()
udp_manager: Optional[HyteraUDPManager] = None
report_gen = ReportGenerator(db_manager)

current_overlay_config = {
    "image_url": DEFAULT_OVERLAY_IMAGE,
    "bounds": DEFAULT_OVERLAY_BOUNDS,
    "opacity": 0.85,
    "visible": True
}


# ── Lifespan Context Manager ──────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Starte Hytera Tactical Suite ===")
    await db_manager.init_db()

    # Gespeicherte Pläne und Overlay laden
    saved_overlay = await db_manager.get_setting("overlay_config")
    if saved_overlay and isinstance(saved_overlay, dict):
        current_overlay_config.update(saved_overlay)

    async def on_telemetry_event(event: Dict[str, Any]):
        await ws_manager.broadcast(event)

    global udp_manager
    udp_port = int(os.environ.get("HYTERA_MAP_UDP_PORT", DEFAULT_UDP_PORT))
    enable_mock = os.environ.get("HYTERA_MAP_ENABLE_MOCK", "0") == "1"

    udp_manager = HyteraUDPManager(
        event_callback=on_telemetry_event,
        db=db_manager,
        port=udp_port,
        enable_mock=enable_mock
    )
    await udp_manager.start()

    init_settings = await db_manager.get_all_settings()
    hw_monitor.update_config(init_settings)
    hw_monitor.event_callback = on_telemetry_event
    await hw_monitor.start()

    yield

    logger.info("Beende Hytera Tactical Suite...")
    await hw_monitor.stop()
    if udp_manager:
        await udp_manager.stop()
    logger.info("Hytera Tactical Suite sauber beendet.")


app = FastAPI(
    title="Hytera Tactical Suite",
    description="Autarkes Lagezentrum & Funkführungssystem für Hytera HR1065",
    version="2.0.0",
    lifespan=lifespan
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── HTTP Routen ───────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_file = os.path.join(TEMPLATES_DIR, "index.html")
    if not os.path.isfile(index_file):
        raise HTTPException(status_code=404, detail="index.html nicht gefunden!")
    return FileResponse(index_file)


@app.get("/report", response_class=HTMLResponse)
async def get_report_page():
    """Liefert die druckfähige A4-Einsatzbericht-Ansicht aus."""
    report_file = os.path.join(TEMPLATES_DIR, "report.html")
    if not os.path.isfile(report_file):
        raise HTTPException(status_code=404, detail="report.html nicht gefunden!")
    return FileResponse(report_file)


@app.get("/api/report/data")
async def get_report_data():
    """Gibt den vollständigen aggregierten Einsatzbericht als JSON zurück."""
    return await report_gen.generate_mission_report()


@app.get("/api/health")
async def health_check():
    radios = await db_manager.get_radios()
    return {
        "status": "online",
        "app": "Hytera Tactical Suite",
        "version": "2.0.0",
        "timestamp": get_current_iso_timestamp(),
        "connected_ws_clients": len(ws_manager.active_connections),
        "total_radios": len(radios),
        "mock_active": udp_manager.is_mock_active() if udp_manager else False
    }


@app.get("/api/initial-state")
async def get_initial_state():
    radios = await db_manager.get_radios()
    recent_gps = await db_manager.get_recent_gps()
    markers = await db_manager.get_markers()
    plans = await db_manager.get_plans()
    geofences = await db_manager.get_geofences()
    recent_calls = await db_manager.get_recent_calls(15)

    db_settings = await db_manager.get_all_settings()
    merged_settings = dict(DEFAULT_SYSTEM_SETTINGS)
    merged_settings.update(db_settings)
    if udp_manager:
        merged_settings["mock_active"] = udp_manager.is_mock_active()

    return {
        "timestamp": get_current_iso_timestamp(),
        "map_center": merged_settings.get("map_center") or DEFAULT_MAP_CENTER,
        "map_zoom": merged_settings.get("map_zoom") or DEFAULT_MAP_ZOOM,
        "overlay": current_overlay_config,
        "settings": merged_settings,
        "infrastructure": hw_monitor.get_all_telemetry(),
        "radios": radios,
        "recent_gps": recent_gps,
        "markers": markers,
        "plans": plans,
        "geofences": geofences,
        "recent_calls": recent_calls,
        "tactical_symbols": TACTICAL_SYMBOLS,
        "mission": await db_manager.get_mission_data(),
        "vpn": get_tailscale_status(int(os.environ.get("HYTERA_MAP_PORT", 8000))),
        "lan_ip": get_local_lan_ip(),
        "connection_info": {
            "http_port": int(os.environ.get("HYTERA_MAP_PORT", 8000)),
            "gps_udp_port": int(os.environ.get("HYTERA_MAP_UDP_PORT", DEFAULT_UDP_PORT)),
            "snmp_trap_port": 10162,
            "dmr_range": "30001 - 30014 (RRS, CC, SMS, Audio RTP)",
            "repeater_ip": merged_settings.get("hw_repeater_ip", "192.168.0.230"),
            "usv_target": f"{merged_settings.get('hw_usv_ip', '192.168.0.232')}:{merged_settings.get('hw_usv_port', 502)}",
            "router_ip": merged_settings.get("hw_router_ip", "192.168.0.1")
        }
    }


@app.get("/api/vpn/status")
async def get_vpn_status():
    """Gibt den aktuellen Tailscale VPN-Status und die externe URL zurück."""
    port = int(os.environ.get("HYTERA_MAP_PORT", 8000))
    return get_tailscale_status(port)


@app.get("/api/infrastructure")
async def get_infrastructure_telemetry():
    return hw_monitor.get_all_telemetry()


@app.get("/api/history")
async def get_history_state(timestamp: str = Query(..., description="ISO-8601 Zeitstempel für Replay")):
    return await db_manager.get_state_at_timestamp(timestamp)


@app.get("/api/timeline")
async def get_timeline_events(
    from_ts: Optional[str] = Query(None),
    to_ts: Optional[str] = Query(None),
    limit: int = Query(500)
):
    items = await db_manager.get_timeline_events(from_ts=from_ts, to_ts=to_ts, limit=limit)
    return {"items": items}


# ── Marker & Taktische Symbole (DV 102) ──
@app.get("/api/markers")
async def get_markers():
    return {"markers": await db_manager.get_markers()}


@app.post("/api/markers")
async def create_marker(req: MarkerCreateRequest):
    now_ts = get_current_iso_timestamp()
    marker_id = await db_manager.insert_marker(
        lat=req.lat,
        lon=req.lon,
        typ=req.typ,
        beschreibung=req.beschreibung,
        prioritaet=req.prioritaet,
        author=req.author or "Operator",
        tactical_symbol=req.tactical_symbol or "",
        timestamp=now_ts
    )
    marker_data = {
        "id": marker_id,
        "lat": req.lat,
        "lon": req.lon,
        "typ": req.typ,
        "beschreibung": req.beschreibung,
        "prioritaet": req.prioritaet,
        "author": req.author or "Operator",
        "tactical_symbol": req.tactical_symbol or "",
        "timestamp": now_ts
    }
    await ws_manager.broadcast({"type": "marker_created", "marker": marker_data})
    return {"status": "ok", "marker": marker_data}


@app.delete("/api/markers/{marker_id}")
async def delete_marker(marker_id: int):
    success = await db_manager.delete_marker(marker_id)
    if not success:
        raise HTTPException(status_code=404, detail="Marker nicht gefunden")
    await ws_manager.broadcast({"type": "marker_deleted", "marker_id": marker_id})
    return {"status": "ok", "deleted_id": marker_id}


# ── Funkgeräte & Notruf ──
@app.get("/api/radios")
async def get_radios():
    return {"radios": await db_manager.get_radios()}


@app.post("/api/radios")
async def update_radio(req: RadioUpdateRequest):
    await db_manager.upsert_radio(
        req.radio_id,
        req.alias,
        req.device_model or "",
        has_gps=req.has_gps,
        floor_level=req.floor_level
    )
    await db_manager.export_ids_json()
    await ws_manager.broadcast({
        "type": "radio_updated",
        "radio_id": req.radio_id,
        "alias": req.alias,
        "device_model": req.device_model,
        "has_gps": req.has_gps,
        "floor_level": req.floor_level
    })
    return {"status": "ok", "radio_id": req.radio_id}


@app.delete("/api/radios/{radio_id}")
async def delete_radio(radio_id: int):
    success = await db_manager.delete_radio(radio_id)
    if not success:
        raise HTTPException(status_code=404, detail="Funkgerät nicht gefunden")
    await db_manager.export_ids_json()
    await ws_manager.broadcast({"type": "radio_deleted", "radio_id": radio_id})
    return {"status": "ok", "deleted_id": radio_id}


@app.post("/api/radios/{radio_id}/position")
async def set_radio_position(radio_id: int, req: RadioPositionRequest):
    await db_manager.set_radio_fixed_position(radio_id, req.lat, req.lon)
    await ws_manager.broadcast({
        "type": "radio_position_updated",
        "radio_id": radio_id,
        "lat": req.lat,
        "lon": req.lon
    })
    return {"status": "ok", "radio_id": radio_id, "lat": req.lat, "lon": req.lon}


@app.post("/api/radios/{radio_id}/gps-toggle")
async def toggle_radio_gps(radio_id: int, req: RadioGpsToggleRequest):
    await db_manager.set_radio_gps(radio_id, req.has_gps)
    await ws_manager.broadcast({
        "type": "radio_updated",
        "radio_id": radio_id,
        "has_gps": req.has_gps
    })
    return {"status": "ok", "radio_id": radio_id, "has_gps": req.has_gps}


@app.post("/api/radios/{radio_id}/floor")
async def set_radio_floor(radio_id: int, req: RadioFloorRequest):
    await db_manager.set_radio_floor(radio_id, req.floor_level)
    await ws_manager.broadcast({
        "type": "radio_floor_updated",
        "radio_id": radio_id,
        "floor_level": req.floor_level
    })
    return {"status": "ok", "radio_id": radio_id, "floor_level": req.floor_level}


@app.post("/api/radios/{radio_id}/emergency")
async def trigger_emergency(radio_id: int, req: EmergencyTriggerRequest):
    """Löst einen Notruf / Totmann-Alarm für ein Funkgerät aus."""
    alert_info = await db_manager.trigger_radio_emergency(radio_id, req.emergency_type or "MAN-DOWN / NOTRUF")
    payload = {"type": "emergency_alert", **alert_info}
    await ws_manager.broadcast(payload)
    return {"status": "ok", "alert": alert_info}


@app.post("/api/radios/{radio_id}/ack-emergency")
async def ack_emergency(radio_id: int):
    """Quittiert einen aktiven Notruf."""
    res = await db_manager.ack_radio_emergency(radio_id)
    await ws_manager.broadcast({"type": "emergency_ack", "radio_id": radio_id})
    return {"status": "ok", **res}


# ── Multi-Plan & Stockwerke ──
@app.get("/api/plans")
async def get_plans():
    return {"plans": await db_manager.get_plans()}


@app.post("/api/plans")
async def create_plan(req: PlanCreateRequest):
    plan_id = await db_manager.insert_plan(
        name=req.name,
        floor_level=req.floor_level,
        image_url=req.image_url,
        bounds=req.bounds,
        opacity=req.opacity or 0.85,
        is_active=req.is_active or False
    )
    plans = await db_manager.get_plans()
    await ws_manager.broadcast({"type": "plans_updated", "plans": plans})
    return {"status": "ok", "plan_id": plan_id}


@app.post("/api/plans/{plan_id}/activate")
async def activate_plan(plan_id: int):
    active_plan = await db_manager.set_active_plan(plan_id)
    if not active_plan:
        raise HTTPException(status_code=404, detail="Plan nicht gefunden")
    plans = await db_manager.get_plans()
    await ws_manager.broadcast({
        "type": "plan_activated",
        "active_plan": active_plan,
        "plans": plans
    })
    return {"status": "ok", "active_plan": active_plan}


@app.delete("/api/plans/{plan_id}")
async def delete_plan(plan_id: int):
    success = await db_manager.delete_plan(plan_id)
    if not success:
        raise HTTPException(status_code=404, detail="Plan nicht gefunden")
    plans = await db_manager.get_plans()
    await ws_manager.broadcast({"type": "plans_updated", "plans": plans})
    return {"status": "ok", "deleted_id": plan_id}


# ── Geofencing & Gefahrenzonen ──
@app.get("/api/geofences")
async def get_geofences():
    return {"geofences": await db_manager.get_geofences()}


@app.post("/api/geofences")
async def create_geofence(req: GeofenceCreateRequest):
    gf_id = await db_manager.insert_geofence(
        name=req.name,
        zone_type=req.zone_type,
        shape=req.shape,
        center_lat=req.center_lat,
        center_lon=req.center_lon,
        radius_m=req.radius_m,
        polygon_coords=req.polygon_coords
    )
    geofences = await db_manager.get_geofences()
    await ws_manager.broadcast({"type": "geofences_updated", "geofences": geofences})
    return {"status": "ok", "geofence_id": gf_id}


@app.delete("/api/geofences/{geofence_id}")
async def delete_geofence(geofence_id: int):
    success = await db_manager.delete_geofence(geofence_id)
    if not success:
        raise HTTPException(status_code=404, detail="Geofence nicht gefunden")
    geofences = await db_manager.get_geofences()
    await ws_manager.broadcast({"type": "geofences_updated", "geofences": geofences})
    return {"status": "ok", "deleted_id": geofence_id}


# ── HF-Pegel / RSSI-Heatmap ──
@app.get("/api/rssi-coverage")
async def get_rssi_coverage(limit: int = Query(300)):
    points = await db_manager.get_rssi_coverage_points(limit=limit)
    return {"points": points}


# ── Offline-Karten-Cache Proxy ──
@app.get("/api/tiles/{z}/{x}/{y}.png")
async def get_map_tile(z: int, x: int, y: int):
    tile_bytes = tile_cache.get_tile(z, x, y)
    return Response(content=tile_bytes, media_type="image/png")


@app.post("/api/tiles/preload")
async def preload_map_tiles(req: PreloadTilesRequest):
    res = tile_cache.preload_bbox(
        min_lat=req.min_lat,
        min_lon=req.min_lon,
        max_lat=req.max_lat,
        max_lon=req.max_lon,
        min_zoom=req.min_zoom or 13,
        max_zoom=req.max_zoom or 16
    )
    return res


# ── Audio / Voice-Log ──
@app.get("/api/recent-calls")
async def get_recent_calls():
    calls = await db_manager.get_recent_calls(15)
    return {"calls": calls}


# ── Overlay & Uploads ──
@app.get("/api/overlay-config")
async def get_overlay_config():
    return current_overlay_config


@app.post("/api/overlay-config")
async def update_overlay_config(req: OverlayConfigRequest):
    global current_overlay_config
    current_overlay_config = {
        "image_url": req.image_url or DEFAULT_OVERLAY_IMAGE,
        "bounds": req.bounds,
        "opacity": req.opacity,
        "visible": req.visible
    }
    await db_manager.save_setting("overlay_config", current_overlay_config)
    await ws_manager.broadcast({"type": "overlay_updated", "overlay": current_overlay_config})
    return {"status": "ok", "overlay": current_overlay_config}


@app.post("/api/overlay/upload")
async def upload_overlay(file: UploadFile = File(...)):
    filename = file.filename or "plan"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".pdf", ".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(status_code=400, detail="Nicht unterstütztes Format.")

    content = await file.read()
    unique_name = f"overlay_{uuid.uuid4().hex[:8]}"

    if ext == ".pdf":
        try:
            pdf = pypdfium2.PdfDocument(content)
            page = pdf[0]
            pil_image = page.render(scale=2.0).to_pil()
            out_filename = f"{unique_name}.png"
            out_path = os.path.join(UPLOADS_DIR, out_filename)
            pil_image.save(out_path, format="PNG")
            width, height = pil_image.size
        except Exception as e:
            logger.error(f"PDF Konvertierungsfehler: {e}")
            raise HTTPException(status_code=500, detail=f"PDF Fehler: {e}")
    else:
        try:
            pil_image = Image.open(io.BytesIO(content))
            out_filename = f"{unique_name}{ext}"
            out_path = os.path.join(UPLOADS_DIR, out_filename)
            pil_image.save(out_path)
            width, height = pil_image.size
        except Exception as e:
            logger.error(f"Bildfehler: {e}")
            raise HTTPException(status_code=500, detail=f"Bild Fehler: {e}")

    image_url = f"/static/uploads/{out_filename}"
    return {
        "status": "ok",
        "image_url": image_url,
        "filename": out_filename,
        "original_name": filename,
        "type": "pdf" if ext == ".pdf" else "image",
        "width": width,
        "height": height
    }


# ── Einstellungen & Steuerung ──
@app.get("/api/settings")
async def get_settings():
    db_settings = await db_manager.get_all_settings()
    merged = dict(DEFAULT_SYSTEM_SETTINGS)
    merged.update(db_settings)
    if udp_manager:
        merged["mock_active"] = udp_manager.is_mock_active()
    return {"settings": merged}


@app.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest):
    for k, v in req.settings.items():
        await db_manager.save_setting(k, v)

    if "mock_active" in req.settings and udp_manager:
        if req.settings["mock_active"] != udp_manager.is_mock_active():
            await udp_manager.toggle_mock()

    all_settings = await db_manager.get_all_settings()
    merged = dict(DEFAULT_SYSTEM_SETTINGS)
    merged.update(all_settings)
    if udp_manager:
        merged["mock_active"] = udp_manager.is_mock_active()

    # Hardware-Monitor mit neuen IPs/Ports/Simulationsmodus versorgen
    hw_monitor.update_config(merged)

    await ws_manager.broadcast({"type": "settings_updated", "settings": merged})
    return {"status": "ok", "settings": merged}


# ── Geocoding- & Standort-Suche ──
@app.get("/api/geocode")
async def geocode_location(q: str = Query(..., min_length=2, description="Suchbegriff, Adresse oder Koordinate")):
    query = q.strip()
    if not query:
        return {"results": []}

    cache_key = query.lower()
    if cache_key in GEOCODE_CACHE:
        return {"results": GEOCODE_CACHE[cache_key]}

    results = []

    # 1. Direkte Koordinateneingabe (z. B. "51.2277, 6.7735" oder "51.2277 6.7735")
    coord_match = re.match(r"^([-+]?\d+(?:\.\d+)?)[,\s]+([-+]?\d+(?:\.\d+)?)$", query)
    if coord_match:
        try:
            lat = float(coord_match.group(1))
            lon = float(coord_match.group(2))
            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                coord_result = [{
                    "name": f"Koordinate: {lat:.5f}, {lon:.5f}",
                    "display_name": f"Direkte GPS-Koordinate ({lat:.5f}, {lon:.5f})",
                    "lat": lat,
                    "lon": lon,
                    "type": "coordinate"
                }]
                GEOCODE_CACHE[cache_key] = coord_result
                return {"results": coord_result}
        except ValueError:
            pass

    # 2. Lokale Datenbank-Suche in Markern, Sicherheitszonen und Funkgeräten
    try:
        markers = await db_manager.get_markers()
        for m in markers:
            title = m.get("beschreibung", "") or m.get("title", "")
            typ = m.get("typ", "") or m.get("category", "Lage")
            if query.lower() in title.lower() or query.lower() in typ.lower():
                results.append({
                    "name": f"📌 {title}",
                    "display_name": f"Taktischer Marker: {title} ({typ})",
                    "lat": float(m["lat"]),
                    "lon": float(m["lon"]),
                    "type": "marker"
                })

        geofences = await db_manager.get_geofences()
        for gf in geofences:
            name = gf.get("name", "")
            if query.lower() in name.lower():
                results.append({
                    "name": f"🛡️ {name}",
                    "display_name": f"Sicherheitszone: {name} ({gf.get('zone_type', 'Zone')})",
                    "lat": float(gf["center_lat"]),
                    "lon": float(gf["center_lon"]),
                    "type": "geofence"
                })

        radios = await db_manager.get_radios()
        for r in radios:
            alias = r.get("alias") or f"Funkgerät {r.get('radio_id')}"
            if (query.lower() in alias.lower() or query == str(r.get("radio_id"))) and r.get("last_lat") and r.get("last_lon"):
                results.append({
                    "name": f"📻 {alias}",
                    "display_name": f"Funkgerät: {alias} (DMR-ID {r.get('radio_id')})",
                    "lat": float(r["last_lat"]),
                    "lon": float(r["last_lon"]),
                    "type": "radio"
                })
    except Exception as e:
        logger.error(f"Fehler bei lokaler Objektsuche: {e}")

    # 3. Online-Geocoding: Google Geocoding (falls API-Key vorhanden) oder OSM Nominatim
    all_settings = await db_manager.get_all_settings()
    api_key = all_settings.get("map_api_key", "").strip()

    if api_key.startswith("AIza"):
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(
                    "https://maps.googleapis.com/maps/api/geocode/json",
                    params={"address": query, "key": api_key, "language": "de"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("results", [])[:5]:
                        loc = item.get("geometry", {}).get("location", {})
                        if "lat" in loc and "lng" in loc:
                            results.append({
                                "name": item.get("formatted_address", query).split(",")[0],
                                "display_name": item.get("formatted_address", query),
                                "lat": float(loc["lat"]),
                                "lon": float(loc["lng"]),
                                "type": "address"
                            })
        except Exception as e:
            logger.debug(f"Google Geocode Fallback Exception: {e}")

    # OSM Nominatim Fallback (bis max. 6 Treffer)
    if len(results) < 6:
        try:
            headers = {
                "User-Agent": "HyteraTacticalSuite/2.0 (BOS-Rescue-Coordination; mailto:support@hytera-tactical.local)"
            }
            async with httpx.AsyncClient(timeout=2.0, headers=headers) as client:
                resp = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "q": query,
                        "format": "json",
                        "limit": 6 - len(results),
                        "addressdetails": 1,
                        "accept-language": "de,en"
                    }
                )
                if resp.status_code == 200:
                    for item in resp.json():
                        results.append({
                            "name": item.get("display_name", query).split(",")[0],
                            "display_name": item.get("display_name", query),
                            "lat": float(item["lat"]),
                            "lon": float(item["lon"]),
                            "type": "address"
                        })
        except Exception as e:
            logger.debug(f"OSM Nominatim Geocode Exception: {e}")

    GEOCODE_CACHE[cache_key] = results
    return {"results": results}


@app.post("/api/mock/toggle")
async def toggle_mock():
    active = False
    if udp_manager:
        active = await udp_manager.toggle_mock()
        await db_manager.save_setting("mock_active", active)
        await ws_manager.broadcast({"type": "mock_status_updated", "active": active})
    return {"status": "ok", "mock_active": active}


# ── Einsatz-Stammdaten Endpunkte ──
@app.get("/api/mission")
async def get_mission():
    return await db_manager.get_mission_data()


@app.post("/api/mission")
async def update_mission(req: MissionUpdateRequest):
    data = req.model_dump(exclude_none=True)
    saved = await db_manager.save_mission_data(data)
    await ws_manager.broadcast({"type": "mission_updated", "mission": saved})
    return {"status": "ok", "mission": saved}


@app.post("/api/mission/close")
async def close_mission():
    saved = await db_manager.save_mission_data({"mission_status": "finished"})
    await ws_manager.broadcast({"type": "mission_updated", "mission": saved})
    return {"status": "ok", "mission": saved}


# ── WebSocket Endpunkt ──
@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("action") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong", "timestamp": get_current_iso_timestamp()}))
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.debug(f"WebSocket Exception: {e}")
        ws_manager.disconnect(websocket)
