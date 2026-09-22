/**
 * Hytera Tactical Suite - Haupt-Frontend-Logik
 * Verwaltet Leaflet-Karte, Offline-Kacheln, WebSocket-Echtzeitkommunikation,
 * Notruf/Totmann-Alarme, Multi-Floor Lagepläne, Distanzmessung, DV 102 Symbole,
 * RSSI-Funkabdeckung, Geofencing, Audio-Voice-Log und Timeline-Replay.
 */

// Prototype Hook für Leaflet ImageOverlay: Garantiert, dass Rotation bei Reset/Zoom/Pan erhalten bleibt
if (window.L && L.ImageOverlay) {
  const origReset = L.ImageOverlay.prototype._reset;
  L.ImageOverlay.prototype._reset = function() {
    origReset.call(this);
    if (this._rotationDeg !== undefined) {
      const img = typeof this.getElement === "function" ? this.getElement() : this._image;
      if (img) {
        img.style.transformOrigin = "center center";
        img.style.rotate = `${this._rotationDeg}deg`;
      }
    }
  };
}

class TacticalApp {
  constructor() {
    this.ws = null;
    this.map = null;
    this.timeline = null;
    this.timelineDataset = null;

    // Lokaler Zustand
    this.radios = {};
    this.radioMarkers = {};
    this.radioTrails = {};
    this.incidentMarkers = {};
    this.geofenceLayers = {};
    this.rssiLayerGroup = null;
    this.baseTileLayer = null;
    this.currentMapProvider = "osm";
    this.mapApiKey = "";
    this.mapRotation = 0;
    this.trackUpActive = false;
    this.georefRotation = 0;

    this.plans = [];
    this.activePlan = null;
    this.planOverlay = null;
    this.activeFloor = "EG";

    this.geofences = [];
    this.tacticalSymbols = {};
    this.recentCalls = [];
    this.settings = {};
    this.mission = {};

    // Notruf-Status
    this.activeEmergency = null;
    this.emergencyTimerInterval = null;

    // Distanz-Messwerkzeug
    this.isMeasuring = false;
    this.measurePoints = [];
    this.measureLine = null;
    this.measureMarkers = [];

    // Georeferenzierungs-Status
    this.isCalibrating = false;
    this.georefHandles = [];
    this.georefCenterHandle = null;
    this.calibrationBounds = null;

    // Replay-Status
    this.isReplay = false;
    this.isReplayPlaying = false;
    this.replaySpeed = 5;
    this.replayTimer = null;
    this.replayTimestamp = null;

    // Audio-Synthesizer & Audio-Player
    this.audioCtx = null;
  }

  async init() {
    this.initAudioContext();
    this.initTheme();
    await this.fetchInitialState();
    this.initMap();
    this.initMapSearch();
    this.initTimeline();
    this.initUIListeners();
    this.initKeyboardShortcuts();
    this.connectWebSocket();
    this.startClock();
  }

