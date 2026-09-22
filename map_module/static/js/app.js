/**
 * Hytera HR1065 // Taktisches Lagezentrum & Tactical Timeline
 * Frontend Controller (Vollständige Implementierung mit Business Casual & Taktischem Modus)
 * Verwaltet Leaflet-Karte, vis.js Timeline, WebSockets, Funktions-Toggles,
 * No-GPS Funkgeräte-Platzierung, Theme-Umschaltung und Infrastruktur-Überwachung (Repeater/USV/LTE).
 */

// ── Globale Anwendungszustände ──────────────────────────────
const State = {
  isLive: true,
  theme: "business", // "business" oder "tactical"
  mapRotation: 0,
  trackUpActive: false,

  // Zentrale Funktions-Toggles
  settings: {
    gps_tracking_enabled: true,
    show_no_gps_units: true,
    allow_manual_positioning: true,
    overlay_visible: true,
    overlay_opacity: 0.85,
    ptt_pulse_enabled: true,
    auto_pan_ptt: false,
    breadcrumbs_enabled: true,
    audio_enabled: true,
    markers_visible: true,
    unit_labels_visible: true,
    timeline_visible: true,
    mock_active: true
  },

  // Manuelle Funkgeräte-Platzierung
  placementMode: {
    active: false,
    radio_id: null,
    alias: ""
  },
  
  // Georeferenzierung & Kalibrierung von Lageplänen (PDF / Bild)
  georef: {
    active: false,
    imageUrl: null,
    bounds: null,
    rotation: 0,
    tempLayer: null,
    handles: []
  },
  recentCalls: [],
  searchFilter: "",

  // Daten
  radios: {},        // radio_id -> { alias, model, has_gps, marker, trail, last_pos, ... }
  markers: {},       // marker_id -> { data, leaflet_marker }
  infrastructure: null,
  
  timeline: null,
  timelineItems: null,
  timelineGroups: null,
  
  overlayLayer: null,
  baseTileLayer: null,
  map: null,
  ws: null,
  wsReconnectTimer: null,
  
  audioContext: null
};

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

// Standard-Farben und Symbole für Marker-Typen
const TYPE_ICONS = {
  incident: "⚠️",
  hazard: "☣️",
  checkpoint: "🛡️",
  medical: "🚑",
  roadblock: "⛔",
  base: "🏢",
  info: "ℹ️"
};

const PRIO_COLORS = {
  critical: "#ef4444",
  high: "#f97316",
  normal: "#f59e0b",
  low: "#38bdf8"
};


// ── Initialisierung beim Laden der Seite ────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  initTheme();
  initClock();
  initUIListeners();
  initSearchBox();
  initShortcuts();
  initMap();
  initTimeline();
  await loadInitialState();
  initWebSocket();
  initRecentCalls();
});


// ── 1. Theme-Steuerung (Business Casual <--> Taktisch Dark) ─
function initTheme() {
  const savedTheme = localStorage.getItem("hytera_map_theme") || "business";
  setTheme(savedTheme);
}

function setTheme(theme) {
  State.theme = theme;
  localStorage.setItem("hytera_map_theme", theme);
  document.body.className = theme === "business" ? "theme-business" : "theme-tactical";

  const themeIcon = document.getElementById("theme-icon");
  const themeLabel = document.getElementById("theme-label");

  if (theme === "business") {
    if (themeIcon) themeIcon.textContent = "💼";
    if (themeLabel) themeLabel.textContent = "BUSINESS CASUAL";
  } else {
    if (themeIcon) themeIcon.textContent = "📡";
    if (themeLabel) themeLabel.textContent = "TAKTISCH DARK";
  }
}

function toggleTheme() {
  setTheme(State.theme === "business" ? "tactical" : "business");
}


// ── 2. Uhrzeit & Header-HUD ────────────────────────────────
function initClock() {
  const clockEl = document.getElementById("clock-utc");
  setInterval(() => {
    const now = new Date();
    const iso = now.toISOString().substring(11, 19);
    if (clockEl) clockEl.textContent = `${iso} UTC`;
  }, 1000);
}


// ── 3. Leaflet Kartensteuerung ──────────────────────────────
function initMap() {
  State.map = L.map("map", {
    center: [51.2325, 6.7800],
    zoom: 15,
    zoomControl: false,
    attributionControl: false
  });

  L.control.zoom({ position: "topright" }).addTo(State.map);

  // Basiskarte (CartoDB Dark)
  State.baseTileLayer = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    subdomains: "abcd",
    maxZoom: 19,
    opacity: 0.95
  }).addTo(State.map);

  // Koordinatenanzeige bei Mausbewegung
  const coordsHud = document.getElementById("val-mouse-coords");
  State.map.on("mousemove", (e) => {
    if (coordsHud) {
      const latStr = Math.abs(e.latlng.lat).toFixed(6) + "° " + (e.latlng.lat >= 0 ? "N" : "S");
      const lonStr = Math.abs(e.latlng.lng).toFixed(6) + "° " + (e.latlng.lng >= 0 ? "E" : "W");
      coordsHud.textContent = `${latStr}, ${lonStr}`;
    }
  });

  // Rechtsklick auf die Karte -> Vorfalls-Modal
  State.map.on("contextmenu", (e) => {
    openIncidentModal(e.latlng.lat, e.latlng.lng);
  });

  // Linksklick auf die Karte: Vorfallsmarker ODER Standortzuweisung für No-GPS Funkgerät
  State.map.on("click", (e) => {
    if (State.placementMode.active) {
      assignRadioPosition(State.placementMode.radio_id, e.latlng.lat, e.latlng.lng);
      cancelPlacementMode();
    } else if (State.settings.allow_manual_positioning && document.getElementById("btn-add-marker-mode")?.classList.contains("btn-danger")) {
      openIncidentModal(e.latlng.lat, e.latlng.lng);
      setAddMarkerMode(false);
    }
  });

  // Korrektur für Mausklicks bei rotierter Karte (Präzise Klick-Erkennung ohne Verzerrung)
  const origMouseEventToContainerPoint = State.map.mouseEventToContainerPoint;
  State.map.mouseEventToContainerPoint = function(e) {
    const pt = origMouseEventToContainerPoint.call(this, e);
    if (!State.mapRotation || State.mapRotation === 0) return pt;

    const rect = this._container.getBoundingClientRect();
    const cx = rect.width / 2;
    const cy = rect.height / 2;

    const dx = pt.x - cx;
    const dy = pt.y - cy;
    const rad = -State.mapRotation * Math.PI / 180;

    const rx = cx + dx * Math.cos(rad) - dy * Math.sin(rad);
    const ry = cy + dx * Math.sin(rad) + dy * Math.cos(rad);

    return L.point(rx, ry);
  };
}

