"""
Hytera Lageplan & Tactical Timeline - FastAPI Backend & WebSockets (AGENT 2: Backend Engineer)
Zentraler Server für REST-API, WebSocket-Echtzeit-Push, statische Assets und Lifecycle-Management.
"""

import os
import json
import uuid
import io
import logging
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field
import pypdfium2
from PIL import Image

from .config import (
    MODULE_DIR, STATIC_DIR, TEMPLATES_DIR, UPLOADS_DIR,
    DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM,
    DEFAULT_OVERLAY_BOUNDS, DEFAULT_OVERLAY_IMAGE,
    DEFAULT_UDP_PORT
)
from .db_manager import DatabaseManager, db_manager, get_current_iso_timestamp
from .udp_listener import HyteraUDPManager
from .hardware_monitor import hw_monitor, HardwareMonitorManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s"
)
logger = logging.getLogger("map_server")


# ── Pydantic Datenmodelle ─────────────────────────────────
class MarkerCreateRequest(BaseModel):
    lat: float = Field(..., description="Breitengrad WGS84")
    lon: float = Field(..., description="Längengrad WGS84")
    typ: str = Field("incident", description="Typ: incident, hazard, checkpoint, medical, roadblock, base, info")
    beschreibung: str = Field(..., description="Beschreibung des Vorfalls")
    prioritaet: str = Field("normal", description="Priorität: low, normal, high, critical")
    author: Optional[str] = Field("Operator", description="Ersteller des Markers")


class RadioUpdateRequest(BaseModel):
    radio_id: int
    alias: str
    device_model: Optional[str] = ""
    has_gps: Optional[bool] = None