  // ── Audio-Synthesizer & Soundeffekte ──
  initAudioContext() {
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      this.audioCtx = new AudioContext();
    } catch (e) {
      console.warn("Web Audio API nicht verfügbar:", e);
    }
  }

  playTone(freq, type, duration, gainVal = 0.2) {
    if (!this.settings.audio_enabled || !this.audioCtx) return;
    try {
      if (this.audioCtx.state === "suspended") this.audioCtx.resume();
      const osc = this.audioCtx.createOscillator();
      const gain = this.audioCtx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, this.audioCtx.currentTime);
      gain.gain.setValueAtTime(gainVal, this.audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, this.audioCtx.currentTime + duration);
      osc.connect(gain);
      gain.connect(this.audioCtx.destination);
      osc.start();
      osc.stop(this.audioCtx.currentTime + duration);
    } catch (e) {
      console.debug("Audio play error:", e);
    }
  }

  playPttStartBeep() { this.playTone(850, "sine", 0.12, 0.25); }
  playPttEndBeep() {
    this.playTone(1200, "sine", 0.08, 0.2);
    setTimeout(() => this.playTone(800, "sine", 0.08, 0.2), 90);
  }
  playEmergencySiren() {
    if (!this.audioCtx) return;
    try {
      if (this.audioCtx.state === "suspended") this.audioCtx.resume();
      const osc = this.audioCtx.createOscillator();
      const gain = this.audioCtx.createGain();
      osc.type = "sawtooth";
      gain.gain.setValueAtTime(0.35, this.audioCtx.currentTime);
      osc.frequency.setValueAtTime(600, this.audioCtx.currentTime);
      osc.frequency.linearRampToValueAtTime(1000, this.audioCtx.currentTime + 0.3);
      osc.frequency.linearRampToValueAtTime(600, this.audioCtx.currentTime + 0.6);
      osc.connect(gain);
      gain.connect(this.audioCtx.destination);
      osc.start();
      osc.stop(this.audioCtx.currentTime + 0.6);
    } catch (e) {}
  }
  playGeofenceAlertTone() {
    this.playTone(550, "triangle", 0.15, 0.3);
    setTimeout(() => this.playTone(440, "triangle", 0.2, 0.3), 150);
  }

  // ── Theme-Verwaltung ──
  initTheme() {
    const saved = localStorage.getItem("hytera_theme") || "business";
    this.setTheme(saved);
    const btn = document.getElementById("btn-theme-toggle");
    if (btn) {
      btn.addEventListener("click", () => {
        const current = document.body.classList.contains("theme-tactical") ? "tactical" : "business";
        this.setTheme(current === "business" ? "tactical" : "business");
      });
    }
  }

  setTheme(theme) {
    if (theme === "tactical") {
      document.body.classList.remove("theme-business");
      document.body.classList.add("theme-tactical");
      document.getElementById("theme-icon").textContent = "🛡️";
      document.getElementById("theme-label").textContent = "TAKTIK DARK";
    } else {
      document.body.classList.remove("theme-tactical");
      document.body.classList.add("theme-business");
      document.getElementById("theme-icon").textContent = "💼";
      document.getElementById("theme-label").textContent = "BUSINESS CASUAL";
    }
    localStorage.setItem("hytera_theme", theme);
  }

  // ── Initialer Datenabruf ──
  async fetchInitialState() {
    try {
      const resp = await fetch("/api/initial-state");
      const data = await resp.json();

      this.settings = data.settings || {};
      this.tacticalSymbols = data.tactical_symbols || {};
      this.plans = data.plans || [];
      this.geofences = data.geofences || [];
      this.recentCalls = data.recent_calls || [];
      this.initialMapCenter = data.map_center || [51.2325, 6.7800];
      this.initialMapZoom = data.map_zoom || 15;

      this.currentMapProvider = this.settings.map_provider || "osm";
      this.mapApiKey = this.settings.map_api_key || "";

      const qProv = document.getElementById("select-quick-map-provider");
      if (qProv) qProv.value = this.currentMapProvider;
      const sProv = document.getElementById("setting-map-provider");
      if (sProv) sProv.value = this.currentMapProvider;
      const sKey = document.getElementById("setting-map-api-key");
      if (sKey) sKey.value = this.mapApiKey;

      // Koordinaten & Hardware-Felder in Optionen vorbefüllen
      const center = this.settings.map_center || this.initialMapCenter;
      const sLat = document.getElementById("setting-map-lat");
      if (sLat) sLat.value = center[0];
      const sLon = document.getElementById("setting-map-lon");
      if (sLon) sLon.value = center[1];
      const sZoom = document.getElementById("setting-map-zoom");
      if (sZoom) sZoom.value = this.settings.map_zoom || this.initialMapZoom;

      const sRptIp = document.getElementById("setting-hw-repeater-ip");
      if (sRptIp) sRptIp.value = this.settings.hw_repeater_ip || "192.168.0.230";
      const sUsvIp = document.getElementById("setting-hw-usv-ip");
      if (sUsvIp) sUsvIp.value = this.settings.hw_usv_ip || "192.168.0.232";
      const sRtrIp = document.getElementById("setting-hw-router-ip");
      if (sRtrIp) sRtrIp.value = this.settings.hw_router_ip || "192.168.0.1";
      const sSim = document.getElementById("setting-hw-simulation");
      if (sSim) sSim.checked = !!this.settings.hw_simulation;

      // Funkgeräte registrieren
      if (data.radios) {
        data.radios.forEach(r => {
          this.radios[r.radio_id] = r;
          if (r.is_emergency) this.setEmergencyState(r);
        });
      }

      // Aktuelle Positionen
      if (data.recent_gps) {
        data.recent_gps.forEach(g => {
          if (this.radios[g.radio_id]) {
            Object.assign(this.radios[g.radio_id], g);
          }
        });
      }

      // Marker
      this.initialMarkers = data.markers || [];

      // Aktiven Plan für aktive Etage finden
      this.updateActivePlanForFloor(this.activeFloor);

      this.updateSidebarRadios();
      this.updateSidebarIncidents(this.initialMarkers);
      this.renderCallLog();
      this.updateInfrastructureUI(data.infrastructure);
      this.updateVpnUI(data.vpn);
      this.updateConnectionSummaryUI(data);
      this.renderPlansTable();
      this.renderGeofencesTable();
      this.populateTestEmergencyDropdown();

      // Einsatz- & Funkgeräte-Manager initialisieren
      this.mission = data.mission || {};
      this.updateMissionUI();
      this.renderRadiosTable();

    } catch (err) {
      console.error("Fehler beim Abrufen des Initialzustands:", err);
    }
  }

  // ── Leaflet Karten-Rotation & Opazität ──
  setMapRotation(deg) {
    deg = (deg % 360 + 360) % 360;
    this.mapRotation = deg;

    const mapPane = document.querySelector("#map .leaflet-map-pane");
    if (mapPane) {
      mapPane.style.transformOrigin = "center center";
      mapPane.style.transform = `rotate(${deg}deg)`;
      mapPane.style.rotate = `${deg}deg`;
    }

    const compassLabel = document.getElementById("compass-angle-label");
    const compassIcon = document.getElementById("compass-icon");
    const compassBtn = document.getElementById("btn-map-compass");
    const rotSlider = document.getElementById("map-rotation-slider");
    const rotVal = document.getElementById("map-rotation-val");

    const rounded = Math.round(deg);
    if (compassLabel) compassLabel.textContent = rounded === 0 ? "0° NORD" : `${rounded}°`;
    if (compassIcon) compassIcon.style.transform = `rotate(${-deg}deg)`;
    if (compassBtn) {
      if (rounded !== 0) compassBtn.classList.add("btn-active");
      else compassBtn.classList.remove("btn-active");
    }
    if (rotSlider) rotSlider.value = rounded;
    if (rotVal) rotVal.textContent = `${rounded}°`;

    const counterDeg = -deg;
    document.querySelectorAll("#map .radio-marker-tag, #map .t-popup, #map .incident-marker-body").forEach(el => {
      el.style.transform = `rotate(${counterDeg}deg)`;
    });
  }

  rotateMap(delta) {
    this.setMapRotation(this.mapRotation + delta);
  }

  resetMapRotation() {
    this.setMapRotation(0);
  }

  toggleTrackUp() {
    this.trackUpActive = !this.trackUpActive;
    const btn = document.getElementById("btn-trackup-toggle");
    if (btn) {
      btn.textContent = `🧭 TRACK-UP: ${this.trackUpActive ? 'AN' : 'AUS'}`;
      if (this.trackUpActive) btn.classList.add("btn-active");
      else btn.classList.remove("btn-active");
    }
  }

  updateOverlayOpacity(op) {
    op = Math.max(0.05, Math.min(1.0, op));
    if (this.settings) this.settings.overlay_opacity = op;

    const slider1 = document.getElementById("overlay-opacity-slider");
    const val1 = document.getElementById("overlay-opacity-val");
    if (slider1) slider1.value = Math.round(op * 100);
    if (val1) val1.textContent = `${Math.round(op * 100)}%`;

    const slider2 = document.getElementById("georef-opacity-slider");
    const val2 = document.getElementById("georef-opacity-val");
    if (slider2) slider2.value = Math.round(op * 100);
    if (val2) val2.textContent = `${Math.round(op * 100)}%`;

    if (this.planOverlay) this.planOverlay.setOpacity(op);
  }

  applyOverlayRotation(deg, targetLayer = null) {
    deg = (deg % 360 + 360) % 360;
    const layer = targetLayer || this.planOverlay;
    if (!layer) return;

    layer._rotationDeg = deg;
    const img = typeof layer.getElement === "function" ? layer.getElement() : layer._image;
    if (img) {
      img.style.transformOrigin = "center center";
      img.style.rotate = `${deg}deg`;
    }
  }

  // ── Leaflet Karte ──
  initMap() {
    // Erhöhe die Bewegungstoleranz für Klicks (verhindert fälschliches Dragging bei schnellem Mausklick)
    if (window.L && L.Drag) {
      L.Drag.prototype._dragThreshold = 8;
    }

    this.map = L.map("map", {
      center: this.initialMapCenter,
      zoom: this.initialMapZoom,
      zoomControl: true,
      attributionControl: false,
      tap: false,          // Verhindert das Verschlucken von Mausklicks auf Windows Touch/Trackpad-Geräten
      tapTolerance: 15,
      closePopupOnClick: true
    });

    // Basiskarte (OSM, Google Maps oder Mapbox) initialisieren
    this.switchBaseLayer(this.currentMapProvider, this.mapApiKey);

    this.rssiLayerGroup = L.layerGroup().addTo(this.map);

    // Initialen Lageplan laden
    this.renderPlanOverlay();

    // Initial Geofences zeichnen
    this.renderGeofencesOnMap();

    // Initial Funkgeräte und Marker platzieren
    Object.values(this.radios).forEach(r => this.updateRadioMarker(r));
    this.initialMarkers.forEach(m => this.addIncidentMarker(m));

    // Karten-Klick-Handler für Marker-Erfassung & Distanzmessung
    this.map.on("click", (e) => this.onMapClick(e));
    this.map.on("contextmenu", (e) => this.onMapContextMenu(e));
  }

  // ── Basiskarten-Umschaltung (OSM, Google Maps, Mapbox) ──
  switchBaseLayer(provider, apiKey = "") {
    this.currentMapProvider = provider || "osm";
    if (apiKey !== undefined && apiKey !== "") this.mapApiKey = apiKey;

    if (this.baseTileLayer) {
      this.map.removeLayer(this.baseTileLayer);
      this.baseTileLayer = null;
    }

    const keyParam = this.mapApiKey ? `&key=${encodeURIComponent(this.mapApiKey)}` : "";
    let tileUrl = "/api/tiles/{z}/{x}/{y}.png";
    let options = {
      maxZoom: 20,
      minZoom: 2,
      crossOrigin: true
    };

    if (this.currentMapProvider === "google_roadmap") {
      tileUrl = `https://mt{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}${keyParam}`;
      options.subdomains = ["0", "1", "2", "3"];
    } else if (this.currentMapProvider === "google_satellite") {
      tileUrl = `https://mt{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}${keyParam}`;
      options.subdomains = ["0", "1", "2", "3"];
    } else if (this.currentMapProvider === "google_hybrid") {
      tileUrl = `https://mt{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}${keyParam}`;
      options.subdomains = ["0", "1", "2", "3"];
    } else if (this.currentMapProvider === "google_terrain") {
      tileUrl = `https://mt{s}.google.com/vt/lyrs=p&x={x}&y={y}&z={z}${keyParam}`;
      options.subdomains = ["0", "1", "2", "3"];
    } else if (this.currentMapProvider === "mapbox") {
      const token = this.mapApiKey || "pk.eyJ1IjoibWFwYm94IiwiYSI6ImNpejY4NXVycTA2emYycXBndHRqcmZ3N3gifQ.rJcFIG214AriISLbB6B5aw";
      tileUrl = `https://api.mapbox.com/styles/v1/mapbox/satellite-streets-v12/tiles/{z}/{x}/{y}?access_token=${token}`;
      options.tileSize = 512;
      options.zoomOffset = -1;
    }

    this.baseTileLayer = L.tileLayer(tileUrl, options).addTo(this.map);

    // Sync Dropdowns
    const qProv = document.getElementById("select-quick-map-provider");
    if (qProv) qProv.value = this.currentMapProvider;
    const sProv = document.getElementById("setting-map-provider");
    if (sProv) sProv.value = this.currentMapProvider;
    const sKey = document.getElementById("setting-map-api-key");
    if (sKey && sKey.value !== this.mapApiKey) sKey.value = this.mapApiKey;
  }

  // ── Multi-Plan & Stockwerks-Steuerung ──
  updateActivePlanForFloor(floor) {
    this.activeFloor = floor;
    const matching = this.plans.filter(p => p.floor_level === floor);
    this.activePlan = matching.find(p => p.is_active) || matching[0] || null;
    this.renderPlanOverlay();
    this.updateFloorFiltering();
  }

  renderPlanOverlay() {
    if (this.planOverlay) {
      this.map.removeLayer(this.planOverlay);
      this.planOverlay = null;
    }

    if (!this.settings.overlay_visible || !this.activePlan || !this.activePlan.bounds) {
      return;
    }

    try {
      this.planOverlay = L.imageOverlay(this.activePlan.image_url, this.activePlan.bounds, {
        opacity: this.activePlan.opacity || this.settings.overlay_opacity || 0.85,
        interactive: false
      }).addTo(this.map);
    } catch (e) {
      console.warn("Fehler beim Anzeigen des Lageplan-Overlays:", e);
    }
  }

  updateFloorFiltering() {
    // Funkgeräte je nach Stockwerk hervorheben / dimmen
    Object.values(this.radios).forEach(r => {
      const marker = this.radioMarkers[r.radio_id];
      if (marker && marker._icon) {
        const isCurrentFloor = (r.floor_level || "EG") === this.activeFloor || this.activeFloor === "Außen";
        marker._icon.style.opacity = isCurrentFloor ? "1.0" : "0.35";
      }
    });
  }

  updateRadioVisibility() {
    const showGps = this.settings.show_gps_radios !== false;
    const showNoGps = this.settings.show_nogps_radios !== false;
    Object.values(this.radios).forEach(r => {
      const marker = this.radioMarkers[r.radio_id];
      if (marker) {
        const visible = (r.has_gps && showGps) || (!r.has_gps && showNoGps);
        if (visible) {
          if (!this.map.hasLayer(marker)) marker.addTo(this.map);
        } else {
          if (this.map.hasLayer(marker)) this.map.removeLayer(marker);
        }
      }
    });
    this.updateSidebarRadios();
  }

  // ── Taktische Zeichen & Vorfälle (DV 102) ──
  addIncidentMarker(markerData) {
    if (this.incidentMarkers[markerData.id]) return;

    let iconHtml = "";
    if (markerData.tactical_symbol && this.tacticalSymbols[markerData.tactical_symbol]) {
      const sym = this.tacticalSymbols[markerData.tactical_symbol];
      iconHtml = `
        <div class="tactical-marker-box">
          <img src="/static/assets/tactical_symbols/${sym.file}" style="width:36px;height:36px;filter:drop-shadow(0 2px 4px rgba(0,0,0,0.5));">
        </div>
      `;
    } else {
      const colors = { critical: "#dc2626", high: "#ea580c", normal: "#2563eb", low: "#64748b" };
      const c = colors[markerData.prioritaet] || "#2563eb";
      iconHtml = `
        <div style="background:${c};color:#fff;width:28px;height:28px;border-radius:50%;border:2px solid #fff;display:flex;align-items:center;justify-content:center;font-size:14px;box-shadow:0 2px 6px rgba(0,0,0,0.4);">
          📍
        </div>
      `;
    }

    const customIcon = L.divIcon({
      className: "custom-incident-icon",
      html: iconHtml,
      iconSize: [36, 36],
      iconAnchor: [18, 18]
    });

    const marker = L.marker([markerData.lat, markerData.lon], { icon: customIcon }).addTo(this.map);

    const popupHtml = `
      <div style="min-width:200px;font-family:var(--font-main);">
        <div style="font-weight:800;font-size:13px;margin-bottom:4px;color:var(--accent-primary);">
          ${markerData.tactical_symbol ? `[${markerData.tactical_symbol.toUpperCase()}] ` : ''}${markerData.typ.toUpperCase()}
        </div>
        <div style="font-size:12px;margin-bottom:6px;">${markerData.beschreibung}</div>
        <div style="font-size:11px;color:#64748b;margin-bottom:8px;">Priorität: <strong style="color:#dc2626;">${markerData.prioritaet.toUpperCase()}</strong> | von ${markerData.author || 'Operator'}</div>
        <button onclick="window.app.deleteIncidentMarker(${markerData.id})" style="background:#dc2626;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:11px;font-weight:700;">Löschen</button>
      </div>
    `;
    marker.bindPopup(popupHtml);
    this.incidentMarkers[markerData.id] = marker;
  }

  async deleteIncidentMarker(id) {
    try {
      await fetch(`/api/markers/${id}`, { method: "DELETE" });
      if (this.incidentMarkers[id]) {
        this.map.removeLayer(this.incidentMarkers[id]);
        delete this.incidentMarkers[id];
      }
    } catch (e) {
      console.error("Fehler beim Löschen des Markers:", e);
    }
  }

  // ── Funkgeräte-Marker & RSSI-Pegel ──
  updateRadioMarker(radio) {
    const rid = radio.radio_id;
    let lat = radio.lat;
    let lon = radio.lon;

    if (!radio.has_gps) {
      lat = radio.fixed_lat;
      lon = radio.fixed_lon;
    }

    if (lat === null || lat === undefined || lon === null || lon === undefined) {
      return;
    }

    const isTx = radio.is_transmitting;
    const isEmergency = radio.is_emergency;

    // RSSI Farbe bestimmen
    let rssiColorClass = "rssi-good";
    if (radio.rssi) {
      if (radio.rssi < -100) rssiColorClass = "rssi-bad";
      else if (radio.rssi < -85) rssiColorClass = "rssi-medium";
    }

    let markerHtml = `
      <div class="radio-marker-wrap" id="marker-wrap-${rid}">
        <div class="radio-marker-icon ${isTx ? 'tx-active' : ''}">
          📻
          ${isEmergency ? '<div class="marker-emergency-pulse"></div>' : ''}
          <div id="decay-ring-${rid}" class="ptt-decay-ring" style="display:none;"></div>
        </div>
        <div class="radio-marker-label">
          ${radio.alias || ('Radio ' + rid)}
          ${radio.floor_level ? ` [${radio.floor_level}]` : ''}
          ${radio.rssi ? ` <span class="${rssiColorClass}">${Math.round(radio.rssi)}dBm</span>` : ''}
        </div>
      </div>
    `;

    const icon = L.divIcon({
      className: "custom-radio-icon",
      html: markerHtml,
      iconSize: [40, 50],
      iconAnchor: [20, 25]
    });

    if (this.radioMarkers[rid]) {
      this.radioMarkers[rid].setLatLng([lat, lon]);
      this.radioMarkers[rid].setIcon(icon);
    } else {
      const marker = L.marker([lat, lon], {
        icon: icon,
        draggable: !radio.has_gps && this.settings.allow_manual_positioning
      }).addTo(this.map);

      marker.on("dragend", async (ev) => {
        const pos = ev.target.getLatLng();
        await fetch(`/api/radios/${rid}/position`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lat: pos.lat, lon: pos.lng })
        });
      });

      this.radioMarkers[rid] = marker;
    }

    // Popup mit Stockwerk-Zuweisung
    const popupHtml = `
      <div style="min-width:210px;font-family:var(--font-main);">
        <div style="font-weight:800;font-size:14px;color:var(--accent-primary);margin-bottom:4px;">
          ${radio.alias} (ID: ${rid})
        </div>
        <div style="font-size:11px;color:#64748b;margin-bottom:6px;">Modell: ${radio.device_model || 'DMR Funkgerät'}</div>
        <div style="font-size:12px;margin-bottom:8px;">
          Signal (RSSI): <strong class="${rssiColorClass}">${radio.rssi ? radio.rssi + ' dBm' : 'Unbekannt'}</strong>
        </div>
        <div style="margin-bottom:8px;">
          <label style="font-size:11px;font-weight:700;">Stockwerk zuweisen:</label>
          <select onchange="window.app.setRadioFloor(${rid}, this.value)" style="width:100%;padding:4px;font-size:12px;margin-top:2px;">
            <option value="Außen" ${radio.floor_level === 'Außen' ? 'selected' : ''}>Außengelände</option>
            <option value="UG" ${radio.floor_level === 'UG' ? 'selected' : ''}>UG (Untergeschoss)</option>
            <option value="EG" ${radio.floor_level === 'EG' || !radio.floor_level ? 'selected' : ''}>EG (Erdgeschoss)</option>
            <option value="1. OG" ${radio.floor_level === '1. OG' ? 'selected' : ''}>1. OG</option>
            <option value="2. OG" ${radio.floor_level === '2. OG' ? 'selected' : ''}>2. OG</option>
          </select>
        </div>
        <div style="display:flex;gap:6px;">
          <button onclick="window.app.toggleRadioGps(${rid}, ${!radio.has_gps})" style="flex:1;background:#0284c7;color:#fff;border:none;padding:4px;border-radius:4px;font-size:11px;cursor:pointer;">
            ${radio.has_gps ? 'Kein GPS setzen' : 'GPS aktivieren'}
          </button>
          <button onclick="window.app.triggerTestEmergency(${rid})" style="background:#dc2626;color:#fff;border:none;padding:4px 6px;border-radius:4px;font-size:11px;cursor:pointer;">
            🚨 Notruf
          </button>
        </div>
      </div>
    `;
    this.radioMarkers[rid].bindPopup(popupHtml);

    // Spuren (Breadcrumbs)
    if (this.settings.breadcrumbs_enabled && radio.has_gps) {
      if (!this.radioTrails[rid]) {
        this.radioTrails[rid] = L.polyline([], {
          color: isEmergency ? "#dc2626" : (radio.rssi && radio.rssi < -95 ? "#eab308" : "#2563eb"),
          weight: 3,
          opacity: 0.7,
          interactive: false
        }).addTo(this.map);
      }
      this.radioTrails[rid].addLatLng([lat, lon]);
    }
  }

  async setRadioFloor(radioId, floor) {
    try {
      await fetch(`/api/radios/${radioId}/floor`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ floor_level: floor })
      });
      if (this.radios[radioId]) this.radios[radioId].floor_level = floor;
      this.updateFloorFiltering();
    } catch (e) {
      console.error("Fehler beim Zuweisen des Stockwerks:", e);
    }
  }

  async toggleRadioGps(radioId, hasGps) {
    try {
      await fetch(`/api/radios/${radioId}/gps-toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ has_gps: hasGps })
      });
    } catch (e) {
      console.error("Fehler beim Umschalten des GPS-Status:", e);
    }
  }

  // ── Notruf & Totmann Management ──
  setEmergencyState(radio) {
    this.activeEmergency = radio;
    const banner = document.getElementById("emergency-banner");
    banner.classList.remove("emergency-banner-hidden");
    document.getElementById("emergency-unit-text").textContent = radio.alias;
    document.getElementById("emergency-type-badge").textContent = radio.emergency_type || "MAN-DOWN / NOTRUF";

    // Sirene & Ton
    this.playEmergencySiren();

    // Timer starten
    if (this.emergencyTimerInterval) clearInterval(this.emergencyTimerInterval);
    const startMs = radio.emergency_since ? new Date(radio.emergency_since).getTime() : Date.now();
    this.emergencyTimerInterval = setInterval(() => {
      const diffSec = Math.floor((Date.now() - startMs) / 1000);
      const m = String(Math.floor(diffSec / 60)).padStart(2, "0");
      const s = String(diffSec % 60).padStart(2, "0");
      document.getElementById("emergency-timer-val").textContent = `${m}:${s} min`;
    }, 1000);

    // Auto-Focus auf Karte
    if (this.radioMarkers[radio.radio_id]) {
      this.map.setView(this.radioMarkers[radio.radio_id].getLatLng(), 17, { animate: true });
    }
  }

  clearEmergencyState() {
    this.activeEmergency = null;
    const banner = document.getElementById("emergency-banner");
    banner.classList.add("emergency-banner-hidden");
    if (this.emergencyTimerInterval) {
      clearInterval(this.emergencyTimerInterval);
      this.emergencyTimerInterval = null;
    }
  }

  async ackEmergency() {
    if (!this.activeEmergency) return;
    try {
      await fetch(`/api/radios/${this.activeEmergency.radio_id}/ack-emergency`, { method: "POST" });
      this.clearEmergencyState();
    } catch (e) {
      console.error("Fehler beim Quittieren des Notrufs:", e);
    }
  }

  async triggerTestEmergency(rid) {
    try {
      await fetch(`/api/radios/${rid}/emergency`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ emergency_type: "MAN-DOWN TESTALARM" })
      });
    } catch (e) {
      console.error("Fehler beim Auslösen des Notrufs:", e);
    }
  }

  // ── Geofencing & Sicherheitszonen ──
  renderGeofencesOnMap() {
    Object.values(this.geofenceLayers).forEach(l => this.map.removeLayer(l));
    this.geofenceLayers = {};

    this.geofences.forEach(gf => {
      let layer = null;
      const colors = { danger: "#dc2626", staging: "#2563eb", perimeter: "#d97706" };
      const col = colors[gf.zone_type] || "#dc2626";

      if (gf.shape === "circle" && gf.center_lat && gf.center_lon) {
        layer = L.circle([gf.center_lat, gf.center_lon], {
          radius: gf.radius_m || 100,
          color: col,
          fillColor: col,
          fillOpacity: 0.18,
          weight: 2,
          interactive: false
        }).addTo(this.map);
        layer.bindTooltip(`🛡️ ${gf.name} (${gf.zone_type.toUpperCase()})`, { permanent: true, direction: "center" });
      }
      if (layer) this.geofenceLayers[gf.id] = layer;
    });
  }

  showGeofenceAlert(alert) {
    const banner = document.getElementById("geofence-banner");
    banner.classList.remove("geofence-banner-hidden");
    document.getElementById("geofence-banner-text").textContent =
      `⚠️ GEOFENCE-ALARM: ${alert.alias} hat Zone '${alert.geofence_name}' (${alert.zone_type.toUpperCase()}) betreten!`;
    this.playGeofenceAlertTone();
  }

  // ── Distanzmessung (Lineal) ──
  toggleMeasureTool() {
    this.isMeasuring = !this.isMeasuring;
    const dock = document.getElementById("measure-dock");
    const btn = document.getElementById("btn-measure-tool");

    if (this.isMeasuring) {
      dock.classList.remove("measure-dock-hidden");
      btn.classList.add("btn-active");
      this.resetMeasurement();
    } else {
      dock.classList.add("measure-dock-hidden");
      btn.classList.remove("btn-active");
      this.resetMeasurement();
    }
  }

  onMeasureClick(latlng) {
    this.measurePoints.push(latlng);
    const m = L.circleMarker(latlng, { radius: 5, color: "#0284c7", fillColor: "#38bdf8", fillOpacity: 0.9, interactive: false }).addTo(this.map);
    this.measureMarkers.push(m);

    if (this.measurePoints.length > 1) {
      if (!this.measureLine) {
        this.measureLine = L.polyline(this.measurePoints, { color: "#0284c7", weight: 3, dashArray: "5, 5", interactive: false }).addTo(this.map);
      } else {
        this.measureLine.setLatLngs(this.measurePoints);
      }
    }

    // Distanz summieren
    let totalDist = 0;
    for (let i = 1; i < this.measurePoints.length; i++) {
      totalDist += this.measurePoints[i - 1].distanceTo(this.measurePoints[i]);
    }

    const distText = totalDist >= 1000 ? `${(totalDist / 1000).toFixed(2)} km` : `${Math.round(totalDist)} m`;
    document.getElementById("measure-distance-text").textContent = distText;
    document.getElementById("measure-points-count").textContent = this.measurePoints.length;
  }

  resetMeasurement() {
    this.measurePoints = [];
    if (this.measureLine) {
      this.map.removeLayer(this.measureLine);
      this.measureLine = null;
    }
    this.measureMarkers.forEach(m => this.map.removeLayer(m));
    this.measureMarkers = [];
    document.getElementById("measure-distance-text").textContent = "0 m";
    document.getElementById("measure-points-count").textContent = "0";
  }

  // ── HF-Pegel / RSSI Heatmap Layer ──
  async toggleRssiLayer() {
    this.settings.rssi_layer_visible = !this.settings.rssi_layer_visible;
    const btn = document.getElementById("btn-rssi-toggle");
    if (btn) btn.textContent = `📶 HF-PEGEL / HEATMAP: ${this.settings.rssi_layer_visible ? 'AN' : 'AUS'}`;

    if (!this.settings.rssi_layer_visible) {
      this.rssiLayerGroup.clearLayers();
      return;
    }

    try {
      const resp = await fetch("/api/rssi-coverage");
      const data = await resp.json();
      this.rssiLayerGroup.clearLayers();

      data.points.forEach(pt => {
        const c = pt.rssi > -85 ? "#22c55e" : (pt.rssi > -100 ? "#eab308" : "#ef4444");
        L.circle([pt.lat, pt.lon], {
          radius: 25,
          color: c,
          fillColor: c,
          fillOpacity: 0.35,
          weight: 1,
          interactive: false
        }).addTo(this.rssiLayerGroup);
      });
    } catch (e) {
      console.error("Fehler beim Laden der RSSI Heatmap:", e);
    }
  }

  // ── Call-Log Ticker & Audio Wiedergabe ──
  addCallLogEntry(call) {
    this.recentCalls.unshift(call);
    if (this.recentCalls.length > 20) this.recentCalls.pop();
    this.renderCallLog();
  }

  renderCallLog() {
    const list = document.getElementById("call-log-list");
    if (!list) return;

    if (!this.recentCalls || this.recentCalls.length === 0) {
      list.innerHTML = '<div class="call-log-empty">Warte auf Funkverkehr...</div>';
      return;
    }

    list.innerHTML = this.recentCalls.slice(0, 10).map(c => `
      <div class="call-log-entry" onclick="window.app.focusRadio(${c.radio_id})">
        <div class="call-log-left">
          <span style="font-size:14px;">🎙️</span>
          <div>
            <div class="call-log-alias">${c.alias || ('Radio ' + c.radio_id)}</div>
            <div class="call-log-meta">${c.slot} | ${(c.duration_ms / 1000).toFixed(1)}s | ${new Date(c.timestamp).toLocaleTimeString()}</div>
          </div>
        </div>
        <div class="call-log-right">
          ${c.rssi ? `<span style="font-size:10px;font-weight:700;color:#94a3b8;">${Math.round(c.rssi)}dBm</span>` : ''}
          ${c.audio_url ? `
            <button class="btn-play-audio" onclick="event.stopPropagation(); window.app.playAudio('${c.audio_url}', '${c.alias || c.radio_id}')" title="Funkspruch abspielen">
              ▶️
            </button>
          ` : ''}
        </div>
      </div>
    `).join("");
  }

  playAudio(audioUrl, title) {
    const player = document.getElementById("mini-audio-player");
    const audioEl = document.getElementById("global-audio-element");
    const titleEl = document.getElementById("mini-player-title");

    titleEl.textContent = title || "Funkspruch";
    audioEl.src = audioUrl;
    player.classList.remove("mini-player-hidden");
    audioEl.play().catch(e => console.warn("Audio play blocked:", e));
  }

  // ── Offline Kachel-Cache Preload ──
  async preloadCurrentViewTiles() {
    const bounds = this.map.getBounds();
    const btn = document.getElementById("btn-preload-tiles");
    const origText = btn.textContent;
    btn.textContent = "⏳ Caching läuft...";

    try {
      const resp = await fetch("/api/tiles/preload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          min_lat: bounds.getSouth(),
          min_lon: bounds.getWest(),
          max_lat: bounds.getNorth(),
          max_lon: bounds.getEast(),
          min_zoom: 13,
          max_zoom: 16
        })
      });
      const res = await resp.json();
      btn.textContent = `✅ ${res.newly_cached} Kacheln gecacht`;
      setTimeout(() => { btn.textContent = origText; }, 3000);
    } catch (e) {
      btn.textContent = "❌ Fehler beim Cachen";
      setTimeout(() => { btn.textContent = origText; }, 3000);
    }
  }

  // ── UI Interaktionen & Modals ──
  initUIListeners() {
    // Dropdown Ebenen & Filter
    const btnLayers = document.getElementById("btn-dropdown-layers");
    const menuLayers = document.getElementById("dropdown-layers-menu");
    if (btnLayers && menuLayers) {
      btnLayers.addEventListener("click", (e) => {
        e.stopPropagation();
        menuLayers.classList.toggle("dropdown-menu-hidden");
      });
      document.addEventListener("click", () => menuLayers.classList.add("dropdown-menu-hidden"));
      menuLayers.addEventListener("click", (e) => e.stopPropagation());
    }

    // Stockwerk Buttons
    document.querySelectorAll(".btn-floor").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".btn-floor").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        this.updateActivePlanForFloor(btn.dataset.floor);
      });
    });

    // Distanz-Messwerkzeug
    document.getElementById("btn-measure-tool").addEventListener("click", () => this.toggleMeasureTool());
    document.getElementById("btn-measure-reset").addEventListener("click", () => this.resetMeasurement());
    document.getElementById("btn-measure-finish").addEventListener("click", () => this.toggleMeasureTool());

    // Zentrieren & Live
    document.getElementById("btn-recenter").addEventListener("click", () => {
      this.map.setView(this.initialMapCenter, this.initialMapZoom, { animate: true });
    });
    document.getElementById("btn-live-toggle").addEventListener("click", () => this.returnToLive());

    // Notruf Buttons
    document.getElementById("btn-ack-emergency").addEventListener("click", () => this.ackEmergency());
    document.getElementById("btn-focus-emergency").addEventListener("click", () => {
      if (this.activeEmergency && this.radioMarkers[this.activeEmergency.radio_id]) {
        this.map.setView(this.radioMarkers[this.activeEmergency.radio_id].getLatLng(), 17, { animate: true });
      }
    });
    document.getElementById("btn-geofence-dismiss").addEventListener("click", () => {
      document.getElementById("geofence-banner").classList.add("geofence-banner-hidden");
    });

    // Modals öffnen
    document.getElementById("btn-add-marker-mode").addEventListener("click", () => this.startMarkerMode());
    document.getElementById("btn-open-georef").addEventListener("click", () => this.openGeorefModal());
    document.getElementById("btn-open-geofence").addEventListener("click", () => this.openGeofenceModal());
    document.getElementById("btn-open-settings")?.addEventListener("click", () => this.openSettingsModal());
    document.getElementById("btn-open-infra")?.addEventListener("click", () => this.openInfraModal());
    document.getElementById("btn-dropdown-infra")?.addEventListener("click", () => {
      this.toggleDropdownLayers(false);
      this.openInfraModal();
    });
    document.getElementById("btn-dropdown-settings")?.addEventListener("click", () => {
      this.toggleDropdownLayers(false);
      this.openSettingsModal();
    });
    document.getElementById("pill-repeater")?.addEventListener("click", () => this.openInfraModal());
    document.getElementById("pill-usv")?.addEventListener("click", () => this.openInfraModal());
    document.getElementById("pill-router")?.addEventListener("click", () => this.openInfraModal());
    document.getElementById("pill-vpn")?.addEventListener("click", () => this.openInfraModal());
    document.getElementById("btn-copy-vpn-url")?.addEventListener("click", () => this.copyVpnUrl());

    // Toggles im Ebenen-Menü
    document.getElementById("btn-overlay-toggle")?.addEventListener("click", () => this.toggleOverlay());
    document.getElementById("btn-map-compass")?.addEventListener("click", () => this.resetMapRotation());
    document.getElementById("btn-rotate-left")?.addEventListener("click", () => this.rotateMap(-15));
    document.getElementById("btn-rotate-right")?.addEventListener("click", () => this.rotateMap(15));
    document.getElementById("btn-reset-rotation")?.addEventListener("click", () => this.resetMapRotation());
    document.getElementById("btn-trackup-toggle")?.addEventListener("click", () => this.toggleTrackUp());

    const rotSlider = document.getElementById("map-rotation-slider");
    rotSlider?.addEventListener("input", (e) => {
      this.setMapRotation(parseInt(e.target.value, 10));
    });

    const georefRotSlider = document.getElementById("georef-rotation-slider");
    const georefRotVal = document.getElementById("georef-rotation-val");

    const setGeorefRot = (val) => {
      val = (val % 360 + 360) % 360;
      this.georefRotation = val;
      if (georefRotSlider) georefRotSlider.value = Math.round(val);
      if (georefRotVal) georefRotVal.textContent = `${Math.round(val)}°`;
      this.applyOverlayRotation(val);
    };

    georefRotSlider?.addEventListener("input", (e) => {
      setGeorefRot(parseInt(e.target.value, 10));
    });

    document.getElementById("btn-georef-rotate-cw")?.addEventListener("click", () => {
      setGeorefRot((this.georefRotation || 0) + 90);
    });

    document.getElementById("btn-rssi-toggle")?.addEventListener("click", () => this.toggleRssiLayer());
    document.getElementById("btn-breadcrumbs-toggle")?.addEventListener("click", () => this.toggleBreadcrumbs());
    document.getElementById("btn-markers-toggle")?.addEventListener("click", () => this.toggleMarkers());
    document.getElementById("btn-audio-toggle")?.addEventListener("click", () => this.toggleAudio());
    document.getElementById("btn-preload-tiles")?.addEventListener("click", () => this.preloadCurrentViewTiles());

    // Funkgeräte Filter-Toggles
    const btnGps = document.getElementById("btn-gps-tracking-toggle");
    if (btnGps) {
      btnGps.addEventListener("click", () => {
        this.settings.show_gps_radios = !this.settings.show_gps_radios;
        btnGps.textContent = `🛰️ GPS-TRACKING: ${this.settings.show_gps_radios ? 'AN' : 'AUS'}`;
        btnGps.classList.toggle("btn-active", this.settings.show_gps_radios);
        this.updateRadioVisibility();
      });
    }

    const btnNoGps = document.getElementById("btn-nogps-toggle");
    if (btnNoGps) {
      btnNoGps.addEventListener("click", () => {
        this.settings.show_nogps_radios = !this.settings.show_nogps_radios;
        btnNoGps.textContent = `📻 GERÄTE OHNE GPS: ${this.settings.show_nogps_radios ? 'AN' : 'AUS'}`;
        btnNoGps.classList.toggle("btn-active", this.settings.show_nogps_radios);
        this.updateRadioVisibility();
      });
    }

    const btnPtt = document.getElementById("btn-ptt-pulse-toggle");
    if (btnPtt) {
      btnPtt.addEventListener("click", () => {
        this.settings.ptt_pulse_enabled = !this.settings.ptt_pulse_enabled;
        btnPtt.textContent = `💥 PTT-PULS: ${this.settings.ptt_pulse_enabled ? 'AN' : 'AUS'}`;
        btnPtt.classList.toggle("btn-active", this.settings.ptt_pulse_enabled);
      });
    }

    const btnAutopan = document.getElementById("btn-autopan-toggle");
    if (btnAutopan) {
      btnAutopan.addEventListener("click", () => {
        this.settings.autopan_active = !this.settings.autopan_active;
        btnAutopan.textContent = `🎥 AUTO-PAN: ${this.settings.autopan_active ? 'AN' : 'AUS'}`;
        btnAutopan.classList.toggle("btn-active", this.settings.autopan_active);
      });
    }

    // Schneller Basiskarten-Wechsel im Ebenen-Menü
    const quickProviderSelect = document.getElementById("select-quick-map-provider");
    if (quickProviderSelect) {
      quickProviderSelect.addEventListener("change", async (e) => {
        const prov = e.target.value;
        this.switchBaseLayer(prov, this.mapApiKey);
        await fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ settings: { map_provider: prov } })
        });
      });
    }

    // Speichern aller Einstellungen in Optionen-Modal (Kartenanbieter, Einsatzgebiet, Hardware)
    const btnSaveMap = document.getElementById("btn-save-map-settings");
    if (btnSaveMap) {
      btnSaveMap.addEventListener("click", async () => {
        const prov = document.getElementById("setting-map-provider").value;
        const key = (document.getElementById("setting-map-api-key").value || "").trim();
        const lat = parseFloat(document.getElementById("setting-map-lat").value) || 51.2277;
        const lon = parseFloat(document.getElementById("setting-map-lon").value) || 6.7735;
        const zoom = parseInt(document.getElementById("setting-map-zoom").value, 10) || 14;
        const rptIp = (document.getElementById("setting-hw-repeater-ip").value || "").trim();
        const usvIp = (document.getElementById("setting-hw-usv-ip").value || "").trim();
        const rtrIp = (document.getElementById("setting-hw-router-ip").value || "").trim();
        const sim = document.getElementById("setting-hw-simulation").checked;

        this.mapApiKey = key;
        this.switchBaseLayer(prov, key);
        this.initialMapCenter = [lat, lon];
        this.initialMapZoom = zoom;

        btnSaveMap.textContent = "⏳ Speichern...";
        try {
          await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              settings: {
                map_provider: prov,
                map_api_key: key,
                map_center: [lat, lon],
                map_zoom: zoom,
                hw_repeater_ip: rptIp,
                hw_usv_ip: usvIp,
                hw_router_ip: rtrIp,
                hw_simulation: sim
              }
            })
          });
          btnSaveMap.textContent = "✅ Gespeichert & Aktiviert!";
          setTimeout(() => { btnSaveMap.textContent = "💾 Alle Einstellungen speichern"; }, 2500);
        } catch (e) {
          btnSaveMap.textContent = "❌ Fehler beim Speichern";
        }
      });
    }

    // Button: Aktuelle Kartenansicht als Standard-Zentrum übernehmen
    const btnUseView = document.getElementById("btn-use-current-map-view");
    if (btnUseView) {
      btnUseView.addEventListener("click", () => {
        if (!this.map) return;
        const center = this.map.getCenter();
        const zoom = this.map.getZoom();
        document.getElementById("setting-map-lat").value = center.lat.toFixed(6);
        document.getElementById("setting-map-lon").value = center.lng.toFixed(6);
        document.getElementById("setting-map-zoom").value = zoom;
        btnUseView.textContent = "✅ Ansicht übernommen!";
        setTimeout(() => { btnUseView.textContent = "📌 Aktuelle Kartenansicht als Standard übernehmen"; }, 2000);
      });
    }

    // Ortssuche im Einstellungs-Modal initialisieren
    this.initSettingsLocationSearch();

    // Simulator-Button in Optionen
    const btnMock = document.getElementById("btn-toggle-mock-sim");
    if (btnMock) {
      btnMock.addEventListener("click", async () => {
        try {
          const resp = await fetch("/api/mock/toggle", { method: "POST" });
          const data = await resp.json();
          btnMock.textContent = data.mock_active ? "Simulator Pausieren (Aktiv)" : "Simulator Starten (Pausiert)";
        } catch (e) {
          console.error("Mock toggle error:", e);
        }
      });
    }

    // Sidebar & Timeline Toggles
    document.getElementById("btn-toggle-sidebar")?.addEventListener("click", () => {
      document.getElementById("hud-sidebar").classList.toggle("sidebar-hidden");
      setTimeout(() => this.map.invalidateSize(), 200);
    });
    document.getElementById("btn-toggle-timeline")?.addEventListener("click", () => {
      document.getElementById("hud-timeline-dock").classList.toggle("timeline-hidden");
      setTimeout(() => this.map.invalidateSize(), 200);
    });

    // Transparenz Schieberegler
    const opSlider = document.getElementById("overlay-opacity-slider");
    if (opSlider) {
      opSlider.addEventListener("input", (e) => {
        this.updateOverlayOpacity(parseInt(e.target.value, 10) / 100.0);
      });
    }

    const georefOpSlider = document.getElementById("georef-opacity-slider");
    if (georefOpSlider) {
      georefOpSlider.addEventListener("input", (e) => {
        this.updateOverlayOpacity(parseInt(e.target.value, 10) / 100.0);
      });
    }

    // Mini Audio Player Schließen
    document.getElementById("btn-close-audio-player")?.addEventListener("click", () => {
      document.getElementById("mini-audio-player").classList.add("mini-player-hidden");
      document.getElementById("global-audio-element").pause();
    });

    // Replay Buttons & Play/Pause
    document.getElementById("btn-replay-return-live")?.addEventListener("click", () => this.returnToLive());
    document.getElementById("btn-replay-jump-1m")?.addEventListener("click", () => this.jumpReplay(-60));
    document.getElementById("btn-replay-jump-5m")?.addEventListener("click", () => this.jumpReplay(-300));
    document.getElementById("btn-replay-jump-15m")?.addEventListener("click", () => this.jumpReplay(-900));
    document.getElementById("btn-replay-play")?.addEventListener("click", () => this.toggleReplayPlayback());

    // Vorfalls-Erfassungs-Modus Abbrechen Button
    document.getElementById("btn-cancel-map-mode")?.addEventListener("click", () => {
      this.isAddingMarker = false;
      document.getElementById("map-mode-indicator").classList.add("map-mode-hidden");
    });

    // Vorfalls-Formular Absenden & Schließen
    document.getElementById("form-create-marker")?.addEventListener("submit", (e) => this.submitMarkerForm(e));
    document.getElementById("btn-marker-modal-close")?.addEventListener("click", () => this.closeMarkerModal());
    document.getElementById("btn-marker-modal-cancel")?.addEventListener("click", () => this.closeMarkerModal());

    // Optionen Modal Schließen Buttons
    document.getElementById("btn-settings-modal-close")?.addEventListener("click", () => this.closeSettingsModal());
    document.getElementById("btn-settings-modal-close-btn")?.addEventListener("click", () => this.closeSettingsModal());

    // Infrastruktur Modal Schließen Buttons
    document.getElementById("btn-infra-modal-close")?.addEventListener("click", () => this.closeInfraModal());
    document.getElementById("btn-infra-close-btn")?.addEventListener("click", () => this.closeInfraModal());

    // Georef Modal Schließen Buttons
    document.getElementById("btn-georef-modal-close")?.addEventListener("click", () => this.closeGeorefModal());
    document.getElementById("btn-georef-modal-cancel")?.addEventListener("click", () => this.closeGeorefModal());

    // Geofence Modal Schließen Buttons
    document.getElementById("btn-geofence-modal-close")?.addEventListener("click", () => this.closeGeofenceModal());
    document.getElementById("btn-geofence-modal-cancel")?.addEventListener("click", () => this.closeGeofenceModal());

    // Klick auf Modal-Hintergrund (Backdrop) schließt das jeweilige Modal intuitiv
    [
      { id: "marker-modal-backdrop", close: () => this.closeMarkerModal() },
      { id: "settings-modal-backdrop", close: () => this.closeSettingsModal() },
      { id: "infra-modal-backdrop", close: () => this.closeInfraModal() },
      { id: "georef-modal-backdrop", close: () => this.closeGeorefModal() },
      { id: "geofence-modal-backdrop", close: () => this.closeGeofenceModal() },
      { id: "mission-modal-backdrop", close: () => this.closeMissionModal() },
      { id: "radios-modal-backdrop", close: () => this.closeRadiosModal() }
    ].forEach(({ id, close }) => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener("click", (e) => {
          if (e.target === el) close();
        });
      }
    });

    // Einsatz-Stammdaten Modal
    document.getElementById("btn-open-mission")?.addEventListener("click", () => this.openMissionModal());
    document.getElementById("btn-mission-modal-close")?.addEventListener("click", () => this.closeMissionModal());
    document.getElementById("btn-mission-modal-cancel")?.addEventListener("click", () => this.closeMissionModal());
    document.getElementById("form-mission-data")?.addEventListener("submit", (e) => this.submitMissionForm(e));
    document.getElementById("btn-close-mission-action")?.addEventListener("click", () => this.closeCurrentMission());

    // Funkgeräte- & Flottenverwaltung Modal
    document.getElementById("btn-open-radios-mgmt")?.addEventListener("click", () => this.openRadiosModal());
    document.getElementById("btn-radios-modal-close")?.addEventListener("click", () => this.closeRadiosModal());
    document.getElementById("btn-radios-modal-cancel")?.addEventListener("click", () => this.closeRadiosModal());
    document.getElementById("form-upsert-radio")?.addEventListener("submit", (e) => this.submitRadioForm(e));
    document.getElementById("btn-reset-radio-form")?.addEventListener("click", () => this.resetRadioForm());

    // Geofence-Formular
    document.getElementById("form-create-geofence")?.addEventListener("submit", (e) => this.submitGeofenceForm(e));

    // Drag and Drop Upload
    this.initDropzone();

    // Kalibrierungs-Dock Buttons
    document.getElementById("btn-georef-save")?.addEventListener("click", () => this.saveGeorefCalibration());
    document.getElementById("btn-georef-cancel")?.addEventListener("click", () => this.stopGeorefCalibration());
    document.getElementById("btn-georef-fit")?.addEventListener("click", () => this.fitGeorefToCurrentView());

    // Seitenleisten-Suche
    const searchInput = document.getElementById("input-radio-search");
    if (searchInput) {
      searchInput.addEventListener("input", (e) => this.filterSidebarRadios(e.target.value));
      document.getElementById("btn-clear-search")?.addEventListener("click", () => {
        searchInput.value = "";
        this.filterSidebarRadios("");
      });
    }
  }

  // ── Georeferenzierungs-Engine (4-Eck-Kalibrierung) ──
  initDropzone() {
    const dropzone = document.getElementById("georef-dropzone");
    const fileInput = document.getElementById("input-overlay-file");
    if (!dropzone || !fileInput) return;

    dropzone.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", (e) => {
      if (e.target.files.length > 0) this.uploadOverlayFile(e.target.files[0]);
    });

    ["dragenter", "dragover"].forEach(eventName => {
      dropzone.addEventListener(eventName, (e) => { e.preventDefault(); dropzone.style.background = "rgba(2, 132, 199, 0.15)"; });
    });
    ["dragleave", "drop"].forEach(eventName => {
      dropzone.addEventListener(eventName, (e) => { e.preventDefault(); dropzone.style.background = ""; });
    });
    dropzone.addEventListener("drop", (e) => {
      if (e.dataTransfer.files.length > 0) this.uploadOverlayFile(e.dataTransfer.files[0]);
    });
  }

  async uploadOverlayFile(file) {
    const prog = document.getElementById("upload-progress-wrap");
    const statText = document.getElementById("upload-status-text");
    prog.classList.remove("upload-progress-hidden");
    statText.textContent = `Lade '${file.name}' hoch und bereite GPS-Verankerung vor...`;

    const formData = new FormData();
    formData.append("file", file);

    try {
      const resp = await fetch("/api/overlay/upload", { method: "POST", body: formData });
      if (!resp.ok) throw new Error("Upload fehlgeschlagen");
      const res = await resp.json();

      prog.classList.add("upload-progress-hidden");
      this.closeGeorefModal();

      const nameInput = document.getElementById("input-plan-name");
      const floorInput = document.getElementById("select-plan-floor");
      const planName = (nameInput && nameInput.value) ? nameInput.value : file.name;
      const planFloor = (floorInput && floorInput.value) ? floorInput.value : "EG";

      this.startGeorefCalibration(res.image_url, planName, planFloor);

    } catch (err) {
      statText.textContent = `Fehler: ${err.message}`;
    }
  }

  startGeorefCalibration(imageUrl, planName, planFloor) {
    this.isCalibrating = true;
    document.getElementById("georef-dock").classList.remove("georef-dock-hidden");

    this.pendingPlanName = planName;
    this.pendingPlanFloor = planFloor;
    this.pendingPlanImageUrl = imageUrl;

    const bounds = this.map.getBounds();
    const south = bounds.getSouth() + (bounds.getNorth() - bounds.getSouth()) * 0.15;
    const north = bounds.getNorth() - (bounds.getNorth() - bounds.getSouth()) * 0.15;
    const west = bounds.getWest() + (bounds.getEast() - bounds.getWest()) * 0.15;
    const east = bounds.getEast() - (bounds.getEast() - bounds.getWest()) * 0.15;
    this.calibrationBounds = [[south, west], [north, east]];

    if (this.planOverlay) this.map.removeLayer(this.planOverlay);
    this.planOverlay = L.imageOverlay(imageUrl, this.calibrationBounds, { opacity: 0.8 }).addTo(this.map);

    this.createGeorefHandles();
  }

  createGeorefHandles() {
    this.georefHandles.forEach(h => this.map.removeLayer(h));
    this.georefHandles = [];
    if (this.georefCenterHandle) this.map.removeLayer(this.georefCenterHandle);

    const [[s, w], [n, e]] = this.calibrationBounds;
    const corners = [
      { id: "nw", latlng: [n, w] },
      { id: "ne", latlng: [n, e] },
      { id: "se", latlng: [s, e] },
      { id: "sw", latlng: [s, w] },
    ];

    corners.forEach(c => {
      const handleIcon = L.divIcon({
        className: "georef-handle-icon",
        html: `<div style="width:100%;height:100%;"></div>`,
        iconSize: [20, 20],
        iconAnchor: [10, 10]
      });
      const marker = L.marker(c.latlng, { icon: handleIcon, draggable: true }).addTo(this.map);
      marker.on("drag", () => this.onGeorefCornerDrag(c.id, marker.getLatLng()));
      this.georefHandles.push(marker);
    });

    const center = [(s + n) / 2.0, (w + e) / 2.0];
    const centerIcon = L.divIcon({
      className: "georef-center-icon",
      html: `✥`,
      iconSize: [26, 26],
      iconAnchor: [13, 13]
    });
    this.georefCenterHandle = L.marker(center, { icon: centerIcon, draggable: true }).addTo(this.map);
    this.georefCenterHandle.on("drag", () => this.onGeorefCenterDrag(this.georefCenterHandle.getLatLng()));
  }

  onGeorefCornerDrag(cornerId, newPos) {
    let [[s, w], [n, e]] = this.calibrationBounds;
    if (cornerId === "nw") { n = newPos.lat; w = newPos.lng; }
    else if (cornerId === "ne") { n = newPos.lat; e = newPos.lng; }
    else if (cornerId === "se") { s = newPos.lat; e = newPos.lng; }
    else if (cornerId === "sw") { s = newPos.lat; w = newPos.lng; }

    this.calibrationBounds = [[Math.min(s, n), Math.min(w, e)], [Math.max(s, n), Math.max(w, e)]];
    if (this.planOverlay) this.planOverlay.setBounds(this.calibrationBounds);
    this.updateHandlesPositions();
  }

  onGeorefCenterDrag(newCenter) {
    const [[s, w], [n, e]] = this.calibrationBounds;
    const oldC = [(s + n) / 2.0, (w + e) / 2.0];
    const dLat = newCenter.lat - oldC[0];
    const dLon = newCenter.lng - oldC[1];
    this.calibrationBounds = [[s + dLat, w + dLon], [n + dLat, e + dLon]];
    if (this.planOverlay) this.planOverlay.setBounds(this.calibrationBounds);
    this.updateHandlesPositions();
  }

  updateHandlesPositions() {
    const [[s, w], [n, e]] = this.calibrationBounds;
    const coords = [[n, w], [n, e], [s, e], [s, w]];
    this.georefHandles.forEach((h, idx) => h.setLatLng(coords[idx]));
    if (this.georefCenterHandle) this.georefCenterHandle.setLatLng([(s + n) / 2.0, (w + e) / 2.0]);
  }

  fitGeorefToCurrentView() {
    const b = this.map.getBounds();
    const s = b.getSouth() + (b.getNorth() - b.getSouth()) * 0.15;
    const n = b.getNorth() - (b.getNorth() - b.getSouth()) * 0.15;
    const w = b.getWest() + (b.getEast() - b.getWest()) * 0.15;
    const e = b.getEast() - (b.getEast() - b.getWest()) * 0.15;
    this.calibrationBounds = [[s, w], [n, e]];
    if (this.planOverlay) this.planOverlay.setBounds(this.calibrationBounds);
    this.updateHandlesPositions();
  }

  async saveGeorefCalibration() {
    const name = this.pendingPlanName || "Lageplan";
    const floor = this.pendingPlanFloor || "EG";
    const imageUrl = this.pendingPlanImageUrl;

    try {
      const resp = await fetch("/api/plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name,
          floor_level: floor,
          image_url: imageUrl,
          bounds: this.calibrationBounds,
          opacity: 0.85,
          is_active: true
        })
      });
      const data = await resp.json();
      await fetch(`/api/plans/${data.plan_id}/activate`, { method: "POST" });
      this.stopGeorefCalibration();
    } catch (e) {
      console.error("Fehler beim Speichern der Verankerung:", e);
    }
  }

  stopGeorefCalibration() {
    this.isCalibrating = false;
    document.getElementById("georef-dock").classList.add("georef-dock-hidden");
    this.georefHandles.forEach(h => this.map.removeLayer(h));
    this.georefHandles = [];
    if (this.georefCenterHandle) {
      this.map.removeLayer(this.georefCenterHandle);
      this.georefCenterHandle = null;
    }
    this.updateActivePlanForFloor(this.activeFloor);
  }

  // ── Karten-Klicks ──
  onMapClick(e) {
    if (this.isMeasuring) {
      this.onMeasureClick(e.latlng);
      return;
    }
    if (this.isAddingMarker) {
      this.openMarkerModal(e.latlng);
    }
  }

  onMapContextMenu(e) {
    e.originalEvent.preventDefault();
    this.openMarkerModal(e.latlng);
  }

  startMarkerMode() {
    this.isAddingMarker = true;
    document.getElementById("map-mode-indicator").classList.remove("map-mode-hidden");
  }

  openMarkerModal(latlng) {
    this.isAddingMarker = false;
    document.getElementById("map-mode-indicator").classList.add("map-mode-hidden");
    document.getElementById("input-marker-lat").value = latlng.lat.toFixed(6);
    document.getElementById("input-marker-lon").value = latlng.lng.toFixed(6);
    document.getElementById("marker-modal-backdrop").classList.remove("modal-backdrop-hidden");
  }

  closeMarkerModal() {
    document.getElementById("marker-modal-backdrop").classList.add("modal-backdrop-hidden");
    document.getElementById("form-create-marker").reset();
  }

  async submitMarkerForm(e) {
    e.preventDefault();
    const lat = parseFloat(document.getElementById("input-marker-lat").value);
    const lon = parseFloat(document.getElementById("input-marker-lon").value);
    const typ = document.getElementById("select-marker-type").value;
    const prio = document.getElementById("select-marker-priority").value;
    const desc = document.getElementById("input-marker-desc").value;
    const author = document.getElementById("input-marker-author").value;

    const symRadio = document.querySelector('input[name="tactical_symbol"]:checked');
    const tacticalSym = symRadio ? symRadio.value : "";

    try {
      await fetch("/api/markers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lat: lat,
          lon: lon,
          typ: typ,
          prioritaet: prio,
          beschreibung: desc,
          author: author,
          tactical_symbol: tacticalSym
        })
      });
      this.closeMarkerModal();
    } catch (err) {
      console.error("Fehler beim Erstellen des Markers:", err);
    }
  }

  // ── Geofence Modal ──
  openGeofenceModal() {
    const center = this.map.getCenter();
    document.getElementById("input-gf-lat").value = center.lat.toFixed(6);
    document.getElementById("input-gf-lon").value = center.lng.toFixed(6);
    document.getElementById("geofence-modal-backdrop").classList.remove("modal-backdrop-hidden");
  }
  closeGeofenceModal() {
    document.getElementById("geofence-modal-backdrop").classList.add("modal-backdrop-hidden");
  }

  async submitGeofenceForm(e) {
    e.preventDefault();
    const name = document.getElementById("input-gf-name").value;
    const type = document.getElementById("select-gf-type").value;
    const lat = parseFloat(document.getElementById("input-gf-lat").value);
    const lon = parseFloat(document.getElementById("input-gf-lon").value);
    const radius = parseFloat(document.getElementById("input-gf-radius").value);

    try {
      await fetch("/api/geofences", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name,
          zone_type: type,
          shape: "circle",
          center_lat: lat,
          center_lon: lon,
          radius_m: radius
        })
      });
      document.getElementById("form-create-geofence").reset();
    } catch (err) {
      console.error("Fehler beim Erstellen des Geofences:", err);
    }
  }

  renderGeofencesTable() {
    const tbody = document.getElementById("tbl-active-geofences");
    if (!tbody) return;
    if (!this.geofences || this.geofences.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:#94a3b8;">Keine Zonen aktiv</td></tr>';
      return;
    }
    tbody.innerHTML = this.geofences.map(gf => `
      <tr>
        <td><strong>${gf.name}</strong></td>
        <td><span class="badge badge-normal">${gf.zone_type.toUpperCase()}</span></td>
        <td class="mono">${gf.radius_m}m</td>
        <td>
          <button onclick="window.app.deleteGeofence(${gf.id})" style="background:#dc2626;color:#fff;border:none;padding:2px 6px;border-radius:4px;cursor:pointer;font-size:11px;">
            Löschen
          </button>
        </td>
      </tr>
    `).join("");
  }

  async deleteGeofence(id) {
    try {
      await fetch(`/api/geofences/${id}`, { method: "DELETE" });
    } catch (e) {
      console.error("Fehler beim Löschen des Geofences:", e);
    }
  }

  // ── Modals & Dialoge ──
  openGeorefModal() { document.getElementById("georef-modal-backdrop").classList.remove("modal-backdrop-hidden"); }
  closeGeorefModal() { document.getElementById("georef-modal-backdrop").classList.add("modal-backdrop-hidden"); }
  openSettingsModal() { document.getElementById("settings-modal-backdrop").classList.remove("modal-backdrop-hidden"); }
  closeSettingsModal() { document.getElementById("settings-modal-backdrop").classList.add("modal-backdrop-hidden"); }
  openInfraModal() { document.getElementById("infra-modal-backdrop").classList.remove("modal-backdrop-hidden"); }
  closeInfraModal() { document.getElementById("infra-modal-backdrop").classList.add("modal-backdrop-hidden"); }

  // ── Einsatz-Stammdaten & Missions-Manager ──
  updateMissionUI() {
    const titleEl = document.getElementById("toolbar-mission-title");
    if (!titleEl) return;
    const title = this.mission?.mission_title || "LAGEZENTRUM";
    const code = this.mission?.mission_code || "";
    const status = this.mission?.mission_status || "running";
    titleEl.textContent = `EINSATZ: ${title.toUpperCase()}${code ? ` (${code})` : ""}`;

    const btnMission = document.getElementById("btn-open-mission");
    if (btnMission) {
      if (status === "finished") {
        btnMission.style.background = "#475569";
        btnMission.title = "Einsatz ist beendet (Klicken zum Einsehen / Wiedereröffnen)";
      } else if (status === "preparation") {
        btnMission.style.background = "#d97706";
        btnMission.title = "Einsatz in Vorbereitung";
      } else {
        btnMission.style.background = "#4338ca";
        btnMission.title = "Einsatz aktiv";
      }
    }
  }

  openMissionModal() {
    const m = this.mission || {};
    const titleInput = document.getElementById("input-mission-title");
    const codeInput = document.getElementById("input-mission-code");
    const statusInput = document.getElementById("select-mission-status");
    const leaderInput = document.getElementById("input-mission-leader");
    const locInput = document.getElementById("input-mission-location");
    const chanInput = document.getElementById("input-mission-channel");
    const notesInput = document.getElementById("textarea-mission-notes");

    if (titleInput) titleInput.value = m.mission_title || "";
    if (codeInput) codeInput.value = m.mission_code || "";
    if (statusInput) statusInput.value = m.mission_status || "running";
    if (leaderInput) leaderInput.value = m.mission_leader || "";
    if (locInput) locInput.value = m.mission_location || "";
    if (chanInput) chanInput.value = m.mission_channel || "";
    if (notesInput) notesInput.value = m.mission_notes || "";

    document.getElementById("mission-modal-backdrop").classList.remove("modal-backdrop-hidden");
  }

  closeMissionModal() {
    document.getElementById("mission-modal-backdrop").classList.add("modal-backdrop-hidden");
  }

  async submitMissionForm(e) {
    if (e) e.preventDefault();
    const payload = {
      mission_title: document.getElementById("input-mission-title")?.value || "",
      mission_code: document.getElementById("input-mission-code")?.value || "",
      mission_status: document.getElementById("select-mission-status")?.value || "running",
      mission_leader: document.getElementById("input-mission-leader")?.value || "",
      mission_location: document.getElementById("input-mission-location")?.value || "",
      mission_channel: document.getElementById("input-mission-channel")?.value || "",
      mission_notes: document.getElementById("textarea-mission-notes")?.value || ""
    };

    const saveBtn = document.getElementById("btn-save-mission");
    if (saveBtn) saveBtn.textContent = "⏳ Speichern...";
    try {
      const resp = await fetch("/api/mission", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await resp.json();
      if (data.mission) {
        this.mission = data.mission;
        this.updateMissionUI();
      }
      if (saveBtn) {
        saveBtn.textContent = "✅ Gespeichert!";
        setTimeout(() => { saveBtn.textContent = "💾 SPEICHERN & RUNDSENDEN"; }, 1500);
      }
      this.closeMissionModal();
    } catch (err) {
      console.error("Fehler beim Speichern der Einsatzdaten:", err);
      if (saveBtn) saveBtn.textContent = "❌ Fehler beim Speichern";
    }
  }

  async closeCurrentMission() {
    if (!confirm("Möchten Sie diesen Einsatz wirklich beenden und archivieren?")) return;
    try {
      const resp = await fetch("/api/mission/close", { method: "POST" });
      const data = await resp.json();
      if (data.mission) {
        this.mission = data.mission;
        this.updateMissionUI();
      }
      this.closeMissionModal();
    } catch (err) {
      console.error("Fehler beim Beenden des Einsatzes:", err);
    }
  }

  // ── Funkgeräte- & Flotten-Verwaltung ──
  openRadiosModal() {
    this.resetRadioForm();
    this.renderRadiosTable();
    document.getElementById("radios-modal-backdrop").classList.remove("modal-backdrop-hidden");
  }

  closeRadiosModal() {
    document.getElementById("radios-modal-backdrop").classList.add("modal-backdrop-hidden");
  }

  resetRadioForm() {
    const form = document.getElementById("form-upsert-radio");
    if (form) form.reset();
    const idInput = document.getElementById("input-rf-radio-id");
    if (idInput) idInput.readOnly = false;
    const title = document.getElementById("radio-form-title");
    if (title) title.textContent = "Neues Funkgerät anlegen";
    const btnSubmit = document.getElementById("btn-submit-radio-form");
    if (btnSubmit) btnSubmit.textContent = "💾 GERÄT SPEICHERN";
    const btnReset = document.getElementById("btn-reset-radio-form");
    if (btnReset) btnReset.style.display = "none";
  }

  editRadio(radioId) {
    const radio = this.radios[radioId];
    if (!radio) return;

    const idInput = document.getElementById("input-rf-radio-id");
    const aliasInput = document.getElementById("input-rf-alias");
    const modelInput = document.getElementById("input-rf-model");
    const gpsSelect = document.getElementById("select-rf-gps");
    const floorSelect = document.getElementById("select-rf-floor");

    if (idInput) {
      idInput.value = radio.radio_id;
      idInput.readOnly = true;
    }
    if (aliasInput) aliasInput.value = radio.alias || "";
    if (modelInput) modelInput.value = radio.device_model || "";
    if (gpsSelect) gpsSelect.value = radio.has_gps ? "1" : "0";
    if (floorSelect) floorSelect.value = radio.floor_level || "EG";

    const title = document.getElementById("radio-form-title");
    if (title) title.textContent = `Funkgerät #${radio.radio_id} bearbeiten`;
    const btnSubmit = document.getElementById("btn-submit-radio-form");
    if (btnSubmit) btnSubmit.textContent = "💾 ÄNDERUNGEN SPEICHERN";
    const btnReset = document.getElementById("btn-reset-radio-form");
    if (btnReset) btnReset.style.display = "inline-block";

    idInput?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async submitRadioForm(e) {
    if (e) e.preventDefault();
    const radioId = parseInt(document.getElementById("input-rf-radio-id")?.value);
    const alias = document.getElementById("input-rf-alias")?.value.trim();
    const model = document.getElementById("input-rf-model")?.value.trim() || "";
    const hasGps = document.getElementById("select-rf-gps")?.value === "1";
    const floor = document.getElementById("select-rf-floor")?.value || "EG";

    if (isNaN(radioId) || !alias) {
      alert("Bitte geben Sie eine gültige DMR-ID und einen Namen ein.");
      return;
    }

    const payload = {
      radio_id: radioId,
      alias: alias,
      device_model: model,
      has_gps: hasGps,
      floor_level: floor
    };

    const submitBtn = document.getElementById("btn-submit-radio-form");
    if (submitBtn) submitBtn.textContent = "⏳ Speichern...";

    try {
      const resp = await fetch("/api/radios", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!resp.ok) throw new Error("Speichern fehlgeschlagen");

      if (!this.radios[radioId]) {
        this.radios[radioId] = { radio_id: radioId };
      }
      Object.assign(this.radios[radioId], payload);
      this.updateRadioMarker(this.radios[radioId]);
      this.updateSidebarRadios();
      this.renderRadiosTable();
      this.populateTestEmergencyDropdown();
      this.resetRadioForm();
    } catch (err) {
      console.error("Fehler beim Speichern des Funkgeräts:", err);
      alert("Fehler beim Speichern des Funkgeräts: " + err.message);
      if (submitBtn) submitBtn.textContent = "💾 GERÄT SPEICHERN";
    }
  }

  async deleteRadio(radioId) {
    const radio = this.radios[radioId];
    const alias = radio?.alias || `Radio #${radioId}`;
    if (!confirm(`Funkgerät '${alias}' (ID: ${radioId}) wirklich löschen?\n\nEs wird aus der Datenbank und ids.json entfernt.`)) return;

    try {
      const resp = await fetch(`/api/radios/${radioId}`, { method: "DELETE" });
      if (!resp.ok) throw new Error("Löschen fehlgeschlagen");

      if (this.radioMarkers[radioId]) {
        this.map.removeLayer(this.radioMarkers[radioId]);
        delete this.radioMarkers[radioId];
      }
      if (this.radioTrails[radioId]) {
        this.map.removeLayer(this.radioTrails[radioId]);
        delete this.radioTrails[radioId];
      }
      delete this.radios[radioId];

      this.updateSidebarRadios();
      this.renderRadiosTable();
      this.populateTestEmergencyDropdown();
    } catch (err) {
      console.error("Fehler beim Löschen des Funkgeräts:", err);
      alert("Fehler beim Löschen: " + err.message);
    }
  }

  renderRadiosTable() {
    const tbody = document.getElementById("tbl-radios-fleet");
    const countEl = document.getElementById("radios-table-count");
    if (!tbody) return;

    const list = Object.values(this.radios).sort((a, b) => a.radio_id - b.radio_id);
    if (countEl) countEl.textContent = list.length;

    if (list.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#94a3b8;">Keine Funkgeräte hinterlegt. Legen Sie oben ein neues Funkgerät an.</td></tr>';
      return;
    }

    tbody.innerHTML = list.map(r => `
      <tr>
        <td class="mono bold" style="color:var(--accent-primary);">${r.radio_id}</td>
        <td><strong>${r.alias || 'Unbekannt'}</strong></td>
        <td style="color:var(--text-muted);font-size:12px;">${r.device_model || '-'}</td>
        <td>${r.has_gps ? '<span class="badge badge-online">🛰️ GPS</span>' : '<span class="badge badge-low">📻 MANUELL</span>'}</td>
        <td><span class="badge badge-normal">${r.floor_level || 'EG'}</span></td>
        <td style="text-align:right;">
          <button class="table-btn-edit" onclick="window.app.editRadio(${r.radio_id})">✏️ Bearbeiten</button>
          <button class="table-btn-del" onclick="window.app.deleteRadio(${r.radio_id})">🗑️ Löschen</button>
        </td>
      </tr>
    `).join("");
  }

  renderPlansTable() {
    const tbody = document.getElementById("tbl-saved-plans");
    if (!tbody) return;
    if (!this.plans || this.plans.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:#94a3b8;">Keine Pläne hinterlegt</td></tr>';
      return;
    }
    tbody.innerHTML = this.plans.map(p => `
      <tr>
        <td><strong>${p.name}</strong></td>
        <td><span class="badge badge-normal">${p.floor_level}</span></td>
        <td>${p.is_active ? '<span class="badge badge-online">AKTIV</span>' : '<span class="badge badge-low">INAKTIV</span>'}</td>
        <td>
          <button onclick="window.app.activatePlan(${p.id})" style="background:#2563eb;color:#fff;border:none;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:11px;">
            Aktivieren
          </button>
          <button onclick="window.app.deletePlan(${p.id})" style="background:#dc2626;color:#fff;border:none;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:11px;">
            Löschen
          </button>
        </td>
      </tr>
    `).join("");
  }

  async activatePlan(id) {
    try {
      await fetch(`/api/plans/${id}/activate`, { method: "POST" });
    } catch (e) {
      console.error("Fehler beim Aktivieren des Plans:", e);
    }
  }

  async deletePlan(id) {
    try {
      await fetch(`/api/plans/${id}`, { method: "DELETE" });
    } catch (e) {
      console.error("Fehler beim Löschen des Plans:", e);
    }
  }

  populateTestEmergencyDropdown() {
    const sel = document.getElementById("select-test-emergency-radio");
    if (!sel) return;
    sel.innerHTML = Object.values(this.radios).map(r => `
      <option value="${r.radio_id}">${r.alias} (ID: ${r.radio_id})</option>
    `).join("");
    document.getElementById("btn-trigger-test-emergency").onclick = () => {
      this.triggerTestEmergency(sel.value);
    };
  }

  // ── WebSocket Kommunikation ──
  connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/live`;
    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      document.getElementById("conn-dot").className = "dot-connected";
      document.getElementById("conn-status-text").textContent = "LIVE VERBUNDEN";
    };

    this.ws.onclose = () => {
      document.getElementById("conn-dot").className = "dot-disconnected";
      document.getElementById("conn-status-text").textContent = "GETRENNT (RECONNECT...)";
      setTimeout(() => this.connectWebSocket(), 3000);
    };

    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        this.handleWebSocketMessage(msg);
      } catch (err) {
        console.debug("WS parse error:", err);
      }
    };
  }

  handleWebSocketMessage(msg) {
    const type = msg.type;

    if (type === "gps_update") {
      const rid = msg.radio_id;
      if (!this.radios[rid]) this.radios[rid] = { radio_id: rid };
      Object.assign(this.radios[rid], msg);
      this.updateRadioMarker(this.radios[rid]);
      this.updateSidebarRadios();

    } else if (type === "ptt_start") {
      this.handlePTTStart(msg);

    } else if (type === "ptt_end") {
      this.handlePTTEnd(msg);

    } else if (type === "emergency_alert") {
      this.setEmergencyState(msg);
      if (this.radios[msg.radio_id]) this.radios[msg.radio_id].is_emergency = true;
      this.updateRadioMarker(this.radios[msg.radio_id]);

    } else if (type === "emergency_ack") {
      this.clearEmergencyState();
      if (this.radios[msg.radio_id]) this.radios[msg.radio_id].is_emergency = false;
      this.updateRadioMarker(this.radios[msg.radio_id]);

    } else if (type === "geofence_alert") {
      this.showGeofenceAlert(msg);

    } else if (type === "marker_created") {
      this.addIncidentMarker(msg.marker);
      this.initialMarkers.unshift(msg.marker);
      this.updateSidebarIncidents(this.initialMarkers);

    } else if (type === "marker_deleted") {
      if (this.incidentMarkers[msg.marker_id]) {
        this.map.removeLayer(this.incidentMarkers[msg.marker_id]);
        delete this.incidentMarkers[msg.marker_id];
      }
      this.initialMarkers = this.initialMarkers.filter(m => m.id !== msg.marker_id);
      this.updateSidebarIncidents(this.initialMarkers);

    } else if (type === "infrastructure_update") {
      this.updateInfrastructureUI(msg.telemetry);

    } else if (type === "plans_updated" || type === "plan_activated") {
      this.plans = msg.plans;
      this.renderPlansTable();
      this.updateActivePlanForFloor(this.activeFloor);

    } else if (type === "geofences_updated") {
      this.geofences = msg.geofences;
      this.renderGeofencesTable();
      this.renderGeofencesOnMap();

    } else if (type === "radio_updated") {
      const rid = msg.radio ? msg.radio.radio_id : msg.radio_id;
      if (!this.radios[rid]) this.radios[rid] = { radio_id: rid };
      if (msg.radio) {
        Object.assign(this.radios[rid], msg.radio);
      } else {
        Object.assign(this.radios[rid], msg);
      }
      this.updateRadioMarker(this.radios[rid]);
      this.updateSidebarRadios();
      this.renderRadiosTable();
      this.populateTestEmergencyDropdown();

    } else if (type === "radio_deleted") {
      const rid = msg.radio_id;
      if (this.radioMarkers[rid]) {
        this.map.removeLayer(this.radioMarkers[rid]);
        delete this.radioMarkers[rid];
      }
      if (this.radioTrails[rid]) {
        this.map.removeLayer(this.radioTrails[rid]);
        delete this.radioTrails[rid];
      }
      delete this.radios[rid];
      this.updateSidebarRadios();
      this.renderRadiosTable();
      this.populateTestEmergencyDropdown();

    } else if (type === "mission_updated") {
      this.mission = msg.mission || {};
      this.updateMissionUI();
    }
  }

  handlePTTStart(msg) {
    const banner = document.getElementById("ptt-alert-banner");
    banner.className = "ptt-banner-active";
    document.getElementById("ptt-banner-text").textContent = `FUNKSPRUCHE: ${msg.alias} (${msg.slot})`;
    this.playPttStartBeep();

    if (this.radios[msg.radio_id]) {
      this.radios[msg.radio_id].is_transmitting = true;
      this.updateRadioMarker(this.radios[msg.radio_id]);
    }
  }

  handlePTTEnd(msg) {
    const banner = document.getElementById("ptt-alert-banner");
    banner.className = "ptt-banner-idle";
    document.getElementById("ptt-banner-text").textContent = "FUNKBEREITSCHAFT IDLE";
    this.playPttEndBeep();

    if (this.radios[msg.radio_id]) {
      this.radios[msg.radio_id].is_transmitting = false;
      this.updateRadioMarker(this.radios[msg.radio_id]);
    }

    // 12s PTT-Decay Ring
    const ring = document.getElementById(`decay-ring-${msg.radio_id}`);
    if (ring) {
      ring.style.display = "block";
      setTimeout(() => { if (ring) ring.style.display = "none"; }, 12000);
    }

    this.addCallLogEntry(msg);
  }

  // ── Sidebar & Telemetrie Aktualisierung ──
  updateSidebarRadios() {
    const list = document.getElementById("radio-list");
    if (!list) return;

    const units = Object.values(this.radios);
    document.getElementById("sidebar-unit-count").textContent = units.length;
    document.getElementById("stat-radios-gps").textContent = units.filter(u => u.has_gps).length;
    document.getElementById("stat-radios-nogps").textContent = units.filter(u => !u.has_gps).length;

    if (units.length === 0) {
      list.innerHTML = '<div class="sidebar-empty">Keine Funkgeräte registriert.<br><small style="color:var(--text-muted);margin-top:4px;display:block;">Nutzen Sie oben <strong>"+ GERÄTE"</strong> zum Anlegen.</small></div>';
      return;
    }

    const searchVal = (document.getElementById("input-radio-search")?.value || "").toLowerCase().trim();

    const filtered = units.filter(u => {
      if (!searchVal) return true;
      return (u.alias && u.alias.toLowerCase().includes(searchVal)) ||
             String(u.radio_id).includes(searchVal) ||
             (u.device_model && u.device_model.toLowerCase().includes(searchVal));
    });

    if (filtered.length === 0) {
      list.innerHTML = '<div class="sidebar-empty">Keine Funkgeräte gefunden</div>';
      return;
    }

    list.innerHTML = filtered.map(u => {
      const isTx = u.is_transmitting;
      const isEm = u.is_emergency;
      let cardClass = "radio-card";
      if (isEm) cardClass += " radio-card-emergency";
      else if (isTx) cardClass += " radio-card-tx";

      return `
        <div class="${cardClass}" onclick="window.app.focusRadio(${u.radio_id})">
          <div class="radio-card-header">
            <span class="radio-alias">${u.alias}</span>
            <span class="radio-badge ${isEm ? 'badge-emergency' : (u.has_gps ? 'badge-gps' : 'badge-nogps')}">
              ${isEm ? '🚨 NOTRUF' : (u.has_gps ? 'GPS' : 'KEIN GPS')}
            </span>
          </div>
          <div class="radio-card-body">
            <span>${u.floor_level || 'EG'} | ID: ${u.radio_id}</span>
            ${u.rssi ? `<span class="radio-rssi-badge">${Math.round(u.rssi)} dBm</span>` : ''}
          </div>
        </div>
      `;
    }).join("");
  }

  updateSidebarIncidents(markers) {
    const list = document.getElementById("incident-quick-list");
    if (!list) return;
    document.getElementById("stat-markers").textContent = markers.length;
    document.getElementById("sidebar-incident-count").textContent = markers.length;

    if (!markers || markers.length === 0) {
      list.innerHTML = '<div class="sidebar-empty">Keine aktiven Vorfälle</div>';
      return;
    }

    list.innerHTML = markers.slice(0, 8).map(m => `
      <div class="incident-quick-item" onclick="window.app.map.setView([${m.lat}, ${m.lon}], 17, {animate:true})">
        <span><strong>${m.typ}:</strong> ${m.beschreibung.substring(0, 24)}...</span>
        <span class="badge badge-${m.prioritaet}">${m.prioritaet}</span>
      </div>
    `).join("");
  }

  // ── Karten- & Ortssuche (HUD-Toolbar & Optionen) ──
  initMapSearch() {
    const input = document.getElementById("input-map-search");
    const dropdown = document.getElementById("map-search-dropdown");
    const btnClear = document.getElementById("btn-clear-map-search");
    if (!input || !dropdown) return;

    let debounceTimer = null;

    input.addEventListener("input", () => {
      const q = input.value.trim();
      if (btnClear) btnClear.style.display = q.length > 0 ? "block" : "none";

      if (debounceTimer) clearTimeout(debounceTimer);
      if (q.length < 2) {
        dropdown.style.display = "none";
        dropdown.innerHTML = "";
        return;
      }

      debounceTimer = setTimeout(async () => {
        try {
          const resp = await fetch("/api/geocode?q=" + encodeURIComponent(q));
          const data = await resp.json();
          this.renderMapSearchResults(data.results || [], dropdown, (item) => {
            input.value = item.name;
            dropdown.style.display = "none";
            this.selectMapLocation(item);
          });
        } catch (e) {
          console.debug("Geocode search error:", e);
        }
      }, 280);
    });

    if (btnClear) {
      btnClear.addEventListener("click", () => {
        input.value = "";
        dropdown.style.display = "none";
        dropdown.innerHTML = "";
        btnClear.style.display = "none";
        input.focus();
      });
    }

    document.addEventListener("click", (e) => {
      if (!input.contains(e.target) && !dropdown.contains(e.target)) {
        dropdown.style.display = "none";
      }
    });
  }

  initSettingsLocationSearch() {
    const input = document.getElementById("setting-location-search");
    const dropdown = document.getElementById("setting-search-dropdown");
    if (!input || !dropdown) return;

    let debounceTimer = null;

    input.addEventListener("input", () => {
      const q = input.value.trim();
      if (debounceTimer) clearTimeout(debounceTimer);
      if (q.length < 2) {
        dropdown.style.display = "none";
        dropdown.innerHTML = "";
        return;
      }

      debounceTimer = setTimeout(async () => {
        try {
          const resp = await fetch("/api/geocode?q=" + encodeURIComponent(q));
          const data = await resp.json();
          this.renderMapSearchResults(data.results || [], dropdown, (item) => {
            input.value = item.name;
            dropdown.style.display = "none";
            document.getElementById("setting-map-lat").value = item.lat.toFixed(6);
            document.getElementById("setting-map-lon").value = item.lon.toFixed(6);
            document.getElementById("setting-map-zoom").value = 15;
            if (this.map) {
              this.map.setView([item.lat, item.lon], 15);
            }
          });
        } catch (e) {
          console.debug("Settings geocode error:", e);
        }
      }, 280);
    });

    document.addEventListener("click", (e) => {
      if (!input.contains(e.target) && !dropdown.contains(e.target)) {
        dropdown.style.display = "none";
      }
    });
  }

  renderMapSearchResults(results, container, onSelect) {
    container.innerHTML = "";
    if (!results || results.length === 0) {
      container.innerHTML = '<div class="search-result-empty">Keine Orte oder Objekte gefunden</div>';
      container.style.display = "block";
      return;
    }

    results.forEach(item => {
      const div = document.createElement("div");
      div.className = "search-result-item";
      let icon = "📍";
      if (item.type === "marker") icon = "📌";
      else if (item.type === "geofence") icon = "🛡️";
      else if (item.type === "radio") icon = "📻";
      else if (item.type === "coordinate") icon = "🌐";

      div.innerHTML = `
        <div class="search-result-title">${icon} ${item.name}</div>
        <div class="search-result-desc">${item.display_name}</div>
      `;
      div.addEventListener("click", () => onSelect(item));
      container.appendChild(div);
    });

    container.style.display = "block";
  }

  selectMapLocation(item) {
    if (!this.map) return;
    this.map.flyTo([item.lat, item.lon], 16, { duration: 1.2 });

    if (this._searchPopup) {
      this.map.removeLayer(this._searchPopup);
    }

    const popupHtml = document.createElement("div");
    popupHtml.innerHTML = `
      <div style="font-weight:700;font-size:13px;margin-bottom:3px;">${item.name}</div>
      <div style="font-size:11px;color:#94a3b8;margin-bottom:8px;">${item.display_name}</div>
      <div style="display:flex;flex-direction:column;gap:5px;">
        <button id="btn-popup-set-center" class="btn btn-sm btn-primary" style="font-size:11px;padding:4px 8px;">🎯 Als Standard-Zentrum setzen</button>
        <button id="btn-popup-add-marker" class="btn btn-sm btn-cta" style="font-size:11px;padding:4px 8px;">➕ Vorfall hier anlegen</button>
      </div>
    `;

    popupHtml.querySelector("#btn-popup-set-center").onclick = async () => {
      const zoom = this.map.getZoom();
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: { map_center: [item.lat, item.lon], map_zoom: zoom } })
      });
      document.getElementById("setting-map-lat").value = item.lat.toFixed(6);
      document.getElementById("setting-map-lon").value = item.lon.toFixed(6);
      document.getElementById("setting-map-zoom").value = zoom;
      this.initialMapCenter = [item.lat, item.lon];
      this.initialMapZoom = zoom;
      alert("✅ Einsatzgebiet gespeichert: Karte startet künftig hier.");
      this.map.closePopup();
    };

    popupHtml.querySelector("#btn-popup-add-marker").onclick = () => {
      document.getElementById("marker-lat").value = item.lat.toFixed(6);
      document.getElementById("marker-lon").value = item.lon.toFixed(6);
      document.getElementById("marker-title").value = item.name.replace(/^[📍📌🛡️📻🌐]\s*/, "");
      document.getElementById("marker-modal-backdrop").className = "modal-backdrop-visible";
      this.map.closePopup();
    };

    this._searchPopup = L.popup()
      .setLatLng([item.lat, item.lon])
      .setContent(popupHtml)
      .openOn(this.map);
  }

  updateInfrastructureUI(infra) {
    if (!infra) return;

    // Repeater
    const rpt = infra.repeater;
    if (rpt) {
      const isOnline = rpt.status === "online";
      const sum = isOnline
        ? `${rpt.temperature_c !== null ? rpt.temperature_c + '°C' : 'ONLINE'} | ${rpt.forward_power_w !== null ? rpt.forward_power_w + 'W' : '--'}`
        : "OFFLINE";
      document.getElementById("stat-repeater-summary").textContent = sum;

      const rptBadge = document.getElementById("rpt-status-badge");
      if (rptBadge) {
        rptBadge.textContent = isOnline ? "ONLINE" : "OFFLINE";
        rptBadge.className = isOnline ? "badge-online" : "badge-offline";
      }

      document.getElementById("rpt-temp").textContent = (isOnline && rpt.temperature_c !== null) ? `${rpt.temperature_c} °C` : "--";
      document.getElementById("rpt-vswr").textContent = (isOnline && rpt.vswr !== null) ? rpt.vswr : "--";
      document.getElementById("rpt-fwd-pwr").textContent = (isOnline && rpt.forward_power_w !== null) ? `${rpt.forward_power_w} W` : "--";
      document.getElementById("rpt-ref-pwr").textContent = (isOnline && rpt.reflected_power_w !== null) ? `${rpt.reflected_power_w} W` : "--";
      document.getElementById("rpt-volt").textContent = (isOnline && rpt.supply_voltage_v !== null) ? `${rpt.supply_voltage_v} V DC` : "--";
      document.getElementById("rpt-channel").textContent = isOnline ? (rpt.channel_alias || "--") : "--";
      document.getElementById("rpt-slot").textContent = isOnline ? (rpt.active_slot || "IDLE / BEREIT") : "OFFLINE";

      const pill = document.getElementById("pill-repeater");
      if (isOnline && (rpt.pa_overheat_alarm || rpt.high_vswr_alarm)) pill.classList.add("pill-alarm-pulse");
      else pill.classList.remove("pill-alarm-pulse");
    }

    // USV
    const usv = infra.usv;
    if (usv) {
      const isOnline = usv.status === "online";
      const sum = isOnline
        ? `${usv.battery_charge_percent}% (${usv.mains_online ? 'Netz' : 'Akku'})`
        : "OFFLINE";
      document.getElementById("stat-usv-summary").textContent = sum;

      const usvBadge = document.getElementById("usv-status-badge");
      if (usvBadge) {
        if (!isOnline) {
          usvBadge.textContent = "OFFLINE";
          usvBadge.className = "badge-offline";
        } else if (usv.mains_online) {
          usvBadge.textContent = "NETZBETRIEB";
          usvBadge.className = "badge-online";
        } else {
          usvBadge.textContent = "AKKUBETRIEB";
          usvBadge.className = "badge-alarm";
        }
      }

      document.getElementById("usv-batt-pct").textContent = (isOnline && usv.battery_charge_percent !== null) ? `${usv.battery_charge_percent} %` : "-- %";
      document.getElementById("usv-battery-fill").style.width = (isOnline && usv.battery_charge_percent !== null) ? `${usv.battery_charge_percent}%` : "0%";
      document.getElementById("usv-batt-min").textContent = (isOnline && usv.runtime_remaining_min !== null) ? `${usv.runtime_remaining_min} Minuten` : "-- Minuten";
      document.getElementById("usv-in-volt").textContent = (isOnline && usv.input_voltage_v !== null) ? `${usv.input_voltage_v} V / ${usv.frequency_hz || 50} Hz` : "--";
      document.getElementById("usv-out-volt").textContent = (isOnline && usv.output_voltage_v !== null) ? `${usv.output_voltage_v} V` : "--";
      document.getElementById("usv-load").textContent = (isOnline && usv.load_percent !== null) ? `${usv.load_percent} %` : "--";
      document.getElementById("usv-temp").textContent = (isOnline && usv.temperature_c !== null) ? `${usv.temperature_c} °C` : "--";

      const pill = document.getElementById("pill-usv");
      if (isOnline && (usv.alarm_active || !usv.mains_online)) pill.classList.add("pill-alarm-pulse");
      else pill.classList.remove("pill-alarm-pulse");
    }

    // Router
    const rtr = infra.router;
    if (rtr) {
      const isOnline = rtr.status === "online";
      document.getElementById("stat-router-summary").textContent = isOnline
        ? `${rtr.network_type || '4G'} (${rtr.ping_ms ? rtr.ping_ms + 'ms' : 'OK'})`
        : "OFFLINE";

      const rtrBadge = document.getElementById("router-status-badge");
      if (rtrBadge) {
        rtrBadge.textContent = isOnline ? "ONLINE" : "OFFLINE";
        rtrBadge.className = isOnline ? "badge-online" : "badge-offline";
      }

      document.getElementById("router-provider").textContent = isOnline ? (rtr.provider || "LTE Gateway") : "--";
      document.getElementById("router-signal-bars").textContent = isOnline ? `📶 ${rtr.signal_bars || 4} / 5 Balken` : "Kein Signal";
      document.getElementById("router-rsrp").textContent = (isOnline && rtr.rsrp_dbm !== null) ? `${rtr.rsrp_dbm} dBm` : "--";
      document.getElementById("router-ping").textContent = (isOnline && rtr.ping_ms !== null) ? `${rtr.ping_ms} ms` : "--";
      document.getElementById("router-throughput").textContent = (isOnline && rtr.download_mbps !== null) ? `⬇️ ${rtr.download_mbps} Mbps | ⬆️ ${rtr.upload_mbps || 0} Mbps` : "--";
    }
  }

  updateVpnUI(vpn) {
    if (!vpn) return;
    this.vpn = vpn;
    const isOnline = !!vpn.active;

    const summaryEl = document.getElementById("stat-vpn-summary");
    if (summaryEl) {
      summaryEl.textContent = isOnline ? (vpn.ip || "ONLINE") : "OFFLINE";
      summaryEl.style.color = isOnline ? "#34d399" : "";
    }

    const vpnBadge = document.getElementById("vpn-status-badge");
    if (vpnBadge) {
      vpnBadge.textContent = isOnline ? "ONLINE" : "OFFLINE";
      vpnBadge.className = isOnline ? "badge-online" : "badge-offline";
    }

    const activeText = document.getElementById("vpn-active-text");
    if (activeText) {
      activeText.textContent = isOnline ? "Aktiv (Tailscale verbunden)" : "Nicht aktiv / getrennt";
      activeText.style.color = isOnline ? "#34d399" : "#ef4444";
    }

    const ipVal = document.getElementById("vpn-ip-val");
    if (ipVal) {
      ipVal.textContent = isOnline ? vpn.ip : "--";
    }

    const urlLink = document.getElementById("vpn-url-link");
    if (urlLink) {
      if (isOnline && vpn.url && vpn.url !== "--") {
        urlLink.href = vpn.url;
        urlLink.textContent = vpn.url;
        urlLink.style.display = "inline";
      } else {
        urlLink.removeAttribute("href");
        urlLink.textContent = "Keine VPN-Verbindung";
        urlLink.style.display = "inline";
      }
    }
  }

  copyVpnUrl() {
    if (this.vpn && this.vpn.url && this.vpn.url !== "--") {
      const url = this.vpn.url;
      const lbl = document.getElementById("btn-copy-vpn-label");
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(() => {
          if (lbl) lbl.textContent = "✅ IN ZWISCHENABLAGE KOPIERT!";
          setTimeout(() => { if (lbl) lbl.textContent = "VPN-URL KOPIEREN"; }, 2500);
        }).catch(() => {
          prompt("VPN-URL zum Kopieren:", url);
        });
      } else {
        prompt("VPN-URL zum Kopieren:", url);
      }
    } else {
      alert("Tailscale VPN ist auf diesem Rechner aktuell nicht aktiv.\n\nStarten Sie Tailscale oder führen Sie 'tailscale up' aus.");
    }
  }

  updateConnectionSummaryUI(data) {
    if (!data) return;
    const port = window.location.port || "8000";
    const lanEl = document.getElementById("conn-info-lan");
    if (lanEl) {
      lanEl.textContent = data.lan_ip ? `http://${data.lan_ip}:${port}` : "Nicht ermittelt";
    }

    const vpnEl = document.getElementById("conn-info-vpn");
    if (vpnEl) {
      if (data.vpn && data.vpn.active && data.vpn.url && data.vpn.url !== "--") {
        vpnEl.textContent = data.vpn.url;
        vpnEl.style.color = "#34d399";
      } else {
        vpnEl.textContent = "Nicht aktiv";
        vpnEl.style.color = "";
      }
    }

    if (data.connection_info) {
      const ci = data.connection_info;
      const repEl = document.getElementById("conn-info-repeater");
      if (repEl && ci.repeater_ip) repEl.textContent = ci.repeater_ip;

      const usvEl = document.getElementById("conn-info-usv");
      if (usvEl && ci.usv_target) usvEl.textContent = ci.usv_target;

      const rtrEl = document.getElementById("conn-info-router");
      if (rtrEl && ci.router_ip) rtrEl.textContent = ci.router_ip;

      const gpsPortEl = document.getElementById("conn-info-gps-port");
      if (gpsPortEl && ci.gps_udp_port) gpsPortEl.textContent = `UDP ${ci.gps_udp_port}`;
    }
  }

  focusRadio(rid) {
    if (this.radioMarkers[rid]) {
      this.map.setView(this.radioMarkers[rid].getLatLng(), 17, { animate: true });
      this.radioMarkers[rid].openPopup();
    } else {
      const radio = this.radios[rid];
      if (radio) {
        this.updateRadioMarker(radio);
        if (this.radioMarkers[rid]) {
          this.map.setView(this.radioMarkers[rid].getLatLng(), 17, { animate: true });
          this.radioMarkers[rid].openPopup();
        }
      }
    }
  }

  filterSidebarRadios(query) {
    this.updateSidebarRadios();
  }

  // ── Timeline & Replay ──
  initTimeline() {
    const container = document.getElementById("visualization-timeline");
    if (!container || !window.vis) return;

    this.timelineDataset = new vis.DataSet();
    const options = {
      height: "140px",
      start: new Date(Date.now() - 3600 * 1000),
      end: new Date(Date.now() + 600 * 1000),
      editable: false,
      zoomable: true,
      moveable: true,
      locale: "de"
    };

    this.timeline = new vis.Timeline(container, this.timelineDataset, options);
    this.timeline.on("select", (props) => {
      if (props.items.length > 0) {
        const item = this.timelineDataset.get(props.items[0]);
        if (item && item.audio_url) {
          this.playAudio(item.audio_url, item.content);
        }
      }
    });

    this.loadTimelineData();
  }

  async loadTimelineData() {
    try {
      const resp = await fetch("/api/timeline");
      const data = await resp.json();
      if (this.timelineDataset && data.items) {
        this.timelineDataset.clear();
        this.timelineDataset.add(data.items);
      }
    } catch (e) {
      console.warn("Timeline Daten laden fehlgeschlagen:", e);
    }
  }

  jumpReplay(seconds) {
    const targetMs = Date.now() + seconds * 1000;
    this.startReplayAt(new Date(targetMs).toISOString());
  }

  async startReplayAt(isoTimestamp) {
    this.isReplay = true;
    this.replayTimestamp = isoTimestamp;
    document.getElementById("tl-mode-badge").className = "badge-replay";
    document.getElementById("tl-mode-badge").textContent = "⏪ HISTORIE (REPLAY)";
    document.getElementById("tl-current-time-display").textContent = new Date(isoTimestamp).toLocaleTimeString();

    try {
      const resp = await fetch(`/api/history?timestamp=${encodeURIComponent(isoTimestamp)}`);
      const state = await resp.json();
      state.radios.forEach(r => this.updateRadioMarker(r));
    } catch (e) {
      console.error("Replay Fehler:", e);
    }
  }

  toggleReplayPlayback() {
    const btn = document.getElementById("btn-replay-play");
    if (!this.isReplayPlaying) {
      this.isReplayPlaying = true;
      if (btn) {
        btn.textContent = "⏸ PAUSE";
        btn.classList.add("btn-active");
      }
      this.replayTimer = setInterval(() => {
        const speed = parseInt(document.getElementById("select-replay-speed")?.value || "5", 10);
        this.jumpReplay(speed);
      }, 1000);
    } else {
      this.isReplayPlaying = false;
      if (this.replayTimer) {
        clearInterval(this.replayTimer);
        this.replayTimer = null;
      }
      if (btn) {
        btn.textContent = "▶ PLAY";
        btn.classList.remove("btn-active");
      }
    }
  }

  returnToLive() {
    this.isReplay = false;
    if (this.replayTimer) {
      clearInterval(this.replayTimer);
      this.replayTimer = null;
      this.isReplayPlaying = false;
      const btn = document.getElementById("btn-replay-play");
      if (btn) {
        btn.textContent = "▶ PLAY";
        btn.classList.remove("btn-active");
      }
    }
    document.getElementById("tl-mode-badge").className = "badge-live";
    document.getElementById("tl-mode-badge").textContent = "🔴 ECHTZEIT (LIVE)";
    document.getElementById("tl-current-time-display").textContent = new Date().toLocaleTimeString();
    Object.values(this.radios).forEach(r => this.updateRadioMarker(r));
  }

  // ── Keyboard Shortcuts ──
  initKeyboardShortcuts() {
    window.addEventListener("keydown", (e) => {
      if (["input", "textarea", "select"].includes(e.target.tagName.toLowerCase())) return;

      if (e.code === "Space") {
        e.preventDefault();
        this.map.setView(this.initialMapCenter, this.initialMapZoom, { animate: true });
      } else if (e.code === "KeyR") {
        e.preventDefault();
        this.rotateMap(e.shiftKey ? -15 : 15);
      } else if (e.code === "KeyN") {
        e.preventDefault();
        if (e.shiftKey) {
          this.resetMapRotation();
        } else {
          this.startMarkerMode();
        }
      } else if (e.code === "KeyL") {
        e.preventDefault();
        this.returnToLive();
      } else if (e.code === "KeyS") {
        e.preventDefault();
        document.getElementById("hud-sidebar").classList.toggle("sidebar-hidden");
        setTimeout(() => this.map.invalidateSize(), 200);
      } else if (e.code === "KeyT") {
        e.preventDefault();
        document.getElementById("hud-timeline-dock").classList.toggle("timeline-hidden");
        setTimeout(() => this.map.invalidateSize(), 200);
      } else if (e.code === "Escape") {
        if (this.isMeasuring) this.toggleMeasureTool();
        if (this.isCalibrating) this.stopGeorefCalibration();
        this.closeMarkerModal();
        this.closeGeorefModal();
        this.closeGeofenceModal();
        this.closeSettingsModal();
        this.closeInfraModal();
        this.closeMissionModal();
        this.closeRadiosModal();
      }
    });
  }

  startClock() {
    const el = document.getElementById("clock-utc");
    setInterval(() => {
      const now = new Date();
      if (el) el.textContent = now.toISOString().substring(11, 19);
      if (!this.isReplay) {
        const tlEl = document.getElementById("tl-current-time-display");
        if (tlEl) tlEl.textContent = now.toLocaleTimeString();
      }
    }, 1000);
  }
}

// Global instanziieren
window.addEventListener("DOMContentLoaded", () => {
  window.app = new TacticalApp();
  window.app.init();
});