function setMapRotation(deg) {
  deg = (deg % 360 + 360) % 360;
  State.mapRotation = deg;

  const mapPane = document.querySelector("#map .leaflet-map-pane");
  if (mapPane) {
    mapPane.style.transformOrigin = "center center";
    if (deg === 0) {
      mapPane.style.transform = "";
      mapPane.style.rotate = "";
    } else {
      mapPane.style.transform = `rotate(${deg}deg)`;
      mapPane.style.rotate = `${deg}deg`;
    }
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

  // Marker-Beschriftungen & Popups entgegenrotieren für Lesbarkeit
  const counterDeg = -deg;
  document.querySelectorAll("#map .radio-marker-tag, #map .t-popup, #map .incident-marker-body").forEach(el => {
    el.style.transform = `rotate(${counterDeg}deg)`;
  });
}

function rotateMap(delta) {
  setMapRotation(State.mapRotation + delta);
}

function resetMapRotation() {
  setMapRotation(0);
}

function toggleTrackUp() {
  State.trackUpActive = !State.trackUpActive;
  const btn = document.getElementById("btn-trackup-toggle");
  if (btn) {
    btn.textContent = `🧭 TRACK-UP: ${State.trackUpActive ? 'AN' : 'AUS'}`;
    if (State.trackUpActive) btn.classList.add("btn-active");
    else btn.classList.remove("btn-active");
  }
}

function updateOverlayOpacity(op) {
  op = Math.max(0.05, Math.min(1.0, op));
  State.settings.overlay_opacity = op;

  const slider1 = document.getElementById("overlay-opacity-slider");
  const val1 = document.getElementById("overlay-opacity-val");
  if (slider1) slider1.value = Math.round(op * 100);
  if (val1) val1.textContent = `${Math.round(op * 100)}%`;

  const slider2 = document.getElementById("georef-opacity-slider");
  const val2 = document.getElementById("georef-opacity-val");
  if (slider2) slider2.value = Math.round(op * 100);
  if (val2) val2.textContent = `${Math.round(op * 100)}%`;

  if (State.overlayLayer) State.overlayLayer.setOpacity(op);
  if (State.georef && State.georef.tempLayer) State.georef.tempLayer.setOpacity(op);
}

function applyOverlayRotation(deg, targetLayer = null) {
  deg = (deg % 360 + 360) % 360;
  const layers = targetLayer ? [targetLayer] : [State.overlayLayer, State.georef && State.georef.tempLayer].filter(Boolean);
  
  layers.forEach(layer => {
    layer._rotationDeg = deg;
    const img = typeof layer.getElement === "function" ? layer.getElement() : layer._image;
    if (img) {
      img.style.transformOrigin = "center center";
      img.style.rotate = `${deg}deg`;
    }
  });
}

function setupImageOverlay(overlayConfig) {
  if (State.overlayLayer) {
    State.map.removeLayer(State.overlayLayer);
    State.overlayLayer = null;
  }

  if (!overlayConfig || !overlayConfig.bounds) return;

  const imageUrl = overlayConfig.image_url || "/static/assets/tactical_overlay.png";
  const bounds = overlayConfig.bounds;
  const opacity = State.settings.overlay_opacity !== undefined ? State.settings.overlay_opacity : 0.85;
  const rotation = overlayConfig.rotation || 0;

  State.overlayLayer = L.imageOverlay(imageUrl, bounds, {
    opacity: opacity,
    interactive: false,
    zIndex: 400
  });

  if (State.settings.overlay_visible) {
    State.overlayLayer.addTo(State.map);
    applyOverlayRotation(rotation, State.overlayLayer);
    updateOverlayOpacity(opacity);
  }
}


// ── 4. Funkgeräte-Marker (mit GPS und ohne GPS) ─────────────
function getOrCreateRadioMarker(radioId, alias, lat, lon, heading = 0, hasGps = true) {
  if (State.radios[radioId] && State.radios[radioId].marker) {
    return State.radios[radioId].marker;
  }

  const isNoGps = !hasGps;
  const iconHtml = `
    <div id="radio-marker-${radioId}" class="radio-marker-container">
      <div class="radio-radar-ring"></div>
      ${hasGps ? `<div class="radio-marker-arrow" style="transform: rotate(${heading}deg);"></div>` : ''}
      <div class="radio-marker-body ${isNoGps ? 'no-gps-radio' : ''}">
        <span>${isNoGps ? '📻' : '📡'}</span>
      </div>
      <div class="radio-marker-tag" style="display: ${State.settings.unit_labels_visible ? 'block' : 'none'};">
        ${escapeHtml(alias || 'Radio ' + radioId)} ${isNoGps ? '(MANUELL)' : ''}
      </div>
    </div>
  `;

  const customIcon = L.divIcon({
    className: "radio-div-icon",
    iconSize: [44, 44],
    iconAnchor: [22, 22],
    popupAnchor: [0, -22],
    html: iconHtml
  });

  const marker = L.marker([lat, lon], { icon: customIcon });

  marker.bindPopup(() => {
    const r = State.radios[radioId] || {};
    return `
      <div class="t-popup">
        <div class="t-popup-title">${r.has_gps ? '📡' : '📻'} ${escapeHtml(r.alias || 'Radio ' + radioId)}</div>
        <div class="t-popup-body">
          <div><b>DMR ID:</b> ${radioId}</div>
          <div><b>Gerät:</b> ${escapeHtml(r.device_model || 'Hytera')}</div>
          <div><b>GPS-Status:</b> ${r.has_gps ? '<span style="color:#10b981;">Aktiv (GNSS)</span>' : '<span style="color:#f59e0b;">Kein GPS (Stationär / Handgerät)</span>'}</div>
          <div><b>Position:</b> ${lat.toFixed(6)}, ${lon.toFixed(6)}</div>
          ${r.has_gps ? `<div><b>Geschwindigkeit:</b> ${(r.last_speed || 0).toFixed(1)} km/h</div>` : ''}
          <div><b>Zuletzt aktiv:</b> ${new Date().toLocaleTimeString()}</div>
        </div>
      </div>
    `;
  });

  marker.on("click", () => {
    State.map.panTo([lat, lon]);
  });

  // Breadcrumbs nur für Einheiten mit GPS
  let trail = null;
  if (hasGps) {
    trail = L.polyline([[lat, lon]], {
      color: "#38bdf8",
      weight: 2,
      opacity: 0.6,
      dashArray: "3, 6"
    });
    if (State.settings.breadcrumbs_enabled && State.settings.gps_tracking_enabled) {
      trail.addTo(State.map);
    }
  }

  // Auf Karte einblenden (falls GPS-Tracking an oder No-GPS-Gerät mit manueller Position)
  if ((hasGps && State.settings.gps_tracking_enabled) || (!hasGps && State.settings.show_no_gps_units)) {
    marker.addTo(State.map);
  }

  State.radios[radioId] = {
    radio_id: radioId,
    alias: alias || `Radio ${radioId}`,
    has_gps: hasGps,
    device_model: "",
    marker: marker,
    trail: trail,
    trailCoords: [[lat, lon]],
    last_pos: [lat, lon],
    last_speed: 0,
    last_heading: heading
  };

  updateSidebarRadios();
  return marker;
}

function updateRadioPosition(data) {
  const rid = data.radio_id;
  const alias = data.alias || (State.radios[rid] ? State.radios[rid].alias : `Radio ${rid}`);
  const lat = data.lat;
  const lon = data.lon;
  const heading = data.heading || 0;
  const speed = data.speed || 0;
  const hasGps = data.has_gps !== undefined ? data.has_gps : (State.radios[rid] ? State.radios[rid].has_gps : true);

  let radioObj = State.radios[rid];
  if (!radioObj || !radioObj.marker) {
    getOrCreateRadioMarker(rid, alias, lat, lon, heading, hasGps);
  } else {
    radioObj.has_gps = hasGps;
    radioObj.alias = alias;
    radioObj.last_pos = [lat, lon];
    radioObj.last_speed = speed;
    radioObj.last_heading = heading;

    if (hasGps && State.settings.gps_tracking_enabled) {
      radioObj.marker.setLatLng([lat, lon]);
      if (!State.map.hasLayer(radioObj.marker)) radioObj.marker.addTo(State.map);

      // Track-Up Automatik bei Bewegung
      if (State.trackUpActive && heading && speed > 2.0) {
        setMapRotation(360 - heading);
      }

      const container = document.getElementById(`radio-marker-${rid}`);
      if (container) {
        const arrow = container.querySelector(".radio-marker-arrow");
        if (arrow) arrow.style.transform = `rotate(${heading}deg)`;
        const tag = container.querySelector(".radio-marker-tag");
        if (tag) tag.textContent = alias;
      }

      if (radioObj.trail) {
        radioObj.trailCoords.push([lat, lon]);
        if (radioObj.trailCoords.length > 35) radioObj.trailCoords.shift();
        radioObj.trail.setLatLngs(radioObj.trailCoords);
      }
    }
  }

  updateSidebarRadios();
}

/**
 * PTT-Start Event (Sprachübertragung beginnt)
 */
function handlePTTStart(data) {
  const rid = data.radio_id;
  const alias = data.alias || (State.radios[rid] ? State.radios[rid].alias : `Radio ${rid}`);
  const slot = data.slot || "TS1";
  const callType = data.call_type || "Gruppe";

  // In Call-Log-Ticker einreihen
  addCallLogEntry({
    radio_id: rid,
    alias: alias,
    slot: slot,
    call_type: callType,
    timestamp: data.timestamp || new Date().toISOString(),
    duration_ms: null
  });

  // 1. PTT-Puls auf Karte (falls Schalter aktiv)
  if (State.settings.ptt_pulse_enabled) {
    const container = document.getElementById(`radio-marker-${rid}`);
    if (container) {
      container.classList.remove("radio-ptt-decay");
      container.querySelectorAll(".ptt-decay-ring").forEach(r => r.remove());
      container.classList.add("radio-ptt-active");
    }
  }

  // 2. Auto-Pan auf funkendes Gerät (falls Schalter aktiv)
  if (State.settings.auto_pan_ptt && State.radios[rid] && State.radios[rid].last_pos) {
    State.map.panTo(State.radios[rid].last_pos);
  }

  // 3. Header Alert-Banner
  const banner = document.getElementById("ptt-alert-banner");
  const bannerText = document.getElementById("ptt-banner-text");
  if (banner && bannerText) {
    banner.className = "ptt-banner-active";
    bannerText.textContent = `🔴 FUNK AKTIV: ${alias} | ${callType} | ${slot}`;
  }

  // 4. Sidebar-Kachel hervorheben
  const card = document.getElementById(`unit-card-${rid}`);
  if (card) card.classList.add("active-tx");

  // 5. Akustischer Funkton
  if (State.settings.audio_enabled) {
    playTacticalChirp();
  }
}

function handlePTTEnd(data) {
  const rid = data.radio_id;

  const container = document.getElementById(`radio-marker-${rid}`);
  if (container) {
    container.classList.remove("radio-ptt-active");

    // 12-Sekunden PTT-Nachleuchten (Optischer Decay)
    container.classList.add("radio-ptt-decay");
    const decayRing = document.createElement("div");
    decayRing.className = "ptt-decay-ring";
    container.appendChild(decayRing);

    setTimeout(() => {
      container.classList.remove("radio-ptt-decay");
      decayRing.remove();
    }, 12000);
  }

  // Letzten Call-Log Eintrag mit Dauer aktualisieren
  if (State.recentCalls && State.recentCalls.length > 0) {
    const latest = State.recentCalls[0];
    if (latest.radio_id === rid && latest.duration_ms === null) {
      latest.duration_ms = data.duration_ms || 2500;
      renderCallLog();
    }
  }

  const banner = document.getElementById("ptt-alert-banner");
  const bannerText = document.getElementById("ptt-banner-text");
  if (banner && bannerText) {
    banner.className = "ptt-banner-idle";
    bannerText.textContent = "FUNKBEREITSCHAFT IDLE";
  }

  const card = document.getElementById(`unit-card-${rid}`);
  if (card) card.classList.remove("active-tx");

  // Zur Timeline hinzufügen
  if (State.timelineItems) {
    const startIso = data.timestamp || new Date().toISOString();
    const durationSec = (data.duration_ms || 2500) / 1000.0;
    const dtEnd = new Date(new Date(startIso).getTime() + durationSec * 1000).toISOString();

    State.timelineItems.add({
      id: "ptt_live_" + Date.now(),
      group: `radio_${rid}`,
      content: `🎙️ ${data.alias || 'Radio ' + rid} (${data.slot || 'TS1'})`,
      start: startIso,
      end: dtEnd,
      type: "range",
      className: `timeline-ptt slot-${(data.slot || 'ts1').toLowerCase()}`,
      title: `PTT Funk: ${data.alias}\nDauer: ${data.duration_ms}ms`,
      radio_id: rid
    });
  }
}


// ── 5. Manuelle Platzierung von No-GPS Funkgeräten ──────────
function startPlacementMode(radioId, alias) {
  State.placementMode.active = true;
  State.placementMode.radio_id = radioId;
  State.placementMode.alias = alias;

  const banner = document.getElementById("placement-banner");
  const text = document.getElementById("placement-text");
  if (banner) banner.classList.remove("placement-banner-hidden");
  if (text) text.textContent = `📍 Klicken Sie auf die Karte, um den Standort für "${alias}" (ID ${radioId}) festzulegen.`;

  const mapEl = document.getElementById("map");
  if (mapEl) mapEl.style.cursor = "crosshair";
}

function cancelPlacementMode() {
  State.placementMode.active = false;
  State.placementMode.radio_id = null;
  State.placementMode.alias = "";

  const banner = document.getElementById("placement-banner");
  if (banner) banner.classList.add("placement-banner-hidden");

  const mapEl = document.getElementById("map");
  if (mapEl) mapEl.style.cursor = "";
}

async function assignRadioPosition(radioId, lat, lon) {
  try {
    const res = await fetch(`/api/radios/${radioId}/position`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat, lon })
    });
    if (res.ok) {
      const radioObj = State.radios[radioId];
      if (radioObj && radioObj.marker) {
        radioObj.marker.setLatLng([lat, lon]);
        if (!State.map.hasLayer(radioObj.marker)) radioObj.marker.addTo(State.map);
      } else {
        const alias = radioObj ? radioObj.alias : `Radio ${radioId}`;
        getOrCreateRadioMarker(radioId, alias, lat, lon, 0, false);
      }
    }
  } catch (err) {
    console.error("Fehler beim Zuweisen der Funkgeräte-Position:", err);
  }
}