class RadioPositionRequest(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None


class RadioGpsToggleRequest(BaseModel):
    has_gps: bool


class SettingsUpdateRequest(BaseModel):
    settings: Dict[str, Any]


class OverlayConfigRequest(BaseModel):
    image_url: Optional[str] = DEFAULT_OVERLAY_IMAGE
    bounds: List[List[float]] = DEFAULT_OVERLAY_BOUNDS
    opacity: Optional[float] = 0.85
    visible: Optional[bool] = True
    rotation: Optional[float] = 0.0


# Standard-Systemeinstellungen & Funktions-Toggles
DEFAULT_SYSTEM_SETTINGS = {
    "gps_tracking_enabled": True,       # Globales GPS Tracking
    "show_no_gps_units": True,          # Handfunkgeräte ohne GPS anzeigen
    "allow_manual_positioning": True,   # Manuelles Platzieren auf Karte
    "overlay_visible": True,            # Lageplan-Overlay
    "overlay_opacity": 0.85,
    "ptt_pulse_enabled": True,          # PTT-Puls-Effekt auf Karte
    "auto_pan_ptt": False,              # Automatisch zu funkender Einheit schwenken
    "breadcrumbs_enabled": True,        # Bewegungsspuren
    "audio_enabled": True,              # Funkton Akustik
    "markers_visible": True,            # Vorfallsmarker auf Karte
    "unit_labels_visible": True,        # Rufzeichen-Schilder an Einheiten
    "timeline_visible": True,           # Zeitstrahl unten
    "theme": "business",                # "business" (Business Casual) oder "tactical" (Taktisch Dark)
    "mock_active": True
}


# ── WebSocket Verbindungs-Manager ───────────────────────────
class WebSocketConnectionManager:
    """Verwaltet aktive WebSocket-Clients und pusht Events in Echtzeit."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Neuer WebSocket-Client verbunden. Gesamt: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket-Client getrennt. Verbleibend: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        """Sendet ein Event als JSON an alle verbundenen WebClients."""
        if not self.active_connections:
            return

        payload = json.dumps(message)
        dead_connections = []

        for conn in self.active_connections:
            try:
                await conn.send_text(payload)
            except Exception as e:
                logger.debug(f"Fehler beim Senden an WebSocket-Client: {e}")
                dead_connections.append(conn)

        for dead in dead_connections:
            self.disconnect(dead)


ws_manager = WebSocketConnectionManager()

# Globaler UDP-Manager
udp_manager: Optional[HyteraUDPManager] = None

# Dynamische Overlay-Konfiguration
current_overlay_config = {
    "image_url": DEFAULT_OVERLAY_IMAGE,
    "bounds": DEFAULT_OVERLAY_BOUNDS,
    "opacity": 0.85,
    "visible": True
}


# ── Lifespan Context Manager ──────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialisiert Datenbank, startet UDP-Listener und beendet Ressourcen sauber."""
    logger.info("=== Starte Hytera Lageplan & Tactical Timeline Server ===")

    # 1. Datenbank initialisieren
    await db_manager.init_db()

    # Gespeichertes Overlay aus DB laden falls vorhanden
    saved_overlay = await db_manager.get_setting("overlay_config")
    if saved_overlay and isinstance(saved_overlay, dict):
        current_overlay_config.update(saved_overlay)

    # 2. Callback-Funktion für UDP- & Mock-Events definieren
    async def on_telemetry_event(event: Dict[str, Any]):
        await ws_manager.broadcast(event)

    # 3. UDP-Manager starten
    global udp_manager
    udp_port = int(os.environ.get("HYTERA_MAP_UDP_PORT", DEFAULT_UDP_PORT))
    enable_mock = os.environ.get("HYTERA_MAP_ENABLE_MOCK", "1") == "1"

    udp_manager = HyteraUDPManager(
        event_callback=on_telemetry_event,
        db=db_manager,
        port=udp_port,
        enable_mock=enable_mock
    )
    await udp_manager.start()
    hw_monitor.event_callback = on_telemetry_event
    await hw_monitor.start()

    yield

    # Herunterfahren
    logger.info("Fahre Hytera Lageplan-Server herunter...")
    await hw_monitor.stop()
    if udp_manager:
        await udp_manager.stop()
    logger.info("Server sauber beendet.")


# ── FastAPI App Erstellung ────────────────────────────────
app = FastAPI(
    title="Hytera Lageplan & Tactical Timeline",
    description="Echtzeit-Tracking und Vorfallserfassung für Hytera HR1065 DMR-Repeater",
    version="1.0.0",
    lifespan=lifespan
)

# Statische Dateien mounten
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── HTTP Routen ───────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def get_index():
    """Liefert die taktische Hauptoberfläche aus."""
    index_file = os.path.join(TEMPLATES_DIR, "index.html")
    if not os.path.isfile(index_file):
        raise HTTPException(status_code=404, detail="index.html nicht gefunden!")
    return FileResponse(index_file)


@app.get("/api/health")
async def health_check():
    """Gibt den Systemstatus, verbundene Clients und aktive Funkgeräte zurück."""
    radios = await db_manager.get_radios()
    return {
        "status": "online",
        "timestamp": get_current_iso_timestamp(),
        "connected_ws_clients": len(ws_manager.active_connections),
        "total_radios": len(radios),
        "udp_mock_active": udp_manager.enable_mock if udp_manager else False
    }


@app.get("/api/initial-state")
async def get_initial_state():
    """
    Liefert den initialen Zustand für das Frontend beim Laden der Seite:
    Funkgeräte, letzte bekannte GPS-Positionen, aktive Marker, Einstellungen und Kartenkonfiguration.
    """
    radios = await db_manager.get_radios()
    recent_gps = await db_manager.get_recent_gps()
    markers = await db_manager.get_markers()
    db_settings = await db_manager.get_all_settings()
    merged_settings = dict(DEFAULT_SYSTEM_SETTINGS)
    merged_settings.update(db_settings)
    if udp_manager:
        merged_settings["mock_active"] = udp_manager.is_mock_active()

    return {
        "timestamp": get_current_iso_timestamp(),
        "map_center": DEFAULT_MAP_CENTER,
        "map_zoom": DEFAULT_MAP_ZOOM,
        "overlay": current_overlay_config,
        "settings": merged_settings,
        "infrastructure": hw_monitor.get_all_telemetry(),
        "radios": radios,
        "recent_gps": recent_gps,
        "markers": markers
    }


@app.get("/api/infrastructure")
async def get_infrastructure_telemetry():
    """Liefert die aktuellen Telemetriedaten für Hytera Repeater, USV und ZTE Router."""
    return hw_monitor.get_all_telemetry()


@app.get("/api/history")
async def get_history_state(timestamp: str = Query(..., description="ISO-8601 Zeitstempel für Replay")):
    """
    Liefert den exakten historischen Zustand zu einem vergangenen Zeitpunkt (für den Zeitstrahl-Slider).
    """
    try:
        state = await db_manager.get_state_at_timestamp(timestamp)
        return state
    except Exception as e:
        logger.error(f"Fehler bei History-Abfrage für {timestamp}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/timeline")
async def get_timeline_events(
    from_ts: Optional[str] = Query(None, description="Startzeitpunkt ISO-8601"),
    to_ts: Optional[str] = Query(None, description="Endzeitpunkt ISO-8601"),
    limit: int = Query(500, description="Maximale Anzahl Ereignisse")
):
    """Liefert formatierte Ereignisse für die vis.js Timeline."""
    items = await db_manager.get_timeline_events(from_ts=from_ts, to_ts=to_ts, limit=limit)
    return {
        "items": items
    }


@app.get("/api/markers")
async def get_markers():
    """Gibt alle vorhandenen Einsatzmarker zurück."""
    markers = await db_manager.get_markers()
    return {"markers": markers}


@app.post("/api/markers")
async def create_marker(req: MarkerCreateRequest):
    """
    Erstellt einen neuen Einsatzmarker (z.B. per Rechtsklick auf die Karte)
    und sendet das Event sofort via WebSocket an alle verbundenen Dispatcher.
    """
    try:
        now_ts = get_current_iso_timestamp()
        marker_id = await db_manager.insert_marker(
            lat=req.lat,
            lon=req.lon,
            typ=req.typ,
            beschreibung=req.beschreibung,
            prioritaet=req.prioritaet,
            author=req.author or "Operator",
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
            "timestamp": now_ts
        }

        # Sofortiger Push an alle Frontend-Clients
        await ws_manager.broadcast({
            "type": "marker_created",
            "marker": marker_data
        })

        logger.info(f"Neuer Marker #{marker_id} ({req.typ} / {req.prioritaet}) erstellt.")
        return {"status": "ok", "marker": marker_data}

    except Exception as e:
        logger.error(f"Fehler beim Erstellen des Markers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/markers/{marker_id}")
async def delete_marker(marker_id: int):
    """Löscht einen Marker und informiert alle Clients per WebSocket."""
    success = await db_manager.delete_marker(marker_id)
    if not success:
        raise HTTPException(status_code=404, detail="Marker nicht gefunden")

    await ws_manager.broadcast({
        "type": "marker_deleted",
        "marker_id": marker_id
    })
    return {"status": "ok", "deleted_id": marker_id}


@app.get("/api/radios")
async def get_radios():
    """Gibt alle bekannten Funkgeräte zurück."""
    radios = await db_manager.get_radios()
    return {"radios": radios}


@app.post("/api/radios")
async def update_radio(req: RadioUpdateRequest):
    """Aktualisiert oder erstellt einen Funkgeräte-Alias."""
    await db_manager.upsert_radio(req.radio_id, req.alias, req.device_model or "")
    await ws_manager.broadcast({
        "type": "radio_updated",
        "radio_id": req.radio_id,
        "alias": req.alias,
        "device_model": req.device_model
    })
    return {"status": "ok", "radio_id": req.radio_id, "alias": req.alias}


@app.get("/api/overlay-config")
async def get_overlay_config():
    """Liefert die aktuelle Lageplan-Overlay-Konfiguration."""
    return current_overlay_config


@app.post("/api/overlay-config")
async def update_overlay_config(req: OverlayConfigRequest):
    """Aktualisiert die Lageplan-Overlay-Konfiguration (Eck-Koordinaten, Bild und Transparenz)."""
    global current_overlay_config
    current_overlay_config = {
        "image_url": req.image_url or DEFAULT_OVERLAY_IMAGE,
        "bounds": req.bounds,
        "opacity": req.opacity,
        "visible": req.visible
    }
    await db_manager.save_setting("overlay_config", current_overlay_config)
    await ws_manager.broadcast({
        "type": "overlay_updated",
        "overlay": current_overlay_config
    })
    return {"status": "ok", "overlay": current_overlay_config}


@app.post("/api/overlay/upload")
async def upload_overlay(file: UploadFile = File(...)):
    """Lädt ein Bild oder eine PDF hoch und bereitet es als Lageplan vor."""
    filename = file.filename or "plan"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".pdf", ".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(status_code=400, detail="Nicht unterstütztes Format. Erlaubt sind PDF, PNG, JPG, WEBP.")

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
            logger.error(f"Fehler beim Rendern der PDF: {e}")
            raise HTTPException(status_code=500, detail=f"PDF konnte nicht konvertiert werden: {e}")
    else:
        try:
            pil_image = Image.open(io.BytesIO(content))
            out_filename = f"{unique_name}{ext}"
            out_path = os.path.join(UPLOADS_DIR, out_filename)
            pil_image.save(out_path)
            width, height = pil_image.size
        except Exception as e:
            logger.error(f"Fehler beim Speichern des Bildes: {e}")
            raise HTTPException(status_code=500, detail=f"Bild konnte nicht verarbeitet werden: {e}")

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


@app.get("/api/recent-calls")
async def get_recent_calls():
    """Gibt die letzten PTT-Funksprüche für den Call-Log-Ticker zurück."""
    calls = await db_manager.get_recent_calls(15)
    return {"calls": calls}


@app.get("/api/settings")
async def get_settings():
    """Gibt alle Systemeinstellungen und Toggles zurück."""
    db_settings = await db_manager.get_all_settings()
    merged = dict(DEFAULT_SYSTEM_SETTINGS)
    merged.update(db_settings)
    if udp_manager:
        merged["mock_active"] = udp_manager.is_mock_active()
    return {"settings": merged}


@app.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest):
    """Aktualisiert Systemeinstellungen und broadcastet diese an alle Clients."""
    for k, v in req.settings.items():
        await db_manager.save_setting(k, v)

    # Wenn mock_active in Settings übergeben wurde
    if "mock_active" in req.settings and udp_manager:
        if req.settings["mock_active"] != udp_manager.is_mock_active():
            udp_manager.toggle_mock()

    all_settings = await db_manager.get_all_settings()
    merged = dict(DEFAULT_SYSTEM_SETTINGS)
    merged.update(all_settings)
    if udp_manager:
        merged["mock_active"] = udp_manager.is_mock_active()

    await ws_manager.broadcast({
        "type": "settings_updated",
        "settings": merged
    })
    return {"status": "ok", "settings": merged}