// ── 6. Taktische Einsatzmarker ──────────────────────────────
function addIncidentMarkerToMap(m) {
  if (State.markers[m.id]) return;

  const iconSymbol = TYPE_ICONS[m.typ] || "📍";
  const prioClass = `prio-${(m.prioritaet || 'normal').toLowerCase()}`;

  const customIcon = L.divIcon({
    className: "incident-div-icon",
    iconSize: [30, 30],
    iconAnchor: [15, 15],
    popupAnchor: [0, -15],
    html: `
      <div id="marker-icon-${m.id}" class="incident-marker-body ${prioClass}">
        <span>${iconSymbol}</span>
      </div>
    `
  });

  const marker = L.marker([m.lat, m.lon], { icon: customIcon });

  marker.bindPopup(`
    <div class="t-popup">
      <div class="t-popup-title">${iconSymbol} [${m.typ.toUpperCase()}] // ${m.prioritaet.toUpperCase()}</div>
      <div class="t-popup-body">
        <div><b>Beschreibung:</b> ${escapeHtml(m.beschreibung)}</div>
        <div><b>Erfasser:</b> ${escapeHtml(m.author || 'Operator')}</div>
        <div><b>Zeitstempel:</b> ${new Date(m.timestamp).toLocaleString()}</div>
        <div><b>Position:</b> ${m.lat.toFixed(6)}, ${m.lon.toFixed(6)}</div>
      </div>
      <button class="btn btn-danger t-popup-del-btn" onclick="deleteIncidentMarker(${m.id})">❌ VORFALL LÖSCHEN</button>
    </div>
  `);

  if (State.settings.markers_visible) {
    marker.addTo(State.map);
  }

  State.markers[m.id] = {
    data: m,
    leaflet_marker: marker
  };

  updateSidebarMarkers();
}

function removeIncidentMarkerFromMap(markerId) {
  if (State.markers[markerId]) {
    State.map.removeLayer(State.markers[markerId].leaflet_marker);
    delete State.markers[markerId];
    updateSidebarMarkers();
  }
  if (State.timelineItems) {
    State.timelineItems.remove(`marker_${markerId}`);
  }
}

async function deleteIncidentMarker(markerId) {
  try {
    const res = await fetch(`/api/markers/${markerId}`, { method: "DELETE" });
    if (res.ok) {
      removeIncidentMarkerFromMap(markerId);
    }
  } catch (err) {
    console.error("Fehler beim Löschen des Markers:", err);
  }
}


// ── 7. vis.js Timeline Integration ──────────────────────────
function initTimeline() {
  const container = document.getElementById("visualization-timeline");
  if (!container) return;

  State.timelineItems = new vis.DataSet([]);
  State.timelineGroups = new vis.DataSet([
    { id: "markers", content: "📍 VORFÄLLE", className: "tl-group-markers" }
  ]);

  const now = new Date();
  const options = {
    start: new Date(now.getTime() - 30 * 60 * 1000),
    end: new Date(now.getTime() + 10 * 60 * 1000),
    zoomMin: 1000 * 10,
    zoomMax: 1000 * 60 * 60 * 24,
    stack: true,
    editable: false,
    horizontalScroll: true,
    orientation: "bottom",
    showCurrentTime: true,
    format: {
      minorLabels: { minute: "HH:mm", second: "HH:mm:ss" }
    }
  };

  State.timeline = new vis.Timeline(container, State.timelineItems, State.timelineGroups, options);
  State.timeline.addCustomTime(now, "replay-slider");

  State.timeline.on("timechange", (properties) => {
    if (properties.id === "replay-slider") {
      const selectedTime = properties.time;
      const nowTime = new Date();
      if (nowTime.getTime() - selectedTime.getTime() > 10000) {
        enterReplayMode(selectedTime);
      } else {
        exitReplayMode();
      }
    }
  });

  State.timeline.on("select", (properties) => {
    if (!properties.items || properties.items.length === 0) return;
    const item = State.timelineItems.get(properties.items[0]);
    if (!item) return;

    if (item.lat && item.lon) {
      State.map.flyTo([item.lat, item.lon], 17);
      if (item.marker_id && State.markers[item.marker_id]) {
        State.markers[item.marker_id].leaflet_marker.openPopup();
      }
    } else if (item.radio_id && State.radios[item.radio_id]) {
      const r = State.radios[item.radio_id];
      if (r.last_pos) {
        State.map.flyTo(r.last_pos, 17);
        r.marker.openPopup();
      }
    }
  });
}

function registerRadioTimelineGroup(radioId, alias) {
  if (!State.timelineGroups) return;
  const groupId = `radio_${radioId}`;
  if (!State.timelineGroups.get(groupId)) {
    State.timelineGroups.add({
      id: groupId,
      content: `📻 ${alias || 'Radio ' + radioId}`,
      className: "tl-group-radio"
    });
  }
}


// ── 8. Replay / Zeitreise-Modus ─────────────────────────────
async function enterReplayMode(targetDate) {
  State.isLive = false;

  const connDot = document.getElementById("conn-dot");
  const connText = document.getElementById("conn-status-text");
  const btnLive = document.getElementById("btn-live-toggle");

  if (connDot) connDot.className = "dot-replay";
  if (connText) connText.textContent = "REPLAY-MODUS";
  if (btnLive) btnLive.classList.remove("btn-active");

  const banner = document.getElementById("replay-banner");
  const timeLabel = document.getElementById("replay-target-time");
  if (banner) banner.classList.remove("replay-banner-hidden");
  if (timeLabel) timeLabel.textContent = targetDate.toISOString().replace("T", " ").substring(0, 19) + " UTC";

  try {
    const isoTs = targetDate.toISOString();
    const res = await fetch(`/api/history?timestamp=${encodeURIComponent(isoTs)}`);
    if (res.ok) {
      const data = await res.json();
      applyHistoricalState(data);
    }
  } catch (err) {
    console.error("Fehler beim Replay-Laden:", err);
  }
}

function applyHistoricalState(histState) {
  if (histState.radios) {
    histState.radios.forEach(r => {
      const rid = r.radio_id;
      if (State.radios[rid] && State.radios[rid].marker) {
        State.radios[rid].marker.setLatLng([r.lat, r.lon]);
      } else {
        getOrCreateRadioMarker(rid, r.alias, r.lat, r.lon, r.heading || 0, r.has_gps !== 0);
      }
    });
  }
}

function exitReplayMode() {
  State.isLive = true;

  const connDot = document.getElementById("conn-dot");
  const connText = document.getElementById("conn-status-text");
  const btnLive = document.getElementById("btn-live-toggle");
  const banner = document.getElementById("replay-banner");

  if (connDot) connDot.className = "dot-online";
  if (connText) connText.textContent = "LIVE";
  if (btnLive) btnLive.classList.add("btn-active");
  if (banner) banner.classList.add("replay-banner-hidden");

  if (State.timeline) {
    State.timeline.setCustomTime(new Date(), "replay-slider");
  }

  loadInitialState();
}


// ── 9. Infrastruktur-Überwachung (Repeater, USV, LTE) ───────
function updateInfrastructureUI(data) {
  if (!data) return;
  State.infrastructure = data;

  const rpt = data.repeater || {};
  const usv = data.usv || {};
  const router = data.router || {};

  // 1. Header-Pills aktualisieren mit proaktiven Alarmschwellen
  const rptSummary = document.getElementById("stat-repeater-summary");
  const pillRpt = document.getElementById("pill-repeater");
  const isRptAlarm = (rpt.vswr && rpt.vswr > 1.8) || (rpt.temp_c && rpt.temp_c > 60);

  if (pillRpt) {
    if (isRptAlarm) {
      pillRpt.classList.add("pill-alarm-pulse");
      if (rptSummary) rptSummary.textContent = `🚨 VSWR ${rpt.vswr} | ${rpt.temp_c}°C`;
    } else {
      pillRpt.classList.remove("pill-alarm-pulse");
      if (rptSummary) rptSummary.textContent = `${rpt.temp_c || 42}°C | ${rpt.fw_pwr_w || 25}W`;
    }
  }

  const usvSummary = document.getElementById("stat-usv-summary");
  const pillUsv = document.getElementById("pill-usv");
  const isUsvOnBattery = usv.netz_status && usv.netz_status.toLowerCase().includes("batterie");
  const isUsvLowBatt = (usv.batterie_pct != null && usv.batterie_pct < 50);

  if (pillUsv) {
    if (isUsvOnBattery || isUsvLowBatt) {
      pillUsv.classList.add("pill-alarm-pulse");
      if (usvSummary) usvSummary.textContent = `⚠️ BATT ${usv.batterie_pct || '?'}% (${usv.batterie_min || '?'}m)`;
    } else {
      pillUsv.classList.remove("pill-alarm-pulse");
      if (usvSummary) usvSummary.textContent = `${usv.batterie_pct || 98}% (${usv.netz_status ? 'Netz' : 'Batt'})`;
    }
  }

  const routerSummary = document.getElementById("stat-router-summary");
  if (routerSummary) routerSummary.textContent = `${router.netz_typ || '4G'} ${router.provider ? router.provider.split(" ")[0] : 'Telekom'}`;

  // 2. Modal-Felder aktualisieren falls geöffnet
  document.getElementById("rpt-ip") && (document.getElementById("rpt-ip").textContent = rpt.ip || "192.168.1.100");
  document.getElementById("rpt-fwd-pwr") && (document.getElementById("rpt-fwd-pwr").textContent = `${rpt.fw_pwr_w || 25.0} W`);
  document.getElementById("rpt-rfl-pwr") && (document.getElementById("rpt-rfl-pwr").textContent = `${rpt.rw_pwr_w || 0.6} W`);
  document.getElementById("rpt-vswr") && (document.getElementById("rpt-vswr").textContent = `${rpt.vswr || 1.15} (GUT)`);
  document.getElementById("rpt-temp") && (document.getElementById("rpt-temp").textContent = `${rpt.temp_c || 42.4} °C`);
  document.getElementById("rpt-fan") && (document.getElementById("rpt-fan").textContent = `${rpt.fan_rpm || 2400} RPM`);
  document.getElementById("rpt-volt") && (document.getElementById("rpt-volt").textContent = `${rpt.volt_v || 13.8} V DC`);
  document.getElementById("rpt-rssi-ts1") && (document.getElementById("rpt-rssi-ts1").textContent = `${rpt.rssi_ts1 || -88} dBm`);
  document.getElementById("rpt-rssi-ts2") && (document.getElementById("rpt-rssi-ts2").textContent = `${rpt.rssi_ts2 || -94} dBm`);

  // USV
  document.getElementById("usv-ip") && (document.getElementById("usv-ip").textContent = `${usv.ip || '192.168.1.102'}:502`);
  document.getElementById("usv-mains-status") && (document.getElementById("usv-mains-status").textContent = usv.netz_status || "Normal (Netzbetrieb)");
  document.getElementById("usv-batt-pct") && (document.getElementById("usv-batt-pct").textContent = `${usv.batterie_pct || 98} %`);
  document.getElementById("usv-battery-fill") && (document.getElementById("usv-battery-fill").style.width = `${usv.batterie_pct || 98}%`);
  document.getElementById("usv-batt-min") && (document.getElementById("usv-batt-min").textContent = `${usv.batterie_min || 145} Minuten`);
  document.getElementById("usv-in-volt") && (document.getElementById("usv-in-volt").textContent = `${usv.eingang_volt || 231.4} V / ${usv.eingang_hz || 50.0} Hz`);
  document.getElementById("usv-out-volt") && (document.getElementById("usv-out-volt").textContent = `${usv.ausgang_volt || 230.1} V`);
  document.getElementById("usv-load") && (document.getElementById("usv-load").textContent = `${usv.last_pct || 28.5} %`);
  document.getElementById("usv-temp") && (document.getElementById("usv-temp").textContent = `${usv.temperatur || 27.2} °C`);

  // Router
  document.getElementById("router-provider") && (document.getElementById("router-provider").textContent = router.provider || "Deutsche Telekom");
  document.getElementById("router-net-type") && (document.getElementById("router-net-type").textContent = router.netz_typ || "4G LTE-CA");
  document.getElementById("router-signal-bars") && (document.getElementById("router-signal-bars").textContent = `📶 ${router.signal_bars || 4} / 5 Balken`);
  document.getElementById("router-rsrp") && (document.getElementById("router-rsrp").textContent = `${router.rsrp_dbm || -84} dBm`);
  document.getElementById("router-sinr") && (document.getElementById("router-sinr").textContent = `${router.sinr_db || 16.5} dB`);
  document.getElementById("router-wan-ip") && (document.getElementById("router-wan-ip").textContent = router.wan_ip || "10.142.88.19");
  document.getElementById("router-throughput") && (document.getElementById("router-throughput").textContent = `⬇️ ${router.rx_mbps || 12.8} Mbps | ⬆️ ${router.tx_mbps || 3.4} Mbps`);
  document.getElementById("router-ping") && (document.getElementById("router-ping").textContent = `${router.ping_ms || 28} ms`);
}


// ── 10. Initialer Datenabruf & Synchronisation ──────────────
async function loadInitialState() {
  try {
    const res = await fetch("/api/initial-state");
    if (!res.ok) throw new Error("HTTP Fehler " + res.status);
    const data = await res.json();

    // Settings synchronisieren
    if (data.settings) {
      Object.assign(State.settings, data.settings);
      applySettingsToUI();
    }

    if (data.overlay) setupImageOverlay(data.overlay);

    if (data.radios) {
      data.radios.forEach(r => {
        State.radios[r.radio_id] = {
          radio_id: r.radio_id,
          alias: r.alias,
          has_gps: r.has_gps !== 0,
          device_model: r.device_model || "",
          fixed_lat: r.fixed_lat,
          fixed_lon: r.fixed_lon,
          last_pos: (r.fixed_lat && r.fixed_lon) ? [r.fixed_lat, r.fixed_lon] : null
        };
        registerRadioTimelineGroup(r.radio_id, r.alias);
      });
    }

    if (data.recent_gps) {
      data.recent_gps.forEach(g => {
        updateRadioPosition(g);
      });
    }

    if (data.markers) {
      data.markers.forEach(m => {
        addIncidentMarkerToMap(m);
      });
    }

    if (data.infrastructure) {
      updateInfrastructureUI(data.infrastructure);
    }

    await loadTimelineEvents();
    updateSidebarRadios();

  } catch (err) {
    console.error("Fehler beim Laden des initialen Zustands:", err);
  }
}

async function loadTimelineEvents() {
  try {
    const res = await fetch("/api/timeline?limit=250");
    if (res.ok) {
      const data = await res.json();
      if (data.items && State.timelineItems) {
        State.timelineItems.clear();
        State.timelineItems.add(data.items);
      }
    }
  } catch (err) {
    console.warn("Konnte Timeline-Events nicht laden:", err);
  }
}