@app.post("/api/radios/{radio_id}/position")
async def set_radio_position(radio_id: int, req: RadioPositionRequest):
    """Weist einem Funkgerät ohne GPS einen festen manuellen Einsatzstandort zu."""
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
    """Schaltet die GPS-Fähigkeit eines Funkgeräts um (z.B. No-GPS Handfunkgerät)."""
    await db_manager.set_radio_gps(radio_id, req.has_gps)
    await ws_manager.broadcast({
        "type": "radio_updated",
        "radio_id": radio_id,
        "has_gps": req.has_gps
    })
    return {"status": "ok", "radio_id": radio_id, "has_gps": req.has_gps}


@app.post("/api/mock/toggle")
async def toggle_mock():
    """Startet oder pausiert den Mock-Generator zur Laufzeit."""
    active = False
    if udp_manager:
        active = udp_manager.toggle_mock()
        await db_manager.save_setting("mock_active", active)
        await ws_manager.broadcast({
            "type": "mock_status_updated",
            "active": active
        })
    return {"status": "ok", "mock_active": active}


# ── WebSocket Endpunkt ────────────────────────────────────
@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    """
    WebSocket-Endpunkt für Echtzeit-Telemetrie.
    Pusht GPS-Updates, PTT-Übertragungen und Marker-Änderungen sofort an das Frontend.
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            # Client-Nachrichten empfangen (z.B. Ping/Keepalive)
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
        logger.debug(f"WebSocket Ausnahme: {e}")
        ws_manager.disconnect(websocket)