// ── 11. WebSocket Echtzeit-Verbindung ─────────────────────────
function initWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/live`;

  const connDot = document.getElementById("conn-dot");
  const connText = document.getElementById("conn-status-text");

  try {
    State.ws = new WebSocket(wsUrl);

    State.ws.onopen = () => {
      if (connDot) connDot.className = "dot-online";
      if (connText) connText.textContent = "LIVE";
      if (State.wsReconnectTimer) {
        clearTimeout(State.wsReconnectTimer);
        State.wsReconnectTimer = null;
      }
    };

    State.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleIncomingWebSocketMessage(msg);
      } catch (e) {
        console.error("Ungültige WebSocket-Nachricht:", e);
      }
    };

    State.ws.onclose = () => {
      if (connDot) connDot.className = "dot-connecting";
      if (connText) connText.textContent = "VERBINDET...";
      if (!State.wsReconnectTimer) {
        State.wsReconnectTimer = setTimeout(initWebSocket, 3000);
      }
    };

    State.ws.onerror = (err) => {
      console.error("WebSocket Fehler:", err);
    };

  } catch (err) {
    console.error("Konnte WebSocket nicht initialisieren:", err);
  }
}

function handleIncomingWebSocketMessage(msg) {
  if (!State.isLive && (msg.type === "gps_update" || msg.type === "ptt_start" || msg.type === "ptt_end")) {
    return;
  }

  switch (msg.type) {
    case "gps_update":
      updateRadioPosition(msg);
      break;

    case "ptt_start":
      handlePTTStart(msg);
      break;

    case "ptt_end":
      handlePTTEnd(msg);
      break;

    case "telemetry_infrastructure":
      updateInfrastructureUI(msg.data);
      break;

    case "marker_created":
      if (msg.marker) {
        addIncidentMarkerToMap(msg.marker);
        if (State.timelineItems) {
          State.timelineItems.add({
            id: `marker_${msg.marker.id}`,
            group: "markers",
            content: `📍 [${msg.marker.typ.toUpperCase()}] ${msg.marker.beschreibung}`,
            start: msg.marker.timestamp,
            type: "point",
            className: `timeline-marker prio-${msg.marker.prioritaet.toLowerCase()} type-${msg.marker.typ.toLowerCase()}`,
            marker_id: msg.marker.id,
            lat: msg.marker.lat,
            lon: msg.marker.lon
          });
        }
      }
      break;

    case "marker_deleted":
      removeIncidentMarkerFromMap(msg.marker_id);
      break;

    case "radio_position_updated":
      if (State.radios[msg.radio_id]) {
        updateRadioPosition({
          radio_id: msg.radio_id,
          alias: State.radios[msg.radio_id].alias,
          lat: msg.lat,
          lon: msg.lon,
          has_gps: false
        });
      }
      break;

    case "settings_updated":
      if (msg.settings) {
        Object.assign(State.settings, msg.settings);
        applySettingsToUI();
      }
      break;
  }
}


// ── 12. Settings & Toggles Steuerung ────────────────────────
function applySettingsToUI() {
  const s = State.settings;

  // Checkboxes im Einstellungs-Modal synchronisieren
  const setChk = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.checked = !!val;
  };

  setChk("toggle-gps-tracking", s.gps_tracking_enabled);
  setChk("toggle-show-no-gps", s.show_no_gps_units);
  setChk("toggle-ptt-pulse", s.ptt_pulse_enabled);
  setChk("toggle-auto-pan", s.auto_pan_ptt);
  setChk("toggle-overlay", s.overlay_visible);
  setChk("toggle-markers", s.markers_visible);
  setChk("toggle-labels", s.unit_labels_visible);
  setChk("toggle-breadcrumbs", s.breadcrumbs_enabled);
  setChk("toggle-audio", s.audio_enabled);
  setChk("toggle-timeline", s.timeline_visible);
  setChk("toggle-mock", s.mock_active);

  // Schnell-Buttons in der Toolbar synchronisieren
  const btnGps = document.getElementById("btn-gps-toggle");
  if (btnGps) {
    btnGps.textContent = s.gps_tracking_enabled ? "🛰️ GPS: AN" : "🛰️ GPS: AUS";
    btnGps.className = s.gps_tracking_enabled ? "btn btn-active" : "btn";
  }

  const btnPttPulse = document.getElementById("btn-ptt-pulse-toggle");
  if (btnPttPulse) {
    btnPttPulse.textContent = s.ptt_pulse_enabled ? "🎙️ PTT-PULS: AN" : "🎙️ PTT-PULS: AUS";
    btnPttPulse.className = s.ptt_pulse_enabled ? "btn btn-active" : "btn";
  }

  const btnAutoPan = document.getElementById("btn-autopan-toggle");
  if (btnAutoPan) {
    btnAutoPan.textContent = s.auto_pan_ptt ? "🎯 AUTO-PAN: AN" : "🎯 AUTO-PAN: AUS";
    btnAutoPan.className = s.auto_pan_ptt ? "btn btn-active" : "btn";
  }

  const btnTimeline = document.getElementById("btn-timeline-toggle");
  if (btnTimeline) {
    btnTimeline.textContent = s.timeline_visible ? "⏱️ ZEITSTRAHL: AN" : "⏱️ ZEITSTRAHL: AUS";
    btnTimeline.className = s.timeline_visible ? "btn btn-active" : "btn";
  }

  const btnAudio = document.getElementById("btn-audio-toggle");
  if (btnAudio) {
    btnAudio.textContent = s.audio_enabled ? "🔊 AUDIO: AN" : "🔇 AUDIO: STUMM";
    btnAudio.className = s.audio_enabled ? "btn btn-active" : "btn";
  }

  // Wirkungen auf der Karte anwenden
  // 1. GPS Tracking Sichtbarkeit
  for (const r of Object.values(State.radios)) {
    if (r.has_gps) {
      if (s.gps_tracking_enabled) {
        if (!State.map.hasLayer(r.marker)) r.marker.addTo(State.map);
        if (r.trail && s.breadcrumbs_enabled) r.trail.addTo(State.map);
      } else {
        if (State.map.hasLayer(r.marker)) State.map.removeLayer(r.marker);
        if (r.trail && State.map.hasLayer(r.trail)) State.map.removeLayer(r.trail);
      }
    } else {
      if (s.show_no_gps_units && r.last_pos) {
        if (!State.map.hasLayer(r.marker)) r.marker.addTo(State.map);
      } else {
        if (State.map.hasLayer(r.marker)) State.map.removeLayer(r.marker);
      }
    }
  }

  // 2. Marker Sichtbarkeit
  for (const mObj of Object.values(State.markers)) {
    if (s.markers_visible) {
      if (!State.map.hasLayer(mObj.leaflet_marker)) mObj.leaflet_marker.addTo(State.map);
    } else {
      if (State.map.hasLayer(mObj.leaflet_marker)) State.map.removeLayer(mObj.leaflet_marker);
    }
  }

  // 3. Rufzeichen-Tags
  document.querySelectorAll(".radio-marker-tag").forEach(tag => {
    tag.style.display = s.unit_labels_visible ? "block" : "none";
  });

  // 4. Timeline Dock Ein-/Ausklappen
  const tlDock = document.getElementById("hud-timeline-dock");
  if (tlDock) {
    if (s.timeline_visible) {
      tlDock.classList.remove("timeline-collapsed");
    } else {
      tlDock.classList.add("timeline-collapsed");
    }
    setTimeout(() => State.map && State.map.invalidateSize(), 250);
  }
}

async function saveSettingsToServer() {
  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: State.settings })
    });
  } catch (err) {
    console.error("Fehler beim Speichern der Einstellungen:", err);
  }
}


// ── 13. Sidebar UI Updates ──────────────────────────────────
function updateSidebarRadios() {
  const gpsPanel = document.getElementById("radio-gps-list-panel");
  const noGpsPanel = document.getElementById("radio-nogps-list-panel");
  const countGps = document.getElementById("sb-gps-count");
  const countNoGps = document.getElementById("sb-nogps-count");
  const statGps = document.getElementById("stat-radios-gps");
  const statNoGps = document.getElementById("stat-radios-nogps");

  const allRadios = Object.values(State.radios);
  const filter = (State.searchFilter || "").toLowerCase();
  const matches = (r) => !filter || (r.alias || '').toLowerCase().includes(filter) || String(r.radio_id).includes(filter) || (r.device_model || '').toLowerCase().includes(filter);

  const gpsRadios = allRadios.filter(r => r.has_gps && matches(r));
  const noGpsRadios = allRadios.filter(r => !r.has_gps && matches(r));

  if (countGps) countGps.textContent = `${gpsRadios.length} AKTIV`;
  if (countNoGps) countNoGps.textContent = `${noGpsRadios.length} GERÄTE`;
  if (statGps) statGps.textContent = allRadios.filter(r => r.has_gps).length;
  if (statNoGps) statNoGps.textContent = allRadios.filter(r => !r.has_gps).length;

  // 1. GPS Einheiten Rendern
  if (gpsPanel) {
    if (gpsRadios.length === 0) {
      gpsPanel.innerHTML = '<div class="list-placeholder">Keine GPS-Einheiten...</div>';
    } else {
      gpsPanel.innerHTML = "";
      gpsRadios.forEach(r => {
        const card = document.createElement("div");
        card.id = `unit-card-${r.radio_id}`;
        card.className = "unit-card";
        card.innerHTML = `
          <div class="unit-card-header">
            <span class="unit-card-alias">🛰️ ${escapeHtml(r.alias)}</span>
            <span class="unit-card-id">ID: ${r.radio_id}</span>
          </div>
          <div class="unit-card-details">
            <span>GESCHW: ${(r.last_speed || 0).toFixed(1)} km/h</span>
            <span>KURS: ${(r.last_heading || 0).toFixed(0)}°</span>
          </div>
        `;
        card.addEventListener("click", () => {
          if (r.last_pos) {
            State.map.flyTo(r.last_pos, 17);
            if (r.marker) r.marker.openPopup();
          }
        });
        gpsPanel.appendChild(card);
      });
    }
  }

  // 2. No-GPS Einheiten Rendern (Handfunkgeräte)
  if (noGpsPanel) {
    if (noGpsRadios.length === 0) {
      noGpsPanel.innerHTML = '<div class="list-placeholder">Keine No-GPS-Geräte...</div>';
    } else {
      noGpsPanel.innerHTML = "";
      noGpsRadios.forEach(r => {
        const card = document.createElement("div");
        card.id = `unit-card-${r.radio_id}`;
        card.className = "unit-card";
        card.innerHTML = `
          <div class="unit-card-header">
            <span class="unit-card-alias">📻 ${escapeHtml(r.alias)}</span>
            <span class="unit-card-id">ID: ${r.radio_id}</span>
          </div>
          <div class="unit-card-details">
            <span>${r.last_pos ? '📍 Platziert' : '⚠️ Kein Standort'}</span>
            <span>${r.device_model || 'Handgerät'}</span>
          </div>
          <div class="unit-card-actions">
            <button class="btn btn-card-action" onclick="event.stopPropagation(); startPlacementMode(${r.radio_id}, '${escapeHtml(r.alias)}')">
              📍 ${r.last_pos ? 'Neu platzieren' : 'Auf Karte setzen'}
            </button>
          </div>
        `;
        card.addEventListener("click", () => {
          if (r.last_pos) {
            State.map.flyTo(r.last_pos, 17);
            if (r.marker) r.marker.openPopup();
          }
        });
        noGpsPanel.appendChild(card);
      });
    }
  }
}

function updateSidebarMarkers() {
  const container = document.getElementById("marker-list-panel");
  const countBadge = document.getElementById("sb-marker-count");
  const statMarkers = document.getElementById("stat-markers");

  const markerList = Object.values(State.markers);
  if (countBadge) countBadge.textContent = `${markerList.length} GESAMT`;
  if (statMarkers) statMarkers.textContent = markerList.length;

  if (!container) return;
  if (markerList.length === 0) {
    container.innerHTML = '<div class="list-placeholder">Keine Vorfälle markiert.</div>';
    return;
  }

  container.innerHTML = "";
  markerList.forEach(mObj => {
    const m = mObj.data;
    const card = document.createElement("div");
    card.className = `marker-card prio-${m.prioritaet.toLowerCase()}`;
    card.innerHTML = `
      <div class="marker-card-top">
        <span class="marker-card-type">${TYPE_ICONS[m.typ] || '📍'} [${m.typ.toUpperCase()}]</span>
        <span style="color:${PRIO_COLORS[m.prioritaet] || '#fff'}; font-size:9px; font-weight:700;">${m.prioritaet.toUpperCase()}</span>
      </div>
      <div class="marker-card-desc">${escapeHtml(m.beschreibung)}</div>
    `;
    card.addEventListener("click", () => {
      State.map.flyTo([m.lat, m.lon], 17);
      mObj.leaflet_marker.openPopup();
    });
    container.appendChild(card);
  });
}


// ── 14. Web Audio Synthesizer (PTT-Chirp) ───────────────────
function playTacticalChirp() {
  if (!State.settings.audio_enabled) return;
  try {
    if (!State.audioContext) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) State.audioContext = new AudioCtx();
    }
    if (State.audioContext.state === "suspended") {
      State.audioContext.resume();
    }

    const ctx = State.audioContext;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = "sine";
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(1760, ctx.currentTime + 0.08);

    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.12);

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.12);
  } catch (err) {}
}


// ── 15. UI Event-Listener & Modals ──────────────────────────
function initUIListeners() {
  // Theme Switcher Button
  document.getElementById("btn-theme-toggle")?.addEventListener("click", toggleTheme);

  // Live / Replay
  document.getElementById("btn-live-toggle")?.addEventListener("click", () => {
    if (!State.isLive) exitReplayMode();
  });
  document.getElementById("btn-return-live")?.addEventListener("click", exitReplayMode);

  // Karte zentrieren
  document.getElementById("btn-recenter")?.addEventListener("click", () => {
    State.map.flyTo([51.2325, 6.7800], 15);
  });

  // Schnell-Toggles in der Toolbar
  document.getElementById("btn-gps-toggle")?.addEventListener("click", () => {
    State.settings.gps_tracking_enabled = !State.settings.gps_tracking_enabled;
    applySettingsToUI();
    saveSettingsToServer();
  });

  document.getElementById("btn-ptt-pulse-toggle")?.addEventListener("click", () => {
    State.settings.ptt_pulse_enabled = !State.settings.ptt_pulse_enabled;
    applySettingsToUI();
    saveSettingsToServer();
  });

  document.getElementById("btn-autopan-toggle")?.addEventListener("click", () => {
    State.settings.auto_pan_ptt = !State.settings.auto_pan_ptt;
    applySettingsToUI();
    saveSettingsToServer();
  });

  document.getElementById("btn-timeline-toggle")?.addEventListener("click", () => {
    State.settings.timeline_visible = !State.settings.timeline_visible;
    applySettingsToUI();
    saveSettingsToServer();
  });

  document.getElementById("btn-tl-close")?.addEventListener("click", () => {
    State.settings.timeline_visible = false;
    applySettingsToUI();
    saveSettingsToServer();
  });

  document.getElementById("btn-audio-toggle")?.addEventListener("click", () => {
    State.settings.audio_enabled = !State.settings.audio_enabled;
    applySettingsToUI();
    saveSettingsToServer();
  });

  document.getElementById("btn-markers-toggle")?.addEventListener("click", () => {
    State.settings.markers_visible = !State.settings.markers_visible;
    applySettingsToUI();
    saveSettingsToServer();
  });

  document.getElementById("btn-breadcrumbs-toggle")?.addEventListener("click", () => {
    State.settings.breadcrumbs_enabled = !State.settings.breadcrumbs_enabled;
    applySettingsToUI();
    saveSettingsToServer();
  });

  // Overlay Toggle & Opacity Slider
  const btnOverlay = document.getElementById("btn-overlay-toggle");
  btnOverlay?.addEventListener("click", () => {
    State.settings.overlay_visible = !State.settings.overlay_visible;
    applySettingsToUI();
    saveSettingsToServer();
  });

  const slider = document.getElementById("overlay-opacity-slider");
  slider?.addEventListener("input", (e) => {
    const val = parseInt(e.target.value, 10);
    updateOverlayOpacity(val / 100.0);
  });

  // Sidebar Toggle
  document.getElementById("btn-toggle-sidebar")?.addEventListener("click", () => {
    document.getElementById("hud-sidebar")?.classList.toggle("sidebar-closed");
    setTimeout(() => State.map && State.map.invalidateSize(), 250);
  });

  // Vorfallsmarker Modus
  document.getElementById("btn-add-marker-mode")?.addEventListener("click", () => {
    setAddMarkerMode(!State.addMarkerMode);
  });

  // Standortzuweisung abbrechen
  document.getElementById("btn-cancel-placement")?.addEventListener("click", cancelPlacementMode);

  // Settings Modal öffnen & schließen
  document.getElementById("btn-open-settings")?.addEventListener("click", () => {
    document.getElementById("settings-modal-backdrop")?.classList.remove("modal-backdrop-hidden");
  });
  document.getElementById("btn-settings-close")?.addEventListener("click", () => {
    document.getElementById("settings-modal-backdrop")?.classList.add("modal-backdrop-hidden");
  });
  document.getElementById("btn-settings-save")?.addEventListener("click", () => {
    // Werte aus Checkboxen lesen
    State.settings.gps_tracking_enabled = document.getElementById("toggle-gps-tracking")?.checked;
    State.settings.show_no_gps_units = document.getElementById("toggle-show-no-gps")?.checked;
    State.settings.ptt_pulse_enabled = document.getElementById("toggle-ptt-pulse")?.checked;
    State.settings.auto_pan_ptt = document.getElementById("toggle-auto-pan")?.checked;
    State.settings.overlay_visible = document.getElementById("toggle-overlay")?.checked;
    State.settings.markers_visible = document.getElementById("toggle-markers")?.checked;
    State.settings.unit_labels_visible = document.getElementById("toggle-labels")?.checked;
    State.settings.breadcrumbs_enabled = document.getElementById("toggle-breadcrumbs")?.checked;
    State.settings.audio_enabled = document.getElementById("toggle-audio")?.checked;
    State.settings.timeline_visible = document.getElementById("toggle-timeline")?.checked;
    State.settings.mock_active = document.getElementById("toggle-mock")?.checked;

    applySettingsToUI();
    saveSettingsToServer();
    document.getElementById("settings-modal-backdrop")?.classList.add("modal-backdrop-hidden");
  });

  // Infrastruktur-Überwachungs-Modal öffnen & schließen
  const openInfraModal = () => {
    document.getElementById("infra-modal-backdrop")?.classList.remove("modal-backdrop-hidden");
    if (State.infrastructure) updateInfrastructureUI(State.infrastructure);
  };

  document.getElementById("btn-open-infra")?.addEventListener("click", openInfraModal);
  document.getElementById("pill-repeater")?.addEventListener("click", openInfraModal);
  document.getElementById("pill-usv")?.addEventListener("click", openInfraModal);
  document.getElementById("pill-router")?.addEventListener("click", openInfraModal);

  document.getElementById("btn-infra-close")?.addEventListener("click", () => {
    document.getElementById("infra-modal-backdrop")?.classList.add("modal-backdrop-hidden");
  });
  document.getElementById("btn-infra-close-btn")?.addEventListener("click", () => {
    document.getElementById("infra-modal-backdrop")?.classList.add("modal-backdrop-hidden");
  });

  // Timeline Navigation
  document.getElementById("btn-tl-zoom-in")?.addEventListener("click", () => State.timeline?.zoom(-0.4));
  document.getElementById("btn-tl-zoom-out")?.addEventListener("click", () => State.timeline?.zoom(0.4));
  document.getElementById("btn-tl-fit")?.addEventListener("click", () => State.timeline?.fit());
  document.getElementById("btn-tl-now")?.addEventListener("click", () => {
    if (State.timeline) {
      const now = new Date();
      State.timeline.moveTo(now);
      State.timeline.setCustomTime(now, "replay-slider");
    }
  });

  // Replay Schnellwahltasten (-1m, -5m, -15m)
  const jumpReplay = (seconds) => {
    const targetDate = new Date(Date.now() - seconds * 1000);
    if (State.timeline) {
      State.timeline.setCustomTime(targetDate, "replay-slider");
      State.timeline.moveTo(targetDate);
    }
    enterReplayMode(targetDate);
  };

  document.getElementById("btn-replay-minus-1m")?.addEventListener("click", () => jumpReplay(60));
  document.getElementById("btn-replay-minus-5m")?.addEventListener("click", () => jumpReplay(300));
  document.getElementById("btn-replay-minus-15m")?.addEventListener("click", () => jumpReplay(900));

  // Layer-Dropdown umschalten
  const btnDropdown = document.getElementById("btn-dropdown-layers");
  const menuDropdown = document.getElementById("dropdown-layers-menu");

  btnDropdown?.addEventListener("click", (e) => {
    e.stopPropagation();
    menuDropdown?.classList.toggle("dropdown-menu-hidden");
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".dropdown-wrapper")) {
      menuDropdown?.classList.add("dropdown-menu-hidden");
    }
  });

  // Kartenrotation Event-Listener
  document.getElementById("btn-map-compass")?.addEventListener("click", resetMapRotation);
  document.getElementById("btn-rotate-left")?.addEventListener("click", () => rotateMap(-15));
  document.getElementById("btn-rotate-right")?.addEventListener("click", () => rotateMap(15));
  document.getElementById("btn-reset-rotation")?.addEventListener("click", resetMapRotation);
  document.getElementById("btn-trackup-toggle")?.addEventListener("click", toggleTrackUp);

  const rotSlider = document.getElementById("map-rotation-slider");
  rotSlider?.addEventListener("input", (e) => {
    setMapRotation(parseInt(e.target.value, 10));
  });

  const georefRotSlider = document.getElementById("georef-rotation-slider");
  const georefRotVal = document.getElementById("georef-rotation-val");
  
  const setGeorefRotation = (val) => {
    val = (val % 360 + 360) % 360;
    State.georef.rotation = val;
    if (georefRotSlider) georefRotSlider.value = Math.round(val);
    if (georefRotVal) georefRotVal.textContent = `${Math.round(val)}°`;
    applyOverlayRotation(val);
    updateGeorefHandlePositions();
  };

  georefRotSlider?.addEventListener("input", (e) => {
    setGeorefRotation(parseInt(e.target.value, 10));
  });

  document.getElementById("btn-georef-rotate-cw")?.addEventListener("click", () => {
    setGeorefRotation((State.georef.rotation || 0) + 90);
  });

  // Georeferenzierungs-Modal & Upload
  document.getElementById("btn-open-georef")?.addEventListener("click", openGeorefModal);
  document.getElementById("btn-georef-modal-close")?.addEventListener("click", closeGeorefModal);
  document.getElementById("btn-georef-modal-cancel")?.addEventListener("click", closeGeorefModal);

  const dropzone = document.getElementById("georef-dropzone");
  const fileInput = document.getElementById("input-overlay-file");

  dropzone?.addEventListener("click", () => fileInput?.click());

  dropzone?.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("drag-over");
  });

  dropzone?.addEventListener("dragleave", () => {
    dropzone.classList.remove("drag-over");
  });

  dropzone?.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag-over");
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      uploadOverlayFile(e.dataTransfer.files[0]);
    }
  });

  fileInput?.addEventListener("change", (e) => {
    if (e.target.files && e.target.files.length > 0) {
      uploadOverlayFile(e.target.files[0]);
    }
  });

  document.getElementById("btn-recalibrate-current")?.addEventListener("click", () => {
    closeGeorefModal();
    const curImg = (State.overlayLayer && State.overlayLayer._url) || "/static/assets/tactical_overlay.png";
    const curBounds = (State.overlayLayer && State.overlayLayer.getBounds()) 
      ? [[State.overlayLayer.getBounds().getSouth(), State.overlayLayer.getBounds().getWest()], [State.overlayLayer.getBounds().getNorth(), State.overlayLayer.getBounds().getEast()]]
      : [[51.2250, 6.7700], [51.2400, 6.7900]];
    startGeorefCalibration(curImg, curBounds, "Aktueller Plan");
  });

  // Georef Dock Buttons
  document.getElementById("btn-georef-fit")?.addEventListener("click", () => {
    const mapBounds = State.map.getBounds();
    const sw = mapBounds.getSouthWest();
    const ne = mapBounds.getNorthEast();
    const latSpan = ne.lat - sw.lat;
    const lngSpan = ne.lng - sw.lng;
    const fitBounds = [
      [sw.lat + latSpan * 0.15, sw.lng + lngSpan * 0.15],
      [ne.lat - latSpan * 0.15, ne.lng - lngSpan * 0.15]
    ];
    State.georef.bounds = fitBounds;
    if (State.georef.tempLayer) State.georef.tempLayer.setBounds(fitBounds);
    if (State.georef.handles) {
      State.georef.handles.forEach(h => State.map.removeLayer(h));
    }
    createGeorefHandles();
  });

  document.getElementById("btn-georef-save")?.addEventListener("click", saveGeorefCalibration);
  document.getElementById("btn-georef-cancel")?.addEventListener("click", () => stopGeorefCalibration(false));

  const georefSlider = document.getElementById("georef-opacity-slider");
  georefSlider?.addEventListener("input", (e) => {
    const val = parseInt(e.target.value, 10);
    updateOverlayOpacity(val / 100.0);
  });

  // Incident Modal
  document.getElementById("btn-modal-close")?.addEventListener("click", closeIncidentModal);
  document.getElementById("btn-modal-cancel")?.addEventListener("click", closeIncidentModal);

  document.getElementById("form-new-marker")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const payload = {
      lat: parseFloat(fd.get("lat")),
      lon: parseFloat(fd.get("lon")),
      typ: fd.get("typ") || "incident",
      beschreibung: fd.get("beschreibung") || "",
      prioritaet: fd.get("prioritaet") || "normal",
      author: fd.get("author") || "Operator"
    };

    try {
      const res = await fetch("/api/markers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        closeIncidentModal();
      }
    } catch (err) {
      console.error("Fehler bei Marker-POST:", err);
    }
  });
}

function openIncidentModal(lat, lon) {
  const modal = document.getElementById("modal-backdrop");
  const inputLat = document.getElementById("input-lat");
  const inputLon = document.getElementById("input-lon");
  const inputDesc = document.getElementById("input-beschreibung");

  if (inputLat) inputLat.value = parseFloat(lat).toFixed(6);
  if (inputLon) inputLon.value = parseFloat(lon).toFixed(6);
  if (inputDesc) inputDesc.value = "";

  if (modal) modal.classList.remove("modal-backdrop-hidden");
}

function closeIncidentModal() {
  document.getElementById("modal-backdrop")?.classList.add("modal-backdrop-hidden");
}

function setAddMarkerMode(active) {
  State.addMarkerMode = active;
  const btn = document.getElementById("btn-add-marker-mode");
  const mapEl = document.getElementById("map");
  if (btn) {
    if (active) {
      btn.classList.add("btn-danger");
      btn.textContent = "📍 KLICK AUF KARTE...";
      if (mapEl) mapEl.style.cursor = "crosshair";
    } else {
      btn.classList.remove("btn-danger");
      btn.textContent = "➕ NEUER VORFALL";
      if (mapEl) mapEl.style.cursor = "";
    }
  }
}

// ── 16. Lageplan-Georeferenzierung & Kalibrierungs-Werkzeug ──
function openGeorefModal() {
  document.getElementById("georef-modal-backdrop")?.classList.remove("modal-backdrop-hidden");
}

function closeGeorefModal() {
  document.getElementById("georef-modal-backdrop")?.classList.add("modal-backdrop-hidden");
}

async function uploadOverlayFile(file) {
  const progressWrap = document.getElementById("upload-progress-wrap");
  const statusText = document.getElementById("upload-status-text");
  if (progressWrap) progressWrap.classList.remove("upload-progress-hidden");
  if (statusText) statusText.textContent = `Verarbeite "${file.name}"...`;

  const fd = new FormData();
  fd.append("file", file);

  try {
    const res = await fetch("/api/overlay/upload", {
      method: "POST",
      body: fd
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || "Upload fehlgeschlagen");
    }
    const data = await res.json();
    closeGeorefModal();
    if (progressWrap) progressWrap.classList.add("upload-progress-hidden");

    // Starte Kalibrierung mit den aktuellen Karten-Bounds
    const mapBounds = State.map.getBounds();
    const sw = mapBounds.getSouthWest();
    const ne = mapBounds.getNorthEast();
    const latSpan = ne.lat - sw.lat;
    const lngSpan = ne.lng - sw.lng;
    const initialBounds = [
      [sw.lat + latSpan * 0.15, sw.lng + lngSpan * 0.15],
      [ne.lat - latSpan * 0.15, ne.lng - lngSpan * 0.15]
    ];

    startGeorefCalibration(data.image_url, initialBounds, data.original_name || file.name);
  } catch (err) {
    alert("Fehler beim Upload: " + err.message);
    if (progressWrap) progressWrap.classList.add("upload-progress-hidden");
  }
}

function startGeorefCalibration(imageUrl, initialBounds, displayName = "Lageplan") {
  stopGeorefCalibration(false);

  State.georef.active = true;
  State.georef.imageUrl = imageUrl;
  State.georef.bounds = initialBounds;

  const georefDock = document.getElementById("georef-dock");
  const filenameEl = document.getElementById("georef-filename");
  if (georefDock) georefDock.classList.remove("georef-dock-hidden");
  if (filenameEl) filenameEl.textContent = displayName;

  if (State.overlayLayer) {
    State.map.removeLayer(State.overlayLayer);
  }

  State.georef.tempLayer = L.imageOverlay(imageUrl, initialBounds, {
    opacity: 0.70,
    zIndex: 450
  }).addTo(State.map);

  createGeorefHandles();
}

function getRotatedCornerPos(lat, lng, centerLat, centerLng, deg) {
  if (!deg || deg === 0) return [lat, lng];

  const rad = deg * Math.PI / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);

  const cosLat = Math.cos(centerLat * Math.PI / 180);
  const dLat = lat - centerLat;
  const dLng = (lng - centerLng) * cosLat;

  const rLat = dLat * cos - dLng * sin;
  const rLng = dLat * sin + dLng * cos;

  return [
    centerLat + rLat,
    centerLng + (cosLat ? rLng / cosLat : rLng)
  ];
}

function updateGeorefHandlePositions() {
  if (!State.georef || !State.georef.bounds || !State.georef.handles) return;

  const bounds = State.georef.bounds;
  const south = bounds[0][0];
  const west = bounds[0][1];
  const north = bounds[1][0];
  const east = bounds[1][1];
  const centerLat = (south + north) / 2;
  const centerLng = (west + east) / 2;
  const rot = State.georef.rotation || 0;

  const nw = State.georef.handles.find(h => h.cornerId === "nw");
  const ne = State.georef.handles.find(h => h.cornerId === "ne");
  const se = State.georef.handles.find(h => h.cornerId === "se");
  const sw = State.georef.handles.find(h => h.cornerId === "sw");
  const center = State.georef.handles.find(h => !h.cornerId);

  if (nw) nw.setLatLng(getRotatedCornerPos(north, west, centerLat, centerLng, rot));
  if (ne) ne.setLatLng(getRotatedCornerPos(north, east, centerLat, centerLng, rot));
  if (se) se.setLatLng(getRotatedCornerPos(south, east, centerLat, centerLng, rot));
  if (sw) sw.setLatLng(getRotatedCornerPos(south, west, centerLat, centerLng, rot));

  if (center) {
    center.setLatLng([centerLat, centerLng]);
    center.lastPos = [centerLat, centerLng];
  }
}

function createGeorefHandles() {
  const bounds = State.georef.bounds;
  const south = bounds[0][0];
  const west = bounds[0][1];
  const north = bounds[1][0];
  const east = bounds[1][1];

  const corners = [
    { id: "nw", lat: north, lng: west },
    { id: "ne", lat: north, lng: east },
    { id: "se", lat: south, lng: east },
    { id: "sw", lat: south, lng: west }
  ];

  State.georef.handles = [];

  corners.forEach(c => {
    const icon = L.divIcon({
      className: "georef-handle-icon",
      html: `<span>${c.id.toUpperCase()}</span>`,
      iconSize: [22, 22],
      iconAnchor: [11, 11]
    });

    const marker = L.marker([c.lat, c.lng], {
      draggable: true,
      icon: icon,
      zIndexOffset: 1000
    }).addTo(State.map);

    marker.cornerId = c.id;
    marker.on("drag", () => onGeorefCornerDrag(marker));
    State.georef.handles.push(marker);
  });

  const centerLat = (south + north) / 2;
  const centerLng = (west + east) / 2;
  const centerIcon = L.divIcon({
    className: "georef-center-icon",
    html: `<span>✥</span>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16]
  });

  const centerMarker = L.marker([centerLat, centerLng], {
    draggable: true,
    icon: centerIcon,
    zIndexOffset: 1001
  }).addTo(State.map);

  centerMarker.lastPos = [centerLat, centerLng];
  centerMarker.on("drag", () => onGeorefCenterDrag(centerMarker));
  State.georef.handles.push(centerMarker);

  updateGeorefHandlePositions();
}

function onGeorefCornerDrag(draggedMarker) {
  const id = draggedMarker.cornerId;
  const newPos = draggedMarker.getLatLng();

  let nw = State.georef.handles.find(h => h.cornerId === "nw");
  let ne = State.georef.handles.find(h => h.cornerId === "ne");
  let se = State.georef.handles.find(h => h.cornerId === "se");
  let sw = State.georef.handles.find(h => h.cornerId === "sw");
  let center = State.georef.handles.find(h => !h.cornerId);

  const rot = State.georef.rotation || 0;
  if (rot !== 0 && center) {
    const centerPos = center.getLatLng();
    const unrotated = getRotatedCornerPos(newPos.lat, newPos.lng, centerPos.lat, centerPos.lng, -rot);
    newPos = { lat: unrotated[0], lng: unrotated[1] };
  }

  let north = nw.getLatLng().lat;
  let south = se.getLatLng().lat;
  let west = nw.getLatLng().lng;
  let east = se.getLatLng().lng;

  if (id === "nw") {
    north = newPos.lat;
    west = newPos.lng;
  } else if (id === "ne") {
    north = newPos.lat;
    east = newPos.lng;
  } else if (id === "se") {
    south = newPos.lat;
    east = newPos.lng;
  } else if (id === "sw") {
    south = newPos.lat;
    west = newPos.lng;
  }

  const s = Math.min(south, north);
  const n = Math.max(south, north);
  const w = Math.min(west, east);
  const e = Math.max(west, east);

  State.georef.bounds = [[s, w], [n, e]];
  if (State.georef.tempLayer) {
    State.georef.tempLayer.setBounds(State.georef.bounds);
  }

  if (center) {
    center.setLatLng([(s + n) / 2, (w + e) / 2]);
    center.lastPos = [(s + n) / 2, (w + e) / 2];
  }
}

function onGeorefCenterDrag(centerMarker) {
  const newPos = centerMarker.getLatLng();
  const dLat = newPos.lat - centerMarker.lastPos[0];
  const dLng = newPos.lng - centerMarker.lastPos[1];
  centerMarker.lastPos = [newPos.lat, newPos.lng];

  State.georef.handles.forEach(h => {
    if (h.cornerId) {
      const p = h.getLatLng();
      h.setLatLng([p.lat + dLat, p.lng + dLng]);
    }
  });

  const b = State.georef.bounds;
  State.georef.bounds = [
    [b[0][0] + dLat, b[0][1] + dLng],
    [b[1][0] + dLat, b[1][1] + dLng]
  ];

  if (State.georef.tempLayer) {
    State.georef.tempLayer.setBounds(State.georef.bounds);
  }
}

async function saveGeorefCalibration() {
  if (!State.georef.active || !State.georef.bounds) return;

  const config = {
    image_url: State.georef.imageUrl,
    bounds: State.georef.bounds,
    rotation: State.georef.rotation || 0,
    opacity: State.settings.overlay_opacity || 0.85,
    visible: true
  };

  try {
    const res = await fetch("/api/overlay-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config)
    });

    if (res.ok) {
      stopGeorefCalibration(true);
      setupImageOverlay(config);
    }
  } catch (err) {
    alert("Fehler beim Speichern der Verankerung: " + err.message);
  }
}

function stopGeorefCalibration(saved = false) {
  State.georef.active = false;
  document.getElementById("georef-dock")?.classList.add("georef-dock-hidden");

  if (State.georef.handles) {
    State.georef.handles.forEach(h => State.map.removeLayer(h));
    State.georef.handles = [];
  }

  if (State.georef.tempLayer) {
    State.map.removeLayer(State.georef.tempLayer);
    State.georef.tempLayer = null;
  }

  if (!saved && State.overlayLayer && State.settings.overlay_visible) {
    State.overlayLayer.addTo(State.map);
  }
}

// ── 17. Call-Log Ticker ─────────────────────────────────────
async function initRecentCalls() {
  try {
    const res = await fetch("/api/recent-calls");
    if (res.ok) {
      const data = await res.json();
      if (data.calls && data.calls.length > 0) {
        State.recentCalls = data.calls.slice(0, 4);
        renderCallLog();
      }
    }
  } catch (e) {}
}

function addCallLogEntry(call) {
  State.recentCalls.unshift(call);
  if (State.recentCalls.length > 4) State.recentCalls.pop();
  renderCallLog();
}

function renderCallLog() {
  const listEl = document.getElementById("call-log-list");
  if (!listEl) return;

  if (State.recentCalls.length === 0) {
    listEl.innerHTML = '<div class="cl-empty">Warte auf Funkverkehr...</div>';
    return;
  }

  listEl.innerHTML = "";
  State.recentCalls.forEach(call => {
    const entry = document.createElement("div");
    entry.className = "call-log-entry";
    const d = new Date(call.timestamp);
    const timeStr = isNaN(d.getTime()) ? "--:--:--" : d.toLocaleTimeString();
    const durSec = call.duration_ms ? `${(call.duration_ms / 1000).toFixed(1)}s` : "TX";

    entry.innerHTML = `
      <div class="cl-entry-left">
        <span class="cl-alias">🎙️ ${escapeHtml(call.alias || 'Radio ' + call.radio_id)}</span>
        <span class="cl-meta">${call.slot || 'TS1'} &bull; ${durSec}</span>
      </div>
      <span class="cl-time">${timeStr}</span>
    `;

    entry.addEventListener("click", () => {
      const r = State.radios[call.radio_id];
      if (r && r.last_pos) {
        State.map.flyTo(r.last_pos, 17);
        if (r.marker) r.marker.openPopup();
      }
    });

    listEl.appendChild(entry);
  });
}

// ── 18. Sidebar Suche ───────────────────────────────────────
function initSearchBox() {
  const input = document.getElementById("input-radio-search");
  const btnClear = document.getElementById("btn-clear-search");

  input?.addEventListener("input", (e) => {
    State.searchFilter = (e.target.value || "").trim().toLowerCase();
    updateSidebarRadios();
  });

  btnClear?.addEventListener("click", () => {
    if (input) input.value = "";
    State.searchFilter = "";
    updateSidebarRadios();
  });
}

// ── 19. Tastatur-Shortcuts ──────────────────────────────────
function initShortcuts() {
  window.addEventListener("keydown", (e) => {
    const tag = e.target.tagName ? e.target.tagName.toLowerCase() : "";
    if (tag === "input" || tag === "textarea" || tag === "select") {
      if (e.key === "Escape") {
        e.target.blur();
      }
      return;
    }

    if (e.key === " ") {
      e.preventDefault();
      State.map.flyTo([51.2325, 6.7800], 15);
    } else if (e.key === "r" || e.key === "R") {
      e.preventDefault();
      rotateMap(e.shiftKey ? -15 : 15);
    } else if (e.key === "n" || e.key === "N") {
      e.preventDefault();
      if (e.shiftKey) {
        resetMapRotation();
      } else {
        openIncidentModal(51.2325, 6.7800);
      }
    } else if (e.key === "l" || e.key === "L") {
      e.preventDefault();
      if (!State.isLive) exitReplayMode();
    } else if (e.key === "s" || e.key === "S") {
      e.preventDefault();
      document.getElementById("hud-sidebar")?.classList.toggle("sidebar-closed");
      setTimeout(() => State.map && State.map.invalidateSize(), 250);
    } else if (e.key === "t" || e.key === "T") {
      e.preventDefault();
      State.settings.timeline_visible = !State.settings.timeline_visible;
      applySettingsToUI();
      saveSettingsToServer();
    } else if (e.key === "Escape") {
      document.querySelectorAll("[id*='modal-backdrop']").forEach(m => m.classList.add("modal-backdrop-hidden"));
      cancelPlacementMode();
      if (State.georef && State.georef.active) stopGeorefCalibration(false);
      document.getElementById("dropdown-layers-menu")?.classList.add("dropdown-menu-hidden");
    }
  });
}

function escapeHtml(str) {
  if (!str) return "";
  return str.replace(/[&<>'"]/g, tag => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;"
  }[tag] || tag));
}
