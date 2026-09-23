/**
 * Hytera Command Center – Frontend Application
 * Professional Dark UI für ELW Führungsunterstützung
 *
 * Module:
 *  - WebSocket (Echtzeit-Events)
 *  - Leaflet Map (GPS, Marker, Geofences, Lagepläne)
 *  - Dashboard (KPIs, Tabellen)
 *  - Infrastruktur (USV, Repeater, ZTE, Omada)
 *  - SMS-Chat
 *  - Timeline / Protokoll
 *  - Einstellungen
 *  - Diagnose-Paketdump
 */

"use strict";

// ─────────────────────────────────────────────────────────────────
// Global State
// ─────────────────────────────────────────────────────────────────
const STATE = {
  radios:        {},   // radio_id → radio object
  gpsPositions:  {},   // radio_id → {lat, lon, heading, rssi}
  activePTTs:    {},   // radio_id → {slot, call_type}
  channelBusy:   { TS1: { busy: false }, TS2: { busy: false } }, // BCL State
  emergencies:   {},   // radio_id → emergency data
  markers:       {},   // id → marker object (DB)
  addingMarker:  false,
  selectedMarkerType: "feuer",
  showMapLabels: localStorage.getItem("hcc_map_labels") !== "false",
  geofences:     [],
  plans:         [],
  sms:           [],
  calls:         [],
  settings:      {},
  mission:       {},
  snmpEvents:    [],
  portStats:     [],
  packets:       [],
  dumpActive:    false,
  breadcrumbs:   {},   // radio_id → [[lat,lon], ...]
  breadcrumbsOn: true,
  rssiMode:      false,
  mapProvider:   "osm",
  currentTab:    "dashboard",
  ws:            null,
  wsConnected:   false,
  map:           null,
  mapMarkers:    {},   // radio_id → Leaflet marker
  mapPaths:      {},   // radio_id → Leaflet polyline
  mapPins:       {},   // DB marker id → Leaflet marker
  mapGeofences:  {},   // zone_id → Leaflet layer
  tileLayer:     null,
  unreadSMS:     0,
  emergencyCount: 0,
  drawingGeofence: null,       // Aktive Zonen-Zeichenkonfiguration
  tempGeofenceLayers: [],      // Temporäre Marker/Linien beim Zeichnen
  selectedGeofenceType: "danger",
  // Hardware-Status (letzter bekannter Zustand)
  hw: {
    ups:     null,
    zte:     null,
    omada:   null,
    repeater: null,
  },
  // Dispatcher
  alarmAudioCtx: null,
  alarmActive:   false,
  loneWorkerTimers: {}, // radio_id → timer
  lastBlockedAlertRadio: null,
  modalTargetRadioId: null,
  settingRadioPositionId: null,
};

// ─────────────────────────────────────────────────────────────────
// Alarm-Sound (Web Audio API – kein externes File nötig)
// ─────────────────────────────────────────────────────────────────
function playAlarmSound() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const pattern = [880, 0, 880, 0, 1320, 0]; // Hz, 0 = Pause
    let t = ctx.currentTime;
    pattern.forEach((freq, i) => {
      if (freq === 0) { t += 0.08; return; }
      const osc  = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = freq;
      osc.type = "square";
      gain.gain.setValueAtTime(0, t);
      gain.gain.linearRampToValueAtTime(0.3, t + 0.01);
      gain.gain.linearRampToValueAtTime(0, t + 0.12);
      osc.start(t);
      osc.stop(t + 0.15);
      t += 0.18;
    });
  } catch(e) {
    console.warn("[Audio] Alarm-Sound fehlgeschlagen:", e);
  }
}

// ─────────────────────────────────────────────────────────────────
// WebSocket
// ─────────────────────────────────────────────────────────────────
function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url   = `${proto}//${location.host}/ws`;
  const ws    = new WebSocket(url);
  STATE.ws    = ws;

  ws.onopen = () => {
    STATE.wsConnected = true;
    setBadge("badge-ws", "online");
    console.log("[WS] Verbunden");
  };

  ws.onmessage = (evt) => {
    if (evt.data === "ping" || evt.data === "pong") return;
    try {
      const msg = JSON.parse(evt.data);
      handleEvent(msg);
    } catch(e) {
      console.warn("[WS] JSON-Fehler:", e);
    }
  };

  ws.onclose = () => {
    STATE.wsConnected = false;
    setBadge("badge-ws", "offline");
    console.log("[WS] Getrennt – reconnect in 3s");
    setTimeout(connectWS, 3000);
  };

  ws.onerror = () => {
    ws.close();
  };
}

// ─────────────────────────────────────────────────────────────────
// Event-Router
// ─────────────────────────────────────────────────────────────────
function handleEvent(msg) {
  switch (msg.type) {

    case "init":
      initFromState(msg);
      break;

    case "gps":
      onGPS(msg);
      break;

    case "ptt_start":
      onPTTStart(msg);
      break;

    case "ptt_end":
      onPTTEnd(msg);
      break;

    case "channel_busy_update":
      if (msg.channel_busy) {
        STATE.channelBusy = msg.channel_busy;
        updateChannelBusyUI();
      }
      break;

    case "emergency":
      onEmergency(msg);
      break;

    case "emergency_ack":
      onEmergencyAck(msg);
      break;

    case "rrs_register":
      onRRSRegister(msg);
      break;

    case "rrs_offline":
      onRRSOffline(msg);
      break;

    case "sms_received":
      onSMSReceived(msg);
      break;

    case "repeater_update":
      onRepeaterUpdate(msg);
      break;

    case "repeater_alarm":
      onRepeaterAlarm(msg);
      break;

    case "ups_update":
      onUPSUpdate(msg);
      break;

    case "zte_update":
      onZTEUpdate(msg);
      break;

    case "omada_update":
      onOmadaUpdate(msg);
      break;

    case "packet_dump":
      onPacketDump(msg.packet);
      break;

    case "audio_ready":
      onAudioReady(msg);
      break;

    case "tx_state_changed":
      onTxStateChanged(msg);
      break;

    case "marker_added":
      onMarkerAdded(msg.marker);
      break;

    case "marker_deleted":
      onMarkerDeleted(msg.id);
      break;

    case "mission_updated":
      applyMissionData(msg.mission);
      break;

    case "settings_updated":
      Object.assign(STATE.settings, msg.settings);
      break;

    case "radio_floor_update":
      if (STATE.radios[msg.radio_id]) {
        STATE.radios[msg.radio_id].floor_level = msg.floor_level;
        renderRadiosTable();
        renderSidebarUnits();
      }
      break;

    case "geofence_added": {
      const existingIdx = STATE.geofences.findIndex(g => g.id === msg.geofence.id);
      if (existingIdx >= 0) {
        STATE.geofences[existingIdx] = msg.geofence;
      } else {
        STATE.geofences.unshift(msg.geofence);
      }
      drawGeofenceOnMap(msg.geofence);
      renderGeofencesList();
      break;
    }

    case "geofence_deleted": {
      if (STATE.mapGeofences && STATE.mapGeofences[msg.id]) {
        STATE.map?.removeLayer(STATE.mapGeofences[msg.id]);
        delete STATE.mapGeofences[msg.id];
      }
      STATE.geofences = STATE.geofences.filter(g => g.id !== msg.id);
      renderGeofencesList();
      break;
    }

    case "zone_breach":
      onZoneBreach(msg);
      break;

    case "system_status":
      updatePortStats(msg.port_stats || []);
      break;

    case "security_alert":
      onSecurityAlert(msg);
      break;

    case "radio_block_update":
      onRadioBlockUpdate(msg);
      break;

    case "repeater_knockdown_changed":
      onRepeaterKnockdownChanged(msg);
      break;

    case "radio_position_cleared":
      if (STATE.radios[msg.radio_id]) {
        STATE.radios[msg.radio_id].fixed_lat = null;
        STATE.radios[msg.radio_id].fixed_lon = null;
        STATE.radios[msg.radio_id].last_lat = null;
        STATE.radios[msg.radio_id].last_lon = null;
      }
      if (STATE.gpsPositions[msg.radio_id]?.is_fixed) {
        delete STATE.gpsPositions[msg.radio_id];
        if (STATE.mapMarkers[msg.radio_id]) {
          STATE.map?.removeLayer(STATE.mapMarkers[msg.radio_id]);
          delete STATE.mapMarkers[msg.radio_id];
        }
      }
      renderRadiosTable();
      break;

    case "system_reset":
      STATE.calls = [];
      STATE.sms = [];
      STATE.gpsPositions = {};
      STATE.emergencies = {};
      STATE.breadcrumbs = {};
      STATE.timelineEvents = [];
      if (msg.radios) {
        STATE.radios = {};
        for (const r of msg.radios) STATE.radios[r.radio_id] = r;
      }
      renderDashboardCalls();
      renderDashboardSMS();
      renderRadiosTable();
      renderSidebarUnits();
      updateLatestCallButton();
      updateKPIs();
      if (STATE.currentTab === "timeline") loadTimeline();
      showToast("System wurde auf sauberen Einsatz-Zustand zurückgesetzt.", "info");
      break;
  }
}

// ─────────────────────────────────────────────────────────────────
// Init from Server
// ─────────────────────────────────────────────────────────────────
function initFromState(msg) {
  // Radios
  (msg.radios || []).forEach(r => {
    STATE.radios[r.radio_id] = r;
    if (r.is_emergency) {
      STATE.emergencies[r.radio_id] = { radio_id: r.radio_id, type: r.emergency_type };
    }
  });

  // GPS-Positionen
  (msg.gps || []).forEach(g => {
    STATE.gpsPositions[g.radio_id] = g;
  });

  // Feste Positionen für Funkgeräte ohne Live-GPS einbinden
  (msg.radios || []).forEach(r => {
    if (r.fixed_lat != null && r.fixed_lon != null && !STATE.gpsPositions[r.radio_id]) {
      STATE.gpsPositions[r.radio_id] = {
        radio_id: r.radio_id,
        lat: r.fixed_lat,
        lon: r.fixed_lon,
        rssi: r.last_rssi,
        is_fixed: true,
        timestamp: new Date().toISOString()
      };
    }
  });

  // Calls
  STATE.calls = msg.calls || [];

  // SMS
  STATE.sms = msg.sms || [];

  // Marker
  (msg.markers || []).forEach(m => {
    STATE.markers[m.id] = m;
  });

  // Geofences
  STATE.geofences = msg.geofences || [];

  // Settings
  STATE.settings = msg.settings || {};
  applySettings(STATE.settings);

  // Mission
  applyMissionData(msg.mission || {});

  // SNMP-Events
  STATE.snmpEvents = msg.snmp_events || [];

  // Kanalbelegung (BCL)
  if (msg.channel_busy) {
    STATE.channelBusy = msg.channel_busy;
    updateChannelBusyUI();
  }

  // Alles rendern
  renderAll();
  initMap();
}

function renderAll() {
  renderSidebarUnits();
  renderRadiosTable();
  renderDashboardCalls();
  renderDashboardSMS();
  renderSMSList();
  renderEmergencyPanel();
  updateKPIs();
  updateTopbarBadges();
  renderSNMPEventsTable();
  renderGeofencesList();
  renderPlansList();
}

// ─────────────────────────────────────────────────────────────────
// GPS Events
// ─────────────────────────────────────────────────────────────────
function onGPS(msg) {
  const rid = msg.radio_id;
  STATE.gpsPositions[rid] = msg;

  // Radio sicherstellen
  if (!STATE.radios[rid]) {
    STATE.radios[rid] = {
      radio_id: rid,
      alias: `Radio ${rid}`,
      online: true,
    };
    renderSidebarUnits();
  }

  // Letzten GPS-Wert im Radio speichern
  STATE.radios[rid].last_lat  = msg.lat;
  STATE.radios[rid].last_lon  = msg.lon;
  STATE.radios[rid].last_rssi = msg.rssi;

  // Breadcrumb hinzufügen
  if (!STATE.breadcrumbs[rid]) STATE.breadcrumbs[rid] = [];
  STATE.breadcrumbs[rid].push([msg.lat, msg.lon]);
  if (STATE.breadcrumbs[rid].length > 200) STATE.breadcrumbs[rid].shift();

  updateMapMarker(rid, msg);
  updateMapPath(rid);

  // RSSI in Sidebar aktualisieren
  const rssiEl = document.getElementById(`unit-rssi-${rid}`);
  if (rssiEl && msg.rssi != null) rssiEl.textContent = `${msg.rssi.toFixed(0)} dBm`;

  updateKPIs();
}

// ─────────────────────────────────────────────────────────────────
// PTT Events
// ─────────────────────────────────────────────────────────────────
function onPTTStart(msg) {
  const rid = msg.radio_id;
  STATE.activePTTs[rid] = msg;

  // Kanalbelegungs-Status (BCL) aktualisieren
  if (msg.channel_busy) {
    STATE.channelBusy = msg.channel_busy;
  } else if (msg.slot) {
    const s = msg.slot.toUpperCase();
    if (!STATE.channelBusy) STATE.channelBusy = {};
    STATE.channelBusy[s] = { busy: true, radio_id: rid, call_type: msg.call_type };
  }
  updateChannelBusyUI();

  // Sidebar-Unit aktualisieren
  const item = document.getElementById(`unit-item-${rid}`);
  if (item) {
    item.classList.add("ptt-active");
    const avatar = item.querySelector(".unit-avatar");
    if (avatar) { avatar.className = "unit-avatar ptt"; avatar.textContent = "🎙"; }
    const sub = item.querySelector(".unit-sub");
    if (sub) sub.textContent = `${msg.call_type} · ${msg.slot}`;
  }

  // PTT-Topbar-Indikator
  const pttContainer = document.getElementById("ptt-indicator-container");
  const pttText      = document.getElementById("ptt-indicator-text");
  if (pttContainer && pttText) {
    const alias = STATE.radios[rid]?.alias || `Radio ${rid}`;
    pttText.textContent = `${alias} – ${msg.call_type}`;
    pttContainer.style.display = "block";
  }

  // Karten-Marker aktualisieren
  updateMapMarkerPTT(rid, true);

  // Auto-pan
  if (STATE.settings.cfg_auto_pan && STATE.gpsPositions[rid] && STATE.map) {
    STATE.map.panTo([STATE.gpsPositions[rid].lat, STATE.gpsPositions[rid].lon]);
  }

  // Toast
  const alias = STATE.radios[rid]?.alias || `Radio ${rid}`;
  if (msg.is_emergency) {
    showToast(`🚨 NOTRUF: ${alias}`, msg.call_type, "emergency");
  }
}

function onPTTEnd(msg) {
  const rid = msg.radio_id;
  delete STATE.activePTTs[rid];

  // Kanalbelegungs-Status (BCL) freigeben
  if (msg.channel_busy) {
    STATE.channelBusy = msg.channel_busy;
  } else if (msg.slot) {
    const s = msg.slot.toUpperCase();
    if (!STATE.channelBusy) STATE.channelBusy = {};
    STATE.channelBusy[s] = { busy: false, radio_id: null, call_type: null };
  }
  updateChannelBusyUI();

  // Sidebar zurücksetzen
  const item = document.getElementById(`unit-item-${rid}`);
  if (item) {
    item.classList.remove("ptt-active");
    const isOnline = STATE.radios[rid]?.online;
    const avatar   = item.querySelector(".unit-avatar");
    if (avatar) {
      avatar.className = `unit-avatar ${isOnline ? 'online' : ''}`;
      avatar.textContent = getAvatarText(rid);
    }
    const sub = item.querySelector(".unit-sub");
    if (sub) sub.textContent = STATE.radios[rid]?.device_model || "–";
  }

  // Topbar-Indikator ausblenden wenn niemand mehr spricht
  if (Object.keys(STATE.activePTTs).length === 0) {
    const pttContainer = document.getElementById("ptt-indicator-container");
    if (pttContainer) pttContainer.style.display = "none";
  }

  updateMapMarkerPTT(rid, false);

  // Dashboard-Calls aktualisieren
  fetch("/api/calls?limit=10")
    .then(r => r.json())
    .then(calls => { STATE.calls = calls; renderDashboardCalls(); updateKPIs(); });
}

// ─────────────────────────────────────────────────────────────────
// Emergency Events
// ─────────────────────────────────────────────────────────────────
function onEmergency(msg) {
  const rid = msg.radio_id;
  STATE.emergencies[rid] = msg;

  if (STATE.radios[rid]) {
    STATE.radios[rid].is_emergency    = true;
    STATE.radios[rid].emergency_type  = msg.call_type || "NOTRUF";
    STATE.radios[rid].emergency_since = msg.timestamp;
  }

  // Unit-Item Notfall-Styling
  const item = document.getElementById(`unit-item-${rid}`);
  if (item) {
    item.classList.add("emergency-active");
    const avatar = item.querySelector(".unit-avatar");
    if (avatar) { avatar.className = "unit-avatar emergency"; avatar.textContent = "\uD83D\uDEA8"; }
  }

  // Overlay
  const overlay = document.getElementById("emergency-overlay");
  if (overlay) overlay.classList.add("active");

  // Notruf-Zähler
  STATE.emergencyCount++;
  updateEmergencyBadge();
  renderEmergencyPanel();
  renderRadiosTable();

  // Alarm-Sound abspielen
  playAlarmSound();

  // Auto-Pan zur Notruf-Position
  if (STATE.map && STATE.gpsPositions[rid]) {
    STATE.map.setView(
      [STATE.gpsPositions[rid].lat, STATE.gpsPositions[rid].lon],
      Math.max(STATE.map.getZoom(), 15),
      { animate: true }
    );
  }

  // Tab auf Dashboard wechseln wenn nicht schon dort
  if (STATE.currentTab !== "dashboard" && STATE.currentTab !== "map") {
    switchTab("dashboard");
  }

  // Toast (persistent bis quittiert)
  const alias = STATE.radios[rid]?.alias || `Radio ${rid}`;
  showToast(`\uD83D\uDEA8 NOTRUF: ${alias}`, `${msg.call_type || "NOTRUF"} \u2013 Sofort quittieren!`, "emergency", 0);
}

function onEmergencyAck(msg) {
  const rid = msg.radio_id;
  delete STATE.emergencies[rid];

  if (STATE.radios[rid]) {
    STATE.radios[rid].is_emergency    = false;
    STATE.radios[rid].emergency_since = null;
  }

  // Overlay ausblenden wenn kein Notruf mehr aktiv
  const overlay = document.getElementById("emergency-overlay");
  if (Object.keys(STATE.emergencies).length === 0) {
    if (overlay) overlay.classList.remove("active");
    STATE.emergencyCount = 0;
  } else {
    STATE.emergencyCount = Object.keys(STATE.emergencies).length;
  }

  updateEmergencyBadge();
  renderEmergencyPanel();
  renderRadiosTable();
  renderSidebarUnits();

  showToast("\u2713 Notruf quittiert", `Radio ${rid}`, "success");
}

// ─────────────────────────────────────────────────────────────────
// RRS Events
// ─────────────────────────────────────────────────────────────────
function onRRSRegister(msg) {
  const rid = msg.radio_id;
  if (!STATE.radios[rid]) {
    STATE.radios[rid] = { radio_id: rid, alias: `Radio ${rid}`, online: true };
  } else {
    STATE.radios[rid].online = true;
  }
  renderSidebarUnits();
  updateKPIs();
  showToast(`✅ ${STATE.radios[rid]?.alias || rid} online`, `${msg.slot}`, "success");
}

function onRRSOffline(msg) {
  const rid = msg.radio_id;
  if (STATE.radios[rid]) STATE.radios[rid].online = false;
  renderSidebarUnits();
  updateKPIs();
}

// ─────────────────────────────────────────────────────────────────
// SMS Events
// ─────────────────────────────────────────────────────────────────
function onSMSReceived(msg) {
  STATE.sms.unshift(msg);
  if (STATE.currentTab !== "sms") {
    STATE.unreadSMS++;
    updateSMSBadge();
  }
  renderSMSList();
  renderDashboardSMS();
  showToast("💬 Nachricht", `${msg.sender_id}: ${msg.text.substring(0, 40)}`, "info");
}

// ─────────────────────────────────────────────────────────────────
// Hardware Updates
// ─────────────────────────────────────────────────────────────────
function onRepeaterUpdate(msg) {
  // msg kommt jetzt als spread state: {type, online, trap_count, alarm_count, ...}
  const s = (msg.online !== undefined) ? msg : (msg.state || msg.data || {});
  STATE.hw.repeater = s;

  const isOnline  = s.online === true;
  const hasAlarm  = (s.alarm_count || 0) > 0 || ((s.active_alarms || []).length > 0);

  setBadge("badge-repeater", hasAlarm ? "alarm" : (isOnline ? "online" : "offline"));
  setChip("dash-repeater-chip", isOnline, hasAlarm);
  setChip("infra-repeater-chip", isOnline, hasAlarm);

  // Repeater Header Info (Rufname / Modell / DMR-ID)
  const aliasParts = [];
  if (s.radio_alias && s.radio_alias !== "—") aliasParts.push(s.radio_alias);
  if (s.radio_id) aliasParts.push(`DMR-ID: ${s.radio_id}`);
  setText("rpt-head-info", aliasParts.length > 0 ? aliasParts.join(" | ") : "");

  // Work State String
  const wsText = s.work_state_str || (s.work_state === 1 ? "TX (Senden)" : (s.work_state === 0 ? "Standby" : "—"));

  // Dashboard Quick-View
  const dashChn = (s.channel_name && s.channel_name !== "—") ? s.channel_name : (s.channel_num ? `Kanal ${s.channel_num}` : "—");
  setText("dash-rpt-chn",   dashChn);
  setText("dash-rpt-temp",  s.temp_wert  != null ? `${s.temp_wert.toFixed(1)} °C`  : "—");
  setText("dash-rpt-vswr",  s.vswr_wert  != null ? `${s.vswr_wert.toFixed(2)} : 1` : "—");
  setText("dash-rpt-volt",  s.volt_wert  != null ? `${s.volt_wert.toFixed(1)} V`   : "—");
  setText("dash-rpt-txrx",  wsText);

  // Infrastruktur-Tab: Status- & Kanalzeile
  const chnName = (s.channel_name && s.channel_name !== "—") ? s.channel_name : (s.channel_num ? `Kanal ${s.channel_num}` : "Standard-Kanal");
  const zoneName = (s.zone_alias && s.zone_alias !== "—") ? ` (${s.zone_alias})` : "";
  setText("rpt-chn-zone", `${chnName}${zoneName}`);

  const chnType = s.channel_type || "Digital (DMR)";
  const txLvl = s.tx_power_level ? ` | ${s.tx_power_level}` : "";
  setText("rpt-work-pwr", `${wsText} | ${chnType}${txLvl}`);

  // Infrastruktur-Tab: 4 Haupt-Messwerte
  setText("rpt-temp", s.temp_wert != null ? `${s.temp_wert.toFixed(1)} °C` : "—");
  setText("rpt-volt", s.volt_wert != null ? `${s.volt_wert.toFixed(1)} V`  : "—");
  
  const pwrDisplay = (s.fw_pwr_watt != null)
    ? `${s.fw_pwr_watt.toFixed(1)} W` + (s.ref_pwr_watt != null ? ` (Ref: ${s.ref_pwr_watt.toFixed(1)} W)` : "")
    : "—";
  setText("rpt-txpwr", pwrDisplay);

  // VSWR mit Ampelfarbe
  const vswrEl = document.getElementById("rpt-vswr");
  if (vswrEl) {
    if (s.vswr_wert != null) {
      vswrEl.textContent = `${s.vswr_wert.toFixed(2)} : 1`;
      vswrEl.className = "metric-value " + (s.vswr_wert >= 2.8 ? "critical" : (s.vswr_wert >= 2.0 ? "warning" : "good"));
    } else {
      vswrEl.textContent = "—";
      vswrEl.className = "metric-value";
    }
  }

  // Frequenzen & Signalpegel
  setText("rpt-txfreq", s.tx_freq_mhz != null ? `${s.tx_freq_mhz.toFixed(4)} MHz` : "—");
  setText("rpt-rxfreq", s.rx_freq_mhz != null ? `${s.rx_freq_mhz.toFixed(4)} MHz` : "—");
  setText("rpt-rssi1",  s.rssi_slot1  != null ? `${s.rssi_slot1} dBm`            : "—");
  setText("rpt-rssi2",  s.rssi_slot2  != null ? `${s.rssi_slot2} dBm`            : "—");

  // Geräte-Details
  const modelText = s.model_name || "Hytera HR1065";
  const modelNo = s.model_no ? ` [${s.model_no}]` : "";
  const bandText = s.freq_band ? ` (${s.freq_band})` : "";
  const snText = (s.serial_number && s.serial_number !== "—") ? ` · SN: ${s.serial_number}` : "";
  setText("rpt-model-sn", `${modelText}${modelNo}${bandText}${snText}`);

  const fwText = (s.firmware_version && s.firmware_version !== "—") ? s.firmware_version : "—";
  const rcdbText = (s.rcdb_version && s.rcdb_version !== "—") ? ` | RCDB: ${s.rcdb_version}` : "";
  setText("rpt-fw-rcdb", `${fwText}${rcdbText}`);

  setText("rpt-fanspeed", s.fan_speed != null ? `${s.fan_speed} RPM` : "Temperaturgeregelt");

  const pwrSrc = s.power_source || "DC Netzteil";
  const battV = s.batt_volt_wert != null ? ` (${s.batt_volt_wert.toFixed(1)} V)` : "";
  setText("rpt-powersource", `${pwrSrc}${battV}`);

  setText("rpt-uptime", s.uptime_str || "—");

  // Repeating & Knockdown Status-Badge & Control Button
  const isRepeating = s.repeating_enabled !== false && s.repeating_state !== 1;
  const repBadge = document.getElementById("rpt-repeating-badge");
  if (repBadge) {
    repBadge.textContent = isRepeating ? "Repeating Aktiv" : "Repeating Unterdrückt";
    repBadge.className = `chip ${isRepeating ? "chip-online" : "chip-warning"}`;
  }
  STATE.repeaterKnockdown = !isRepeating;
  updateRepeaterKnockdownButton(!isRepeating);

  // LAN-Port IF-MIB Stats
  const speed = s.eth_speed_mbps || 100;
  const oper = s.eth_oper_status || (isOnline ? "Up" : "—");
  const errs = (s.eth_in_errors || 0) + (s.eth_out_errors || 0);
  const lanText = isOnline ? `${speed} Mbit/s (${oper}) · ${errs} Err` : "—";
  setText("rpt-lan-stats", lanText);

  // Standby-Rauschflur
  const nfText = s.noise_floor_dbm != null ? `${s.noise_floor_dbm} dBm` : "— dBm";
  setText("rpt-noise-floor", nfText);

  // Trap-Counter
  setText("rpt-trap-count",  s.trap_count  != null ? String(s.trap_count)  : "0");
  setText("rpt-alarm-count", s.alarm_count != null ? String(s.alarm_count) : "0");

  // NVRAM Logbuch
  if (s.log_count != null || (s.recent_logs && s.recent_logs.length > 0)) {
    const count = s.log_count != null ? s.log_count : s.recent_logs.length;
    setText("rpt-log-count-badge", `${count} Einträge`);
    if (s.recent_logs && s.recent_logs.length > 0) {
      renderRepeaterLogs(s.recent_logs);
    }
  }

  // Alarm-Flags & Warnungen
  const alarmsEl = document.getElementById("rpt-alarms");
  if (alarmsEl) {
    const active = s.active_alarms || [];
    const warns = s.warnings || [];
    if (active.length > 0 || warns.length > 0) {
      let html = "";
      for (const a of active) {
        html += `<div class="chip chip-emergency">&#9888; ${a.label || a.oid_label || a.oid_key || "Alarm"}</div>`;
      }
      for (const w of warns) {
        html += `<div class="chip chip-warning">&#9889; ${w}</div>`;
      }
      alarmsEl.innerHTML = html;
    } else if (isOnline) {
      alarmsEl.innerHTML = '<div class="chip chip-online">\u2713 Alle Systeme & HF-Parameter im Normbereich</div>';
    } else {
      alarmsEl.innerHTML = '<div class="chip chip-offline">Keine Verbindung zum Repeater</div>';
    }
  }
}

function renderRepeaterLogs(logs) {
  const tbody = document.getElementById("rpt-log-tbody");
  if (!tbody) return;
  if (!logs || logs.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" style="padding:8px;text-align:center;color:var(--text-muted);">Keine Fehler im NVRAM gespeichert</td></tr>';
    return;
  }
  tbody.innerHTML = logs.map(l => {
    const badgeClass = l.is_active ? "chip-emergency" : "chip-online";
    return `<tr style="border-bottom:1px solid var(--border-color);font-size:11px;">
      <td style="padding:4px 8px;font-family:var(--font-mono);">${escapeHTML(l.time_str || (l.uptime_s + 's'))}</td>
      <td style="padding:4px 8px;font-weight:500;">${escapeHTML(l.alarm_name || ('Code #' + l.alarm_code))}</td>
      <td style="padding:4px 8px;"><span class="chip ${badgeClass}" style="font-size:9px;padding:2px 6px;">${escapeHTML(l.status)}</span></td>
    </tr>`;
  }).join("");
}

async function loadRepeaterLogs() {
  try {
    const res = await fetch("/api/hardware/repeater/logs?count=15");
    if (!res.ok) return;
    const data = await res.json();
    if (data.status === "ok" && Array.isArray(data.logs)) {
      renderRepeaterLogs(data.logs);
      setText("rpt-log-count-badge", `${data.logs.length} Einträge`);
    }
  } catch (e) {
    console.warn("Fehler beim Laden der Repeater-Logs:", e);
  }
}



function onRepeaterAlarm(msg) {
  STATE.snmpEvents.unshift({
    oid_key: msg.oid_key,
    oid_label: msg.oid_label,
    value: msg.value,
    alarm_level: msg.alarm_level,
    created_at: new Date().toISOString(),
  });
  if (STATE.snmpEvents.length > 100) STATE.snmpEvents.pop();
  renderSNMPEventsTable();
  showToast(`⚠ Repeater-Alarm: ${msg.oid_label}`, `Wert: ${msg.value}`, "warning");
}

function onUPSUpdate(msg) {
  // Neues Format: {type:"ups_update", online, batt_pct, ...} (spread, kein .data)
  const d = (msg.online !== undefined || msg.batt_pct !== undefined) ? msg : (msg.data || {});
  STATE.hw.ups = d;

  const online = d.online === true;
  const onBatt = d.netz_status === "Batteriebetrieb!" || d.netz_ok === false;
  const bypass = d.bypass_active === true;
  const battLow = d.batt_low === true;

  // Badge
  const badgeState = !online ? "offline" : (onBatt ? "warning" : (battLow ? "warning" : "online"));
  setBadge("badge-ups", badgeState);

  // Chips
  setChip("dash-ups-chip",  online, onBatt || battLow);
  setChip("infra-ups-chip", online, onBatt || battLow);

  // Topbar-Label
  const upsLabel = document.getElementById("badge-ups-label");
  if (upsLabel) upsLabel.textContent = onBatt ? "USV Bat!" : (bypass ? "USV Byp!" : "USV");

  // Batterie
  const pct = d.batt_pct ?? 0;
  setProgressBar("dash-ups-batt-bar", pct);
  setProgressBar("ups-batt-bar",      pct);

  // Batterie-Temperatur Warnung
  if ((d.batt_temp_c || 0) > 35) {
    showToast("⚠ USV Batterietemperatur", `${d.batt_temp_c} °C – überprüfen!`, "warning");
  }

  // USV Restlaufzeit-Warnung (<15 min auf Batterie)
  if (onBatt && d.batt_runtime_min != null && d.batt_runtime_min < 15) {
    showToast("🚨 USV-RESTLAUFZEIT KRITISCH!", `Akkubetrieb! Nur noch ${d.batt_runtime_min} Minuten verbleibend! Stromversorgung wiederherstellen!`, "emergency", 8000);
    playAlarmSound();
  }

  // Fault-Alarm
  if (d.fault_active && d.fault_code) {
    showToast("⚠ USV Fehler", `Fehlercode: ${d.fault_code}`, "emergency");
  }

  // ── Dashboard
  setText("dash-ups-batt-pct", d.batt_pct         != null ? `${d.batt_pct}%`                  : "\u2014");
  setText("dash-ups-runtime",  d.batt_runtime_min  != null ? `${d.batt_runtime_min} min`       : "\u2014");
  setText("dash-ups-load",     d.load_pct          != null ? `${d.load_pct}%`                  : "\u2014");

  // ── Infrastruktur-Tab – alle verf\u00fcgbaren Werte
  setText("ups-status",       d.netz_status        || "\u2014");
  setText("ups-batt-status",  d.batt_status        || "\u2014");
  setText("ups-batt-pct",     d.batt_pct           != null ? `${d.batt_pct}%`                  : "\u2014");
  setText("ups-runtime",      d.batt_runtime_min   != null ? `${d.batt_runtime_min} min`       : "\u2014");
  setText("ups-load",         d.load_pct           != null ? `${d.load_pct}%`                  : "\u2014");

  // Eingang
  setText("ups-in-volt",   d.input_volt != null  ? `${d.input_volt} V`   : "\u2014");
  setText("ups-in-freq",   d.input_freq != null  ? `${d.input_freq} Hz`  : "\u2014");
  setText("ups-in-curr",   d.input_curr != null  ? `${d.input_curr} A`   : "\u2014");

  // Ausgang
  setText("ups-out-volt",  d.output_volt     != null ? `${d.output_volt} V`      : "—");
  setText("ups-out-freq",  d.output_freq     != null ? `${d.output_freq} Hz`     : "—");
  setText("ups-out-curr",  d.output_curr     != null ? `${d.output_curr} A`      : "—");
  setText("ups-out-power", d.output_power_w  != null ? `${d.output_power_w} W`  : "—");

  // Erweiterte elektrische Leistung & Energie
  const pwrW = d.output_power_w != null ? `${d.output_power_w} W` : "— W";
  const pwrVA = d.output_power_va != null ? `${d.output_power_va} VA` : "— VA";
  setText("ups-power-pwr", `${pwrW} / ${pwrVA}`);
  setText("ups-power-factor", d.power_factor != null ? d.power_factor.toFixed(2) : "—");
  setText("ups-energy-kwh", d.energy_kwh != null ? `${Number(d.energy_kwh).toFixed(3)} kWh` : "0.000 kWh");

  // Akku-Gesundheit (SOH)
  const soh = d.battery_soh_pct ?? 100;
  setText("ups-soh", `${soh}%`);
  colorizeMetric("ups-soh", soh, [[60, "danger"], [80, "warn"], [100, "good"]]);

  // Netzausfälle & Akkulaufzeit
  const transfers = d.transfers_to_battery ?? 0;
  const battSecs = Math.round(d.total_battery_seconds || 0);
  const battDurStr = battSecs < 60 ? `${battSecs}s` : formatUptime(battSecs);
  setText("ups-transfers", `${transfers} Mal (${battDurStr})`);

  // Last-Segmente (1 / 2)
  const o1 = d.outlet_group_1 !== false ? "Ein" : "Aus";
  const o2 = d.outlet_group_2 !== false ? "Ein" : "Aus";
  setText("ups-outlets", `Seg 1: ${o1} · Seg 2: ${o2}`);

  // Batterie
  setText("ups-batt-volt", d.batt_volt_v    != null ? `${d.batt_volt_v} V`      : "—");
  setText("ups-batt-curr", d.batt_curr_a    != null ? `${d.batt_curr_a} A`      : "—");
  setText("ups-batt-temp", d.batt_temp_c    != null ? `${d.batt_temp_c} °C`     : "—");

  // Intern & Bypass
  setText("ups-temp",        d.internal_temp_c != null ? `${d.internal_temp_c} °C` : "—");
  setText("ups-bypass",      d.bypass_active   === true ? "Aktiv" : "Inaktiv");
  setText("ups-bypass-volt", d.bypass_volt     != null ? `${d.bypass_volt.toFixed(1)} V` : "— V");
  setText("ups-fault-code",  d.fault_code      != null ? String(d.fault_code) : "—");

  // Farb-Kodierungen
  colorizeMetric("ups-batt-pct",  pct,                    [[20, "danger"], [40, "warn"], [100, "good"]]);
  colorizeMetric("ups-runtime",   d.batt_runtime_min ?? 999, [[10, "danger"], [30, "warn"], [999, "good"]]);
  colorizeMetric("ups-load",      d.load_pct ?? 0,           [[80, "good"], [90, "warn"], [100, "danger"]]);
  colorizeProgressBar("ups-batt-bar",      pct, [[20, "danger"], [40, "warn"], [100, "good"]]);
  colorizeProgressBar("dash-ups-batt-bar", pct, [[20, "danger"], [40, "warn"], [100, "good"]]);

  // USV-Chart aktualisieren
  updateUPSChart(d);
}

function onZTEUpdate(msg) {
  // Neues Format: spread state (kein .data wrapper)
  const d = (msg.rsrp !== undefined || msg.online !== undefined) ? msg : (msg.data || {});
  STATE.hw.zte = d;

  const online    = d.online === true;
  const connected = d.wan_status === "Verbunden";

  setBadge("badge-zte", online && connected ? "online" : (online ? "warning" : "offline"));
  setChip("dash-zte-chip",  online && connected, !connected && online);
  setChip("infra-zte-chip", online && connected, !connected && online);

  // ZTE Topbar Label
  const zteLabel = document.getElementById("badge-zte-label");
  if (zteLabel) zteLabel.textContent = d.network_type || "LTE";

  // Dashboard
  setText("dash-zte-type", d.network_type || "\u2014");
  setText("dash-zte-rsrp", d.rsrp != null ? `${d.rsrp} dBm` : "\u2014");
  setText("dash-zte-ip",   d.wan_ip || "\u2014");

  // Infrastruktur-Tab
  setText("zte-nettype",  d.network_type || "\u2014");
  setText("zte-rsrp",     d.rsrp  != null ? `${d.rsrp} dBm`  : "\u2014");
  setText("zte-rsrq",     d.rsrq  != null ? `${d.rsrq} dB`   : "\u2014");
  setText("zte-sinr",     d.sinr  != null ? `${d.sinr} dB`   : "\u2014");
  setText("zte-rssi",     d.rssi  != null ? `${d.rssi} dBm`  : "\u2014");
  setText("zte-wan-ip",   d.wan_ip   || "\u2014");
  setText("zte-network",  d.network_name || "\u2014");
  setText("zte-band",     d.band   || "\u2014");
  setText("zte-cell-id",  d.cell_id != null ? String(d.cell_id)  : "\u2014");

  // Durchsatz
  const dl = d.rx_bytes != null ? formatBytes(d.rx_bytes) + "/s" : "\u2014";
  const ul = d.tx_bytes != null ? formatBytes(d.tx_bytes) + "/s" : "\u2014";
  setText("zte-dl", dl);
  setText("zte-ul", ul);

  // Akku (falls ZTE-Router einen hat)
  if (d.battery_pct != null) {
    setText("zte-battery", `${d.battery_pct}%`);
    setText("zte-charging", d.charging ? "Laden" : "Entladen");
  }

  // Signal-Qualit\u00e4t (abgeleitet aus RSRP)
  const rsrp = d.rsrp ?? -120;
  const quality = rsrp >= -80 ? "Ausgezeichnet" : rsrp >= -90 ? "Gut" : rsrp >= -100 ? "M\u00e4\u00dfig" : rsrp >= -110 ? "Schwach" : "Sehr schwach";
  setText("zte-signal-quality", quality);

  // Signal-Bars (CSS)
  const barsEl = document.getElementById("zte-signal-bars");
  const barCount = rsrp >= -80 ? 5 : rsrp >= -90 ? 4 : rsrp >= -100 ? 3 : rsrp >= -110 ? 2 : 1;
  if (barsEl) barsEl.setAttribute("data-bars", barCount);
}

function onOmadaUpdate(msg) {
  // Neues Format: spread state (kein .data wrapper)
  const d = (msg.hostname !== undefined || msg.online !== undefined) ? msg : (msg.data || {});
  STATE.hw.omada = d;

  const online = d.online === true;
  setChip("infra-omada-chip", online, false);

  // Router-Info
  setText("omada-hostname",   d.hostname   || "ER606");
  setText("omada-firmware",   d.firmware   || "\u2014");
  setText("omada-wan-status", d.wan_status || (online ? "\u2713 Verbunden" : "\u2715 Nicht erreichbar"));
  setText("omada-wan-ip",     d.wan_ip     || "\u2014");

  // Uptime
  const uptime = d.uptime_s != null ? formatUptime(d.uptime_s) : (online ? "Unbekannt" : "\u2014");
  setText("omada-uptime", uptime);

  // Durchsatz
  const dl = d.wan_rx_bytes != null ? formatBytes(d.wan_rx_bytes) : (d.wan_rx_bps != null ? formatBytes(d.wan_rx_bps) + "/s" : "\u2014");
  const ul = d.wan_tx_bytes != null ? formatBytes(d.wan_tx_bytes) : (d.wan_tx_bps != null ? formatBytes(d.wan_tx_bps) + "/s" : "\u2014");
  setText("omada-dl", dl);
  setText("omada-ul", ul);

  // Clients & Performance
  setText("omada-clients", d.clients_count != null ? String(d.clients_count) : "\u2014");
  if (d.cpu_pct != null) {
    setText("omada-cpu", `${d.cpu_pct}%`);
    setProgressBar("omada-cpu-bar", d.cpu_pct);
  }
  if (d.mem_pct != null) {
    setText("omada-mem", `${d.mem_pct}%`);
    setProgressBar("omada-mem-bar", d.mem_pct);
  }

  // Ports
  const portsEl = document.getElementById("omada-ports-list");
  if (portsEl && d.port_states) {
    portsEl.innerHTML = d.port_states.map(p =>
      `<div class="infra-row">
        <span class="infra-key">${p.name || "Port " + p.index}</span>
        <div style="display:flex;align-items:center;gap:8px;">
          <span class="chip ${p.up ? 'chip-online' : 'chip-offline'}" style="font-size:10px">${p.up ? "▲ UP" : "▼ DOWN"}</span>
          ${p.speed_mbps ? `<span class="infra-val">${p.speed_mbps} Mbit</span>` : ""}
        </div>
      </div>`
    ).join("");
  }
}

function onPacketDump(packet) {
  if (!STATE.dumpActive) return;
  STATE.packets.unshift(packet);
  if (STATE.packets.length > 500) STATE.packets.pop();
  renderPacketList();
}

// ─────────────────────────────────────────────────────────────────
// Leaflet Map
// ─────────────────────────────────────────────────────────────────
function initMap() {
  if (STATE.map) return;

  const map = L.map("map", {
    center: [51.2325, 6.7800],
    zoom:   14,
    zoomControl: true,
    attributionControl: true,
  });
  STATE.map = map;

  // Standard: OSM mit lokalem Tile-Cache
  applyMapProvider(STATE.settings.cfg_map_provider || "osm");

  // Bestehende Marker auf Karte zeichnen
  Object.values(STATE.gpsPositions).forEach(g => updateMapMarker(g.radio_id, g));
  Object.values(STATE.markers).forEach(m => drawDBMarkerOnMap(m));
  STATE.geofences.forEach(g => drawGeofenceOnMap(g));

  // Karten-Toolbar-Events
  document.getElementById("btn-map-center")?.addEventListener("click", centerMapOnUnits);
  document.getElementById("btn-toggle-breadcrumbs")?.addEventListener("click", () => {
    STATE.breadcrumbsOn = !STATE.breadcrumbsOn;
    Object.values(STATE.mapPaths).forEach(p => {
      if (STATE.breadcrumbsOn) p.addTo(map);
      else map.removeLayer(p);
    });
  });
  document.getElementById("btn-toggle-rssi")?.addEventListener("click", toggleRSSILayer);

  // Karten-Klick → Feste Radio-Position, Marker setzen oder Zone zeichnen
  map.on("click", e => {
    if (STATE.settingRadioPositionId) {
      const rid = STATE.settingRadioPositionId;
      STATE.settingRadioPositionId = null;
      const mapEl = document.getElementById("map");
      if (mapEl) mapEl.style.cursor = "";
      saveRadioFixedPosition(rid, e.latlng.lat, e.latlng.lng);
      return;
    }
    if (STATE.addingMarker) {
      setMarkerMode(false);
      openMarkerModal(e.latlng.lat, e.latlng.lng);
      return;
    }
    if (STATE.drawingGeofence) {
      handleGeofenceMapClick(e.latlng);
      return;
    }
  });

  centerMapOnUnits();
}

function applyMapProvider(provider) {
  if (STATE.tileLayer) STATE.map.removeLayer(STATE.tileLayer);

  const apiKey = STATE.settings.cfg_map_api_key || "";
  let layer;

  switch (provider) {
    case "google_roadmap":
      layer = L.tileLayer(
        `https://mt{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}&key=${apiKey}`,
        { subdomains: "0123", attribution: "© Google", maxZoom: 20 }
      );
      break;
    case "google_satellite":
      layer = L.tileLayer(
        `https://mt{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}&key=${apiKey}`,
        { subdomains: "0123", attribution: "© Google", maxZoom: 20 }
      );
      break;
    case "google_hybrid":
      layer = L.tileLayer(
        `https://mt{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}&key=${apiKey}`,
        { subdomains: "0123", attribution: "© Google", maxZoom: 20 }
      );
      break;
    default: // osm
      layer = L.tileLayer("/tiles/{z}/{x}/{y}.png", {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        maxZoom: 19,
      });
      break;
  }

  layer.addTo(STATE.map);
  STATE.tileLayer = layer;
}

// ── Intelligente taktische Funkrufkürzel ────────────────────────
function getTacticalBadge(alias, rid) {
  if (!alias) return rid ? rid.toString().slice(-3) : "–";
  const str = alias.trim();

  // 1. Taktische Kennung mit Slash oder Bindestrich (z.B. "1/11", "44-1", "1-44-1", "2/10")
  const slashMatch = str.match(/\b\d+[\/\-]\d+(?:[\/\-]\d+)?\b/);
  if (slashMatch) return slashMatch[0];

  // 2. Fahrzeugkürzel + Nummer (z.B. "ELW 1", "HLF 20", "DLK 23", "RTW 1", "NEF 2", "MTF")
  const fzgMatch = str.match(/\b(ELW|HLF|DLK|MTF|RTW|KTW|GW|RW|LF|TLF|KDOW|VRW|NEF|VSt)\s*(\d+)?\b/i);
  if (fzgMatch) {
    const type = fzgMatch[1].toUpperCase();
    const num = fzgMatch[2] ? fzgMatch[2] : "";
    return num ? (type.length > 2 ? `${type.slice(0, 2)}${num}` : `${type}${num}`) : type;
  }

  // 3. Einzelne Nummer am Ende oder im String (z.B. "Florian 11" -> "11", "Fahrzeug 4" -> "4")
  const numMatch = str.match(/\b\d{1,3}\b/);
  if (numMatch) return numMatch[0];

  // 4. Initialen von Mehrwort-Rufnamen (z.B. "Einsatzleitung West" -> "ELW")
  const words = str.split(/[\s\-_]+/).filter(w => w.length > 0);
  if (words.length > 1) {
    return words.map(w => w[0]).join("").slice(0, 3).toUpperCase();
  }

  // 5. Fallback: Erste 3 Buchstaben
  return str.slice(0, 3).toUpperCase();
}

// ── Funkgeräte-Marker Icon ─────────────────────────────────────
function makeRadioIcon(rid, online, isPTT, isEmergency, heading = null) {
  const radio = STATE.radios[rid];
  const alias = radio?.alias || `Radio ${rid}`;
  const badgeText = getTacticalBadge(alias, rid);

  let statusClass = "offline";
  if (isEmergency)     statusClass = "emergency";
  else if (isPTT)      statusClass = "ptt";
  else if (online)     statusClass = "online";

  const pulseRing = (isPTT || isEmergency)
    ? `<div class="radio-pulse-ring ${isEmergency ? 'emergency' : 'ptt'}"></div>`
    : "";

  const arrow = (heading != null && heading !== undefined)
    ? `<div class="radio-marker-arrow" style="transform: rotate(${heading}deg);"></div>`
    : "";

  const tagDisplay = STATE.showMapLabels ? "block" : "none";
  const tagHtml = `<div class="radio-marker-tag" style="display:${tagDisplay}">${escapeHTML(alias)}</div>`;

  const html = `
    <div class="radio-marker-wrapper">
      ${pulseRing}
      ${arrow}
      <div class="radio-marker-core ${statusClass}" title="${escapeHTML(alias)} (ID: ${rid})">
        ${badgeText}
      </div>
      ${tagHtml}
    </div>`;

  return L.divIcon({
    html,
    className: "",
    iconSize: [44, 44],
    iconAnchor: [22, 22],
    popupAnchor: [0, -22],
  });
}

function updateMapMarker(rid, gps) {
  if (!STATE.map) return;
  const lat = gps.lat;
  const lon = gps.lon;
  if (!lat || !lon) return;

  const radio = STATE.radios[rid] || {};
  const isOnline    = radio.online !== false;
  const isPTT       = !!STATE.activePTTs[rid];
  const isEmergency = !!STATE.emergencies[rid];
  const heading     = gps.heading != null ? gps.heading : null;
  const icon = makeRadioIcon(rid, isOnline, isPTT, isEmergency, heading);

  if (STATE.mapMarkers[rid]) {
    STATE.mapMarkers[rid].setLatLng([lat, lon]).setIcon(icon);
  } else {
    const marker = L.marker([lat, lon], { icon })
      .addTo(STATE.map)
      .bindPopup(() => buildRadioPopup(rid));
    STATE.mapMarkers[rid] = marker;
  }

  // Karten-Zusammenfassung
  const onlineCount = Object.keys(STATE.mapMarkers).length;
  setText("map-units-summary", `${onlineCount} auf Karte`);
}

function updateMapMarkerPTT(rid, isPTT) {
  if (!STATE.mapMarkers[rid]) return;
  const radio = STATE.radios[rid] || {};
  const isOnline    = radio.online !== false;
  const isEmergency = !!STATE.emergencies[rid];
  const gps         = STATE.gpsPositions[rid] || {};
  const heading     = gps.heading != null ? gps.heading : null;
  STATE.mapMarkers[rid].setIcon(makeRadioIcon(rid, isOnline, isPTT, isEmergency, heading));
}

function updateMapPath(rid) {
  if (!STATE.map || !STATE.breadcrumbsOn) return;
  const crumbs = STATE.breadcrumbs[rid] || [];
  if (crumbs.length < 2) return;

  const color = STATE.emergencies[rid] ? "#f85149" : (STATE.activePTTs[rid] ? "#2f81f7" : "#8b949e");

  if (STATE.mapPaths[rid]) {
    STATE.mapPaths[rid].setLatLngs(crumbs);
  } else {
    STATE.mapPaths[rid] = L.polyline(crumbs, {
      color,
      weight: 2,
      opacity: 0.6,
      dashArray: "4 4",
    }).addTo(STATE.map);
  }
}

function buildRadioPopup(rid) {
  const radio = STATE.radios[rid] || {};
  const gps   = STATE.gpsPositions[rid] || {};
  const ptt   = STATE.activePTTs[rid];
  const em    = STATE.emergencies[rid];
  const isFixed = Boolean(radio.fixed_lat != null || gps.is_fixed);

  let statusBadge = `<span class="t-popup-badge prio-normal">Online</span>`;
  if (em) statusBadge = `<span class="t-popup-badge prio-critical">🚨 Notruf</span>`;
  else if (ptt) statusBadge = `<span class="t-popup-badge prio-high">🎙 PTT</span>`;
  else if (!radio.online && radio.online !== undefined) statusBadge = `<span class="t-popup-badge prio-low">Offline</span>`;
  if (isFixed) {
    statusBadge += ` <span class="t-popup-badge" style="background:rgba(59,130,246,0.2);color:var(--blue-400);border:1px solid rgba(59,130,246,0.3)">📌 Fest</span>`;
  }

  const coords = (gps.lat && gps.lon) ? `${gps.lat.toFixed(6)}, ${gps.lon.toFixed(6)}` : "–";
  const speedStr = gps.speed != null ? `${gps.speed.toFixed(1)} km/h` : "–";
  const headingStr = gps.heading != null ? `${gps.heading}°` : "–";
  const rssiStr = gps.rssi != null ? `${gps.rssi.toFixed(0)} dBm` : "–";

  return `
    <div class="t-popup">
      <div class="t-popup-header">
        <div class="t-popup-title">
          <span>📻</span>
          <span>${escapeHTML(radio.alias || "Radio " + rid)}</span>
        </div>
        ${statusBadge}
      </div>
      <div class="t-popup-rows">
        <div class="t-popup-row"><span class="t-key">DMR-ID</span><span class="t-val">${rid}</span></div>
        <div class="t-popup-row"><span class="t-key">Gerät</span><span class="t-val">${escapeHTML(radio.device_model || "Hytera")}</span></div>
        <div class="t-popup-row"><span class="t-key">Position</span><span class="t-val">${coords}${isFixed ? ' (Fest)' : ''}</span></div>
        <div class="t-popup-row"><span class="t-key">Geschw. / Kurs</span><span class="t-val">${speedStr} · ${headingStr}</span></div>
        <div class="t-popup-row"><span class="t-key">Signal (RSSI)</span><span class="t-val">${rssiStr}</span></div>
      </div>
      <div class="t-popup-actions" style="display:flex;gap:4px;flex-wrap:wrap;">
        ${em ? `<button class="btn btn-sm btn-danger" onclick="ackEmergency(${rid})" style="flex:1;padding:3px 6px;font-size:11px;">✓ Quittieren</button>` : ""}
        <button class="btn btn-sm btn-secondary" onclick="focusOnMap(${rid})" style="flex:1;padding:3px 6px;font-size:11px;">⊙ Zentrieren</button>
        <button class="btn btn-sm" onclick="startSetRadioPosition(${rid})" style="flex:1;padding:3px 6px;font-size:11px;" title="Feste Position auf Karte platzieren / anpassen">📍 Pos. anpassen</button>
        ${radio.fixed_lat != null ? `<button class="btn btn-sm btn-icon" onclick="clearRadioFixedPosition(${rid})" style="color:var(--text-muted);padding:3px 6px;font-size:11px;" title="Feste Position entfernen">✕</button>` : ""}
      </div>
    </div>`;
}

function centerMapOnUnits() {
  if (!STATE.map) return;
  const points = Object.values(STATE.gpsPositions).filter(g => g.lat && g.lon).map(g => [g.lat, g.lon]);

  // Wenn keine Einheiten GPS haben, beziehe gesetzte taktische Marker ein
  if (points.length === 0) {
    Object.values(STATE.markers).forEach(m => {
      if (m.lat && m.lon) points.push([m.lat, m.lon]);
    });
  }

  if (points.length === 0) {
    showToast("Zentrieren", "Keine Einheiten oder Marker mit Koordinaten vorhanden", "info");
    return;
  }
  if (points.length === 1) {
    STATE.map.setView(points[0], 16);
    return;
  }
  const bounds = L.latLngBounds(points);
  STATE.map.fitBounds(bounds, { padding: [50, 50], maxZoom: 17 });
}

function focusOnMap(rid) {
  const radio = STATE.radios[rid];
  const gps = STATE.gpsPositions[rid] || (radio?.last_lat && radio?.last_lon ? { lat: radio.last_lat, lon: radio.last_lon } : null);
  if (gps && STATE.map) {
    switchTab("map");
    STATE.map.setView([gps.lat, gps.lon], 17);
    STATE.mapMarkers[rid]?.openPopup();
  } else {
    showToast(`Keine GPS-Position für ${radio?.alias || 'Radio ' + rid} verfügbar.`, "info");
  }
}

// ── Taktische Einsatzmarker (Pins) ──────────────────────────
const TACTICAL_TYPES = {
  feuer:          { icon: "🔥", label: "Feuer / Brand",         prioDefault: "high" },
  feuerwehr:      { icon: "🚒", label: "Feuerwehr / Löschzug",   prioDefault: "normal" },
  rettung:        { icon: "🚑", label: "Rettungsdienst / Notarzt", prioDefault: "high" },
  polizei:        { icon: "🚓", label: "Polizei / Absicherung",  prioDefault: "normal" },
  warnung:        { icon: "⚠️", label: "Gefahrenbereich",        prioDefault: "critical" },
  absperrung:     { icon: "🚧", label: "Absperrung / Sperre",    prioDefault: "normal" },
  wasser:         { icon: "💧", label: "Wasserentnahme",        prioDefault: "low" },
  sammelpunkt:    { icon: "🏁", label: "Sammelplatz / VSt",      prioDefault: "normal" },
  einsatzleitung: { icon: "🏢", label: "Einsatzleitung / ELW",  prioDefault: "normal" },
  info:           { icon: "ℹ️", label: "Information",           prioDefault: "low" },
};

function drawDBMarkerOnMap(marker) {
  if (!STATE.map) return;
  const info = TACTICAL_TYPES[marker.typ] || { icon: "📍", label: marker.typ || "Marker", prioDefault: "normal" };
  const prio = (marker.prioritaet || info.prioDefault || "normal").toLowerCase();
  const desc = marker.beschreibung ? escapeHTML(marker.beschreibung) : "";
  const tagDisplay = STATE.showMapLabels ? "block" : "none";

  const pinHtml = `
    <div class="tactical-pin-wrapper prio-${prio}" title="${escapeHTML(info.label)}${desc ? ': ' + desc : ''}">
      <div class="tactical-pin-body">${info.icon}</div>
      <div class="tactical-pin-tip"></div>
      <div class="tactical-pin-tag" style="display:${tagDisplay}">${desc || info.label}</div>
    </div>`;

  const customIcon = L.divIcon({
    html: pinHtml,
    className: "",
    iconSize: [34, 42],
    iconAnchor: [17, 38],
    popupAnchor: [0, -38],
  });

  const timeStr = marker.timestamp ? new Date(marker.timestamp).toLocaleTimeString() : "";
  const prioLabels = { critical: "Kritisch", high: "Hoch", normal: "Normal", low: "Niedrig" };
  const prioLabel = prioLabels[prio] || prio.toUpperCase();

  const popupHtml = `
    <div class="t-popup">
      <div class="t-popup-header">
        <div class="t-popup-title">
          <span>${info.icon}</span>
          <span>${escapeHTML(info.label)}</span>
        </div>
        <span class="t-popup-badge prio-${prio}">${prioLabel}</span>
      </div>
      <div class="t-popup-rows">
        ${desc ? `<div class="t-popup-row"><span class="t-key">Info</span><span class="t-val" style="font-family:inherit">${desc}</span></div>` : ""}
        <div class="t-popup-row"><span class="t-key">Erfasser</span><span class="t-val">${escapeHTML(marker.author || "Leitstelle")}</span></div>
        ${timeStr ? `<div class="t-popup-row"><span class="t-key">Zeit</span><span class="t-val">${timeStr}</span></div>` : ""}
        <div class="t-popup-row"><span class="t-key">Position</span><span class="t-val">${marker.lat.toFixed(6)}, ${marker.lon.toFixed(6)}</span></div>
      </div>
      <div class="t-popup-actions">
        <button class="btn btn-sm btn-danger" onclick="deleteMarker(${marker.id})" style="flex:1;padding:4px 8px;font-size:11px;">🗑 Marker löschen</button>
      </div>
    </div>`;

  const lm = L.marker([marker.lat, marker.lon], { icon: customIcon })
    .addTo(STATE.map)
    .bindPopup(popupHtml);

  STATE.mapPins[marker.id] = lm;
}

function onMarkerAdded(marker) {
  STATE.markers[marker.id] = marker;
  drawDBMarkerOnMap(marker);
}

function onMarkerDeleted(id) {
  delete STATE.markers[id];
  if (STATE.mapPins[id]) {
    STATE.map?.removeLayer(STATE.mapPins[id]);
    delete STATE.mapPins[id];
  }
}

function drawGeofenceOnMap(gf) {
  if (!STATE.map || !gf) return;
  if (!STATE.mapGeofences) STATE.mapGeofences = {};

  // Vorherigen Layer dieser Zone entfernen falls vorhanden
  if (STATE.mapGeofences[gf.id]) {
    STATE.map.removeLayer(STATE.mapGeofences[gf.id]);
    delete STATE.mapGeofences[gf.id];
  }

  let layer = null;
  const zoneType = gf.zone_type || "danger";
  let color = "#f85149";
  let typeLabel = "🚨 Gefahrenzone";
  let fillOpacity = 0.15;

  if (zoneType === "danger") {
    color = "#f85149";
    typeLabel = "🚨 Gefahrenzone";
    fillOpacity = 0.15;
  } else if (zoneType === "restricted") {
    color = "#a371f7";
    typeLabel = "⛔ Sperrzone";
    fillOpacity = 0.15;
  } else if (zoneType === "warning") {
    color = "#d29922";
    typeLabel = "⚠️ Warnbereich";
    fillOpacity = 0.12;
  } else if (zoneType === "operational") {
    color = "#2f81f7";
    typeLabel = "🔵 Einsatzabschnitt";
    fillOpacity = 0.10;
  }

  if (gf.shape === "circle" && gf.center_lat != null && gf.center_lon != null) {
    layer = L.circle([gf.center_lat, gf.center_lon], {
      radius: gf.radius_m || 100,
      color: color,
      fillColor: color,
      fillOpacity: fillOpacity,
      weight: 2.5,
      dashArray: "6 4",
    });
  } else if (gf.shape === "polygon") {
    let coords = gf.polygon_coords;
    if (typeof coords === "string") {
      try { coords = JSON.parse(coords); } catch(e) { coords = []; }
    }
    if (Array.isArray(coords) && coords.length >= 3) {
      layer = L.polygon(coords, {
        color: color,
        fillColor: color,
        fillOpacity: fillOpacity,
        weight: 2.5,
        dashArray: "6 4",
      });
    }
  }

  if (layer) {
    layer.bindTooltip(`<b>⬡ ${escapeHTML(gf.name)}</b><br><span style="font-size:10px;opacity:0.85">${typeLabel}</span>`, {
      sticky: true,
      className: "geofence-tooltip"
    });

    const popupHtml = `
      <div style="font-family:var(--font-sans);min-width:180px;padding:4px 0;">
        <div style="font-weight:700;font-size:13px;margin-bottom:4px;display:flex;align-items:center;gap:6px;">
          <span>⬡</span> <span>${escapeHTML(gf.name)}</span>
        </div>
        <div style="font-size:11px;color:${color};font-weight:600;margin-bottom:6px;">${typeLabel}</div>
        <div style="font-size:11px;color:var(--text-muted);margin-bottom:10px;">
          ${gf.shape === 'circle' ? `⭕ Kreis (Radius: ${gf.radius_m || 100}m)` : `⬡ Freiform-Polygon (${(gf.polygon_coords || []).length} Eckpunkte)`}
        </div>
        <button class="btn btn-sm btn-danger" onclick="deleteGeofence(${gf.id})" style="width:100%;font-size:11px;padding:4px 8px;">
          🗑️ Zone entfernen
        </button>
      </div>
    `;
    layer.bindPopup(popupHtml);
    layer.addTo(STATE.map);
    STATE.mapGeofences[gf.id] = layer;
  }
}

function toggleRSSILayer() {
  // Einfache RSSI-Visualisierung: Farbige Kreise an GPS-Positionen
  STATE.rssiMode = !STATE.rssiMode;
  if (!STATE.rssiMode) {
    STATE.rssiLayers?.forEach(l => STATE.map?.removeLayer(l));
    STATE.rssiLayers = [];
    return;
  }
  STATE.rssiLayers = [];
  Object.entries(STATE.gpsPositions).forEach(([rid, gps]) => {
    if (gps.rssi == null || !gps.lat) return;
    const rssi = gps.rssi;
    let color;
    if (rssi >= -75)      color = "#3fb950";
    else if (rssi >= -90) color = "#d29922";
    else                  color = "#f85149";
    const l = L.circle([gps.lat, gps.lon], {
      radius: 50, color, fillColor: color, fillOpacity: 0.25, weight: 1,
    }).addTo(STATE.map);
    STATE.rssiLayers.push(l);
  });
}

// ─────────────────────────────────────────────────────────────────
// Render-Funktionen
// ─────────────────────────────────────────────────────────────────
function renderSidebarUnits() {
  const container = document.getElementById("sidebar-unit-list");
  if (!container) return;

  const radios = Object.values(STATE.radios);
  if (radios.length === 0) {
    container.innerHTML = `<div class="empty-state" style="padding:20px;"><span style="font-size:24px;opacity:0.3">📻</span><span style="font-size:11px;color:var(--text-muted)">Keine Einheiten</span></div>`;
    return;
  }

  // Notrufe zuerst, dann Online, dann Offline
  radios.sort((a, b) => {
    const ea = STATE.emergencies[a.radio_id] ? 0 : (a.online ? 1 : 2);
    const eb = STATE.emergencies[b.radio_id] ? 0 : (b.online ? 1 : 2);
    return ea - eb;
  });

  container.innerHTML = radios.map(r => {
    const rid  = r.radio_id;
    const em   = STATE.emergencies[rid];
    const isPTT = STATE.activePTTs[rid];
    const gps   = STATE.gpsPositions[rid];
    const rssi  = gps?.rssi;
    const initials = getAvatarText(rid);

    let avatarClass = "unit-avatar";
    let avatarContent = initials;
    if (em)    { avatarClass += " emergency"; avatarContent = "🚨"; }
    else if (isPTT) { avatarClass += " ptt"; avatarContent = "🎙"; }
    else if (r.is_blocked) { avatarClass += " blocked"; avatarContent = "🚫"; }
    else if (r.online) { avatarClass += " online"; }

    let itemClass = "unit-item";
    if (em)     itemClass += " emergency-active";
    else if (isPTT) itemClass += " ptt-active";
    if (r.is_blocked) itemClass += " unit-blocked";

    let subText = r.device_model || (r.online ? "Online" : "Offline");
    if (r.is_blocked) {
      subText = `🚫 ${r.blocked_reason || "Gesperrt"}`;
    }

    return `
      <div class="${itemClass}" id="unit-item-${rid}" onclick="focusOnMap(${rid})" title="${r.is_blocked ? 'GESPERRT: ' + (r.blocked_reason || 'Blacklist') : ''}">
        <div class="${avatarClass}">${avatarContent}</div>
        <div class="unit-info">
          <div class="unit-name">${r.alias || "Radio " + rid}</div>
          <div class="unit-sub" style="${r.is_blocked ? 'color:var(--red);font-weight:600;' : ''}">${subText}</div>
        </div>
        <span class="unit-rssi" id="unit-rssi-${rid}">${rssi != null ? rssi.toFixed(0) + "d" : ""}</span>
      </div>`;
  }).join("");
}

function renderRadiosTable() {
  const tbody = document.getElementById("radios-table-body");
  if (!tbody) return;

  const filterSelect = document.getElementById("radios-filter-select");
  const filterVal = filterSelect ? filterSelect.value : "all";

  let radios = Object.values(STATE.radios);
  if (filterVal === "active") {
    radios = radios.filter(r => !r.is_blocked);
  } else if (filterVal === "blocked") {
    radios = radios.filter(r => Boolean(r.is_blocked));
  }

  if (radios.length === 0) {
    const emptyMsg = filterVal === "blocked"
      ? "Keine gesperrten Funkgeräte in der Blacklist."
      : (filterVal === "active" ? "Keine aktiven Funkgeräte vorhanden." : "Keine Funkgeräte konfiguriert. Klicke „+ Gerät\".");
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:30px">
      ${emptyMsg}
    </td></tr>`;
    return;
  }

  tbody.innerHTML = radios.map(r => {
    const rid = r.radio_id;
    const em  = STATE.emergencies[rid];
    const gps = STATE.gpsPositions[rid];
    const lat = r.last_lat ?? gps?.lat;
    const lon = r.last_lon ?? gps?.lon;
    const isFixed = Boolean(r.fixed_lat != null && r.fixed_lon != null);
    let gpsStr = "—";
    if (isFixed) {
      gpsStr = `<span style="color:var(--blue-400);font-weight:600;" title="Feste Position (fixiert auf Karte)">📌 ${r.fixed_lat.toFixed(5)}, ${r.fixed_lon.toFixed(5)}</span>`;
    } else if (lat && lon) {
      gpsStr = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    }
    const rssi = r.last_rssi ?? gps?.rssi;
    const rssiStr = rssi != null ? `${rssi.toFixed(0)} dBm` : "—";

    let statusChips = [];
    if (r.is_blocked) {
      statusChips.push(`<span class="chip badge-blocked" title="Gesperrt: ${r.blocked_reason || 'Blacklist'}">🚫 GESPERRT</span>`);
    }
    if (em) {
      statusChips.push(`<span class="chip chip-emergency">🚨 NOTRUF</span>`);
    } else if (r.online) {
      statusChips.push(`<span class="chip chip-online">● Online</span>`);
    } else {
      statusChips.push(`<span class="chip chip-offline">○ Offline</span>`);
    }

    const blockBtn = r.is_blocked
      ? `<button class="btn btn-sm btn-icon" onclick="unblockRadio(${rid})" title="Sperre aufheben (Entsperren)" style="color:var(--green);font-size:13px;">🔓</button>`
      : `<button class="btn btn-sm btn-icon" onclick="openRadioBlockModal(${rid})" title="Funkgerät sperren / Blacklist" style="color:var(--red);font-size:13px;">🚫</button>`;

    return `<tr>
      <td class="mono" style="font-size:11px">${rid}</td>
      <td style="font-weight:600">${r.alias || "—"}${r.is_blocked ? ` <small style="color:var(--red);font-weight:normal;">(${r.blocked_reason || 'Gesperrt'})</small>` : ""}</td>
      <td style="color:var(--text-secondary);font-size:12px">${r.device_model || "—"}</td>
      <td>${statusChips.join(" ")}</td>
      <td style="font-size:11px;font-family:var(--font-mono);color:var(--text-secondary)">${gpsStr}</td>
      <td style="font-family:var(--font-mono);font-size:12px">${rssiStr}</td>
      <td style="font-size:12px">${r.floor_level || "—"}</td>
      <td>
        <div style="display:flex;gap:4px;">
          <button class="btn btn-sm btn-icon" onclick="focusOnMap(${rid})" title="Auf Karte zentrieren">🗺</button>
          <button class="btn btn-sm btn-icon" onclick="startSetRadioPosition(${rid})" title="Feste Position auf Karte setzen / anpassen" style="color:var(--blue-400);font-size:13px;">📍</button>
          ${em ? `<button class="btn btn-sm btn-danger" onclick="ackEmergency(${rid})">✓ Quit.</button>` : ""}
          ${blockBtn}
          <button class="btn btn-sm btn-icon" onclick="deleteRadio(${rid})" title="Löschen" style="color:var(--text-muted)">✕</button>
        </div>
      </td>
    </tr>`;
  }).join("");
}

function renderDashboardCalls() {
  const tbody = document.getElementById("dashboard-calls-table");
  if (!tbody) return;
  if (STATE.calls.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:20px">Keine Daten</td></tr>`;
    return;
  }
  tbody.innerHTML = STATE.calls.slice(0, 10).map(c => {
    const radio = STATE.radios[c.radio_id];
    const alias = radio?.alias || `Radio ${c.radio_id}`;
    const dur = c.duration_ms ? `${(c.duration_ms / 1000).toFixed(1)}s` : "—";
    const rssi = c.rssi != null ? `${c.rssi.toFixed(0)} dBm` : "—";
    const time = (c.timestamp || c.created_at) ? new Date(c.timestamp || c.created_at).toLocaleTimeString("de-DE") : "—";
    const hasAudio = Boolean(c.audio_url || c.id);
    const audioBtn = hasAudio
      ? `<button class="btn btn-xs btn-primary btn-play-audio" data-ptt="${c.id}" onclick="playAudio(${c.id}, '${alias.replace(/'/g, "\\'")}', '${dur}')" title="Funkspruch sofort anhören" style="padding:3px 8px;font-size:11px;">▶ Anhören</button>`
      : `<span style="color:var(--text-muted);font-size:11px">—</span>`;
    return `<tr>
      <td style="font-size:11px;color:var(--text-muted)">${time}</td>
      <td style="font-weight:500">${alias}</td>
      <td><span class="chip chip-offline" style="font-size:10px">${c.typ || "Gruppe"}</span></td>
      <td style="font-family:var(--font-mono);font-size:12px">${dur}</td>
      <td style="font-family:var(--font-mono);font-size:12px">${rssi}</td>
      <td style="text-align:center">${audioBtn}</td>
    </tr>`;
  }).join("");
  updateLatestCallButton();
}

function renderDashboardSMS() {
  const tbody = document.getElementById("dashboard-sms-table");
  if (!tbody) return;
  if (STATE.sms.length === 0) {
    tbody.innerHTML = `<tr><td colspan="3" style="text-align:center;color:var(--text-muted);padding:20px">Keine Nachrichten</td></tr>`;
    return;
  }
  tbody.innerHTML = STATE.sms.slice(0, 5).map(s => {
    const alias = STATE.radios[s.sender_id]?.alias || `Radio ${s.sender_id}`;
    const time  = s.timestamp ? new Date(s.timestamp).toLocaleTimeString("de-DE") : "—";
    const text  = (s.text || "").substring(0, 50) + (s.text?.length > 50 ? "…" : "");
    return `<tr>
      <td style="font-size:11px;color:var(--text-muted);white-space:nowrap">${time}</td>
      <td style="font-weight:500;white-space:nowrap">${alias}</td>
      <td style="color:var(--text-secondary);font-size:12px">${text}</td>
    </tr>`;
  }).join("");
}

function renderSMSList() {
  const list = document.getElementById("sms-list");
  if (!list) return;
  if (STATE.sms.length === 0) {
    list.innerHTML = `<div class="empty-state"><span style="font-size:32px;opacity:0.3">💬</span><span>Keine Nachrichten</span></div>`;
    return;
  }
  list.innerHTML = [...STATE.sms].reverse().map(s => {
    const isOut = s.direction === "outgoing";
    const alias = isOut ? "Operator" : (STATE.radios[s.sender_id]?.alias || `Radio ${s.sender_id}`);
    const time  = s.timestamp ? new Date(s.timestamp).toLocaleTimeString("de-DE") : "";
    return `<div class="sms-item ${isOut ? 'outgoing' : 'incoming'}">
      <div class="sms-sender">${alias}</div>
      <div class="sms-text">${escapeHTML(s.text || "")}</div>
      <div class="sms-time">${time}</div>
    </div>`;
  }).join("");
  // Scroll to bottom
  const container = document.getElementById("sms-list-container");
  if (container) container.scrollTop = container.scrollHeight;
}

function renderEmergencyPanel() {
  const panel = document.getElementById("emergency-panel");
  const list  = document.getElementById("emergency-list");
  if (!panel || !list) return;

  const emergencies = Object.values(STATE.emergencies);
  panel.style.display = emergencies.length > 0 ? "block" : "none";

  list.innerHTML = emergencies.map(e => {
    const alias = STATE.radios[e.radio_id]?.alias || `Radio ${e.radio_id}`;
    return `<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--red-border);">
      <div>
        <div style="font-weight:700;color:var(--red)">🚨 ${alias}</div>
        <div style="font-size:11px;color:var(--text-muted)">${e.call_type || "NOTRUF"} · ${e.radio_id}</div>
      </div>
      <button class="btn btn-danger btn-sm" onclick="ackEmergency(${e.radio_id})">✓ Quittieren</button>
    </div>`;
  }).join("");
}

function renderSNMPEventsTable() {
  const tbody = document.getElementById("snmp-events-table");
  if (!tbody) return;
  if (STATE.snmpEvents.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:20px">Keine SNMP-Alarme</td></tr>`;
    return;
  }
  tbody.innerHTML = STATE.snmpEvents.slice(0, 50).map(e => {
    const time = e.created_at ? new Date(e.created_at).toLocaleTimeString("de-DE") : "—";
    const levelClass = e.alarm_level === "critical" ? "chip-emergency"
      : e.alarm_level === "warning" ? "chip-warning" : "chip-offline";
    return `<tr>
      <td style="font-size:11px;color:var(--text-muted)">${time}</td>
      <td>${e.oid_label || e.oid_key}</td>
      <td style="font-family:var(--font-mono);font-size:11px">${e.value}</td>
      <td><span class="chip ${levelClass}" style="font-size:10px">${e.alarm_level || "info"}</span></td>
    </tr>`;
  }).join("");
}

function renderPacketList() {
  const container = document.getElementById("packet-list-container");
  const countEl   = document.getElementById("dump-count");
  if (!container) return;

  if (countEl) countEl.textContent = `${STATE.packets.length} Pakete`;

  container.innerHTML = STATE.packets.slice(0, 100).map(p => `
    <div class="packet-entry" title="${p.hex_full || ''}">
      <span class="packet-port">${p.port}</span>
      <span class="packet-service">${p.service}</span>
      <span class="packet-opcode">${p.opcode || "—"}</span>
      <span style="color:var(--text-secondary);min-width:80px">${p.opcode_label || ""}</span>
      <span class="packet-hex">${p.hex_preview || ""}</span>
    </div>
  `).join("");
}

function updatePortStats(stats) {
  if (stats && stats.length > 0) STATE.portStats = stats;
  const tbody = document.getElementById("port-stats-table");
  if (!tbody) return;
  tbody.innerHTML = STATE.portStats.map(s =>
    `<tr>
      <td class="mono" style="font-size:11px">${s.port}</td>
      <td style="color:var(--text-secondary)">${s.service}</td>
      <td class="mono">${s.total || 0}</td>
      <td class="mono">${s.pps?.toFixed(2) || "0.00"}</td>
      <td><span class="chip ${s.active ? 'chip-online' : 'chip-offline'}" style="font-size:10px">${s.active ? "● Aktiv" : "○ Idle"}</span></td>
    </tr>`
  ).join("");
}

function renderGeofencesList() {
  const el = document.getElementById("geofences-list");
  if (!el) return;
  if (!STATE.geofences || STATE.geofences.length === 0) {
    el.innerHTML = `
      <div class="empty-state" style="padding:24px;text-align:center;">
        <span style="font-size:24px;opacity:0.3;display:block;margin-bottom:6px;">⬡</span>
        <span style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:10px;">Keine taktischen Zonen definiert</span>
        <button class="btn btn-sm btn-primary" onclick="openAddGeofenceModal()" style="font-size:11px;padding:3px 10px;">＋ Erste Zone anlegen</button>
      </div>`;
    return;
  }

  const typeConfig = {
    danger: { icon: "🚨", label: "Gefahrenzone", chipClass: "chip-emergency" },
    restricted: { icon: "⛔", label: "Sperrzone", chipClass: "chip-restricted", style: "background:rgba(163,113,247,0.15);color:#d2a8ff;border:1px solid rgba(163,113,247,0.3);" },
    operational: { icon: "🔵", label: "Abschnitt", chipClass: "chip-online" },
    warning: { icon: "⚠️", label: "Warnbereich", chipClass: "chip-warn", style: "background:rgba(210,153,34,0.15);color:#e3b341;border:1px solid rgba(210,153,34,0.3);" }
  };

  el.innerHTML = STATE.geofences.map(g => {
    const cfg = typeConfig[g.zone_type] || typeConfig.danger;
    const shapeInfo = g.shape === "circle" 
      ? `⭕ ${g.radius_m || 100}m Radius` 
      : `⬡ Freiform (${(g.polygon_coords || []).length} Punkte)`;

    return `
      <div class="infra-row" style="padding:8px var(--space-3);display:flex;align-items:center;justify-content:space-between;gap:8px;">
        <div style="flex:1;min-width:0;cursor:pointer;" onclick="focusOnGeofence(${g.id})" title="Auf Karte zentrieren">
          <div style="font-weight:600;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:flex;align-items:center;gap:5px;">
            <span>${cfg.icon}</span>
            <span style="color:var(--text-primary);">${escapeHTML(g.name)}</span>
          </div>
          <div style="font-size:10px;color:var(--text-muted);margin-top:2px;">
            ${shapeInfo}
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
          <span class="chip ${cfg.chipClass}" style="font-size:9px;padding:2px 6px;${cfg.style || ''}">${cfg.label}</span>
          <button class="btn btn-sm btn-icon" onclick="focusOnGeofence(${g.id})" title="Auf Karte anzeigen" style="padding:2px 5px;font-size:11px;">🎯</button>
          <button class="btn btn-sm btn-icon btn-danger" onclick="deleteGeofence(${g.id})" title="Zone löschen" style="padding:2px 5px;font-size:11px;">✕</button>
        </div>
      </div>
    `;
  }).join("");
}

function renderPlansList() {
  const el = document.getElementById("plans-list");
  if (!el) return;
  if (STATE.plans.length === 0) {
    el.innerHTML = `<div class="empty-state" style="padding:20px"><span style="opacity:0.3">🗺</span><span style="font-size:11px">Keine Lagepläne</span></div>`;
    return;
  }
  el.innerHTML = STATE.plans.map(p =>
    `<div class="infra-row" style="padding:8px var(--space-3);">
      <span>${p.name} <small style="color:var(--text-muted)">(${p.floor_level})</small></span>
      <div style="display:flex;gap:6px;">
        <button class="btn btn-sm btn-success" onclick="activatePlan(${p.id})">✓ Aktivieren</button>
        <button class="btn btn-sm btn-icon" onclick="deletePlan(${p.id})" style="color:var(--text-muted)">✕</button>
      </div>
    </div>`
  ).join("");
}

// ─────────────────────────────────────────────────────────────────
// Timeline
// ─────────────────────────────────────────────────────────────────
async function loadTimeline() {
  const from = document.getElementById("timeline-from")?.value;
  const to   = document.getElementById("timeline-to")?.value;
  let url = "/api/timeline?limit=200";
  if (from) url += `&from_ts=${from}`;
  if (to)   url += `&to_ts=${to}`;

  try {
    const data = await fetch(url).then(r => r.json());
    renderTimeline(data);
  } catch(e) {
    console.error("Timeline Fehler:", e);
  }
}

function renderTimeline(events) {
  const el = document.getElementById("timeline-list");
  if (!el) return;
  let evList = events || [];
  if (STATE.timelineAudioOnly) {
    evList = evList.filter(e => e.type === "ptt");
  }
  if (!evList || evList.length === 0) {
    el.innerHTML = `<div class="empty-state"><span style="font-size:32px;opacity:0.3">📋</span><span>Keine Ereignisse</span></div>`;
    return;
  }

  const typeInfo = {
    ptt:      { icon: "🎙", label: "Funk",      color: "var(--accent)" },
    gps:      { icon: "📍", label: "GPS",       color: "var(--green)" },
    emergency:{ icon: "🚨", label: "NOTRUF",    color: "var(--red)" },
    sms:      { icon: "💬", label: "SMS",        color: "var(--purple)" },
    marker:   { icon: "📌", label: "Marker",    color: "var(--yellow)" },
    alarm:    { icon: "⚠",  label: "Alarm",     color: "var(--orange)" },
    online:   { icon: "✅", label: "Online",    color: "var(--green)" },
    offline:  { icon: "🔴", label: "Offline",   color: "var(--text-muted)" },
  };

  el.innerHTML = `<div style="display:flex;flex-direction:column;gap:4px;">` +
    evList.map(e => {
      const info = typeInfo[e.type] || { icon: "•", label: e.type, color: "var(--text-muted)" };
      const radio = e.radio_id ? (STATE.radios[e.radio_id]?.alias || `Radio ${e.radio_id}`) : "";
      const time  = (e.timestamp || e.start) ? new Date(e.timestamp || e.start).toLocaleString("de-DE") : "—";
      const details = e.duration_ms ? `${(e.duration_ms/1000).toFixed(1)}s (${e.slot || "TS1"})` : (e.text || e.beschreibung || "");
      const pttId = e.ptt_id || (e.id && String(e.id).startsWith("ptt_") ? parseInt(String(e.id).replace("ptt_", "")) : null);
      const audioBtn = (e.type === "ptt" && pttId)
        ? `<button class="btn btn-xs btn-primary btn-play-audio" data-ptt="${pttId}" onclick="playAudio(${pttId}, '${radio.replace(/'/g, "\\'")}', '${details.replace(/'/g, "\\'")}')" title="Funkspruch sofort anhören" style="padding:4px 10px;font-size:11px;margin-left:auto;flex-shrink:0;">▶ Anhören</button>`
        : "";
      return `
        <div style="display:flex;align-items:center;gap:12px;padding:8px 12px;border-radius:6px;border:1px solid var(--border-subtle);background:var(--bg-card);">
          <span style="font-size:16px;flex-shrink:0">${info.icon}</span>
          <span style="font-size:11px;color:var(--text-muted);white-space:nowrap;min-width:130px">${time}</span>
          <span style="font-size:10px;font-weight:700;text-transform:uppercase;color:${info.color};min-width:60px">${info.label}</span>
          <span style="font-weight:600;min-width:110px">${radio}</span>
          <span style="color:var(--text-secondary);font-size:12px;flex:1">${details}</span>
          ${audioBtn}
        </div>`;
    }).join("") + "</div>";
}

// ─────────────────────────────────────────────────────────────────
// KPIs & Badges
// ─────────────────────────────────────────────────────────────────
function updateKPIs() {
  const online = Object.values(STATE.radios).filter(r => r.online).length;
  const total  = Object.keys(STATE.radios).length;
  const emergency = Object.keys(STATE.emergencies).length;

  setText("kpi-online",    online);
  setText("kpi-total-sub", `von ${total} gesamt`);
  setText("kpi-calls",     STATE.calls.length);
  setText("kpi-alarms",    STATE.snmpEvents.length);

  const emergencyEl = document.getElementById("kpi-emergency");
  if (emergencyEl) {
    emergencyEl.textContent = emergency;
    emergencyEl.className   = `metric-value ${emergency > 0 ? "danger" : ""}`;
    if (!emergency) emergencyEl.style.color = "var(--text-muted)";
  }
}

function updateTopbarBadges() {
  // wird durch onRepeaterUpdate / onUPSUpdate etc. gesetzt
}

function updateEmergencyBadge() {
  const badge = document.getElementById("badge-emergency");
  const count = Object.keys(STATE.emergencies).length;
  if (badge) {
    badge.style.display = count > 0 ? "inline" : "none";
    badge.textContent   = count;
  }
}

function updateSMSBadge() {
  const badge = document.getElementById("badge-sms-count");
  if (badge) {
    badge.style.display = STATE.unreadSMS > 0 ? "inline" : "none";
    badge.textContent   = STATE.unreadSMS;
  }
}

// ─────────────────────────────────────────────────────────────────
// Settings
// ─────────────────────────────────────────────────────────────────
function applySettings(s) {
  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.type === "checkbox") el.checked = val === true || val === "true" || val === 1;
    else el.value = val ?? "";
  };

  setVal("cfg-repeater-ip", s.hw_repeater_ip);
  setVal("cfg-trap-port",   s.hw_repeater_trap_port);
  setVal("cfg-usv-ip",      s.hw_usv_ip);
  setVal("cfg-usv-port",    s.hw_usv_port);
  setVal("cfg-zte-ip",      s.hw_zte_ip);
  setVal("cfg-omada-ip",    s.hw_omada_ip);
  setVal("cfg-mock-active", s.mock_active);
  setVal("cfg-audio-enabled", s.audio_enabled);
  setVal("cfg-breadcrumbs", s.breadcrumbs_enabled !== false);
  setVal("cfg-auto-pan",    s.auto_pan);
  setVal("cfg-map-provider",s.cfg_map_provider || "osm");
  setVal("cfg-map-api-key", s.cfg_map_api_key);

  STATE.breadcrumbsOn = s.breadcrumbs_enabled !== false;
}

function applyMissionData(data) {
  STATE.mission = data || {};
  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val || "";
  };
  setVal("mission-title",    data.mission_title);
  setVal("mission-code",     data.mission_code);
  setVal("mission-leader",   data.mission_leader);
  setVal("mission-location", data.mission_location);
  setVal("mission-channel",  data.mission_channel);
  setVal("mission-notes",    data.mission_notes);

  const statusEl = document.getElementById("mission-status");
  if (statusEl) statusEl.value = data.mission_status || "preparation";

  // Topbar
  const titleEl = document.getElementById("topbar-mission-title");
  const subEl   = document.getElementById("topbar-mission-sub");
  if (titleEl) titleEl.textContent = data.mission_title || "Kein aktiver Einsatz";
  if (subEl)   subEl.textContent   = data.mission_location || "";
}

// ─────────────────────────────────────────────────────────────────
// API Actions
// ─────────────────────────────────────────────────────────────────
async function ackEmergency(radioId) {
  try {
    await fetch(`/api/radios/${radioId}/ack_emergency`, { method: "POST" });
  } catch(e) { console.error("ACK Fehler:", e); }
}

async function deleteRadio(radioId) {
  if (!confirm(`Radio ${radioId} wirklich löschen?`)) return;
  try {
    await fetch(`/api/radios/${radioId}`, { method: "DELETE" });
    delete STATE.radios[radioId];
    delete STATE.gpsPositions[radioId];
    delete STATE.activePTTs[radioId];
    delete STATE.emergencies[radioId];
    if (STATE.mapMarkers[radioId]) {
      STATE.map?.removeLayer(STATE.mapMarkers[radioId]);
      delete STATE.mapMarkers[radioId];
    }
    renderSidebarUnits();
    renderRadiosTable();
    updateKPIs();
  } catch(e) { console.error("Delete Fehler:", e); }
}

async function deleteMarker(id) {
  try {
    await fetch(`/api/markers/${id}`, { method: "DELETE" });
  } catch(e) { console.error("Marker delete Fehler:", e); }
}

// ── Taktische Zonen / Geofencing Controller ──────────────────────

function openAddGeofenceModal() {
  const modal = document.getElementById("modal-add-geofence");
  if (!modal) return;
  const nameInput = document.getElementById("new-geofence-name");
  const radiusInput = document.getElementById("new-geofence-radius");
  if (nameInput) {
    const nextNum = (STATE.geofences || []).length + 1;
    nameInput.value = `Gefahrenbereich ${nextNum}`;
    setTimeout(() => { nameInput.focus(); nameInput.select(); }, 60);
  }
  if (radiusInput) radiusInput.value = 100;
  selectGeofenceType("danger");
  toggleGeofenceShapeInputs("circle");
  const circleRadio = document.querySelector('input[name="geofence-shape"][value="circle"]');
  if (circleRadio) circleRadio.checked = true;

  modal.style.display = "flex";
}

function closeAddGeofenceModal() {
  const modal = document.getElementById("modal-add-geofence");
  if (modal) modal.style.display = "none";
}

function selectGeofenceType(type) {
  STATE.selectedGeofenceType = type;
  document.querySelectorAll("#geofence-type-selector .geofence-type-card").forEach(c => {
    if (c.getAttribute("data-type") === type) {
      c.classList.add("active");
    } else {
      c.classList.remove("active");
    }
  });
}

function toggleGeofenceShapeInputs(shape) {
  const circleBox = document.getElementById("geofence-circle-settings");
  const polyBox = document.getElementById("geofence-polygon-hint");
  if (shape === "circle") {
    if (circleBox) circleBox.style.display = "flex";
    if (polyBox) polyBox.style.display = "none";
  } else {
    if (circleBox) circleBox.style.display = "none";
    if (polyBox) polyBox.style.display = "block";
  }
}

function setGeofenceRadiusPreset(r) {
  const input = document.getElementById("new-geofence-radius");
  if (input) input.value = r;
}

function startGeofenceDrawing() {
  const nameInput = document.getElementById("new-geofence-name");
  const name = nameInput?.value.trim() || "Taktische Zone";
  const type = STATE.selectedGeofenceType || "danger";
  const shapeRadio = document.querySelector('input[name="geofence-shape"]:checked');
  const shape = shapeRadio ? shapeRadio.value : "circle";
  const radius = parseFloat(document.getElementById("new-geofence-radius")?.value || 100);

  closeAddGeofenceModal();
  switchTab("map");

  // Falls Marker-Modus aktiv war, beenden
  if (STATE.addingMarker) setMarkerMode(false);
  cleanUpGeofenceDrawing();

  STATE.drawingGeofence = {
    name,
    zone_type: type,
    shape,
    radius_m: radius,
    points: [],
  };

  const btn = document.getElementById("btn-add-geofence");
  if (btn) btn.classList.add("zone-mode-active");

  const mapEl = document.getElementById("map");
  if (mapEl) mapEl.style.cursor = "crosshair";

  const banner = document.getElementById("geofence-draw-banner");
  const bannerText = document.getElementById("geofence-draw-text");
  const btnFinish = document.getElementById("btn-finish-draw-geofence");

  if (shape === "circle") {
    if (bannerText) bannerText.textContent = `📍 Klicke auf die Karte, um den Mittelpunkt für '${name}' (${radius}m Radius) zu setzen`;
    if (btnFinish) btnFinish.style.display = "none";
    showToast("📍 Kreis-Zone platzieren", `Klicke auf die Karte, um '${name}' zu platzieren (ESC zum Abbrechen)`, "info", 4000);
  } else {
    if (bannerText) bannerText.textContent = `⬡ Freiform-Zone '${name}': Klicke Eckpunkte auf die Karte (0 Punkte)`;
    if (btnFinish) btnFinish.style.display = "none";
    showToast("⬡ Freiform-Zone", `Klicke mindestens 3 Eckpunkte auf die Karte (ESC zum Abbrechen)`, "info", 4000);
  }

  if (banner) banner.style.display = "flex";
}

async function handleGeofenceMapClick(latlng) {
  if (!STATE.drawingGeofence) return;
  const cfg = STATE.drawingGeofence;

  if (cfg.shape === "circle") {
    await saveNewGeofence({
      name: cfg.name,
      zone_type: cfg.zone_type,
      shape: "circle",
      center_lat: latlng.lat,
      center_lon: latlng.lng,
      radius_m: cfg.radius_m
    });
    cleanUpGeofenceDrawing();
  } else if (cfg.shape === "polygon") {
    cfg.points.push([latlng.lat, latlng.lng]);
    const ptCount = cfg.points.length;

    // Marker für Eckpunkt auf Karte visualisieren
    const dot = L.circleMarker(latlng, {
      radius: 5,
      color: "#a371f7",
      fillColor: "#ffffff",
      fillOpacity: 1,
      weight: 2
    }).addTo(STATE.map);
    STATE.tempGeofenceLayers.push(dot);

    // Vorschau-Linie aktualisieren
    if (cfg.previewLine) {
      STATE.map.removeLayer(cfg.previewLine);
      cfg.previewLine = null;
    }

    if (ptCount >= 2) {
      cfg.previewLine = L.polyline(cfg.points, {
        color: "#a371f7",
        weight: 2.5,
        dashArray: "5 5"
      }).addTo(STATE.map);
      STATE.tempGeofenceLayers.push(cfg.previewLine);
    }

    const bannerText = document.getElementById("geofence-draw-text");
    const btnFinish = document.getElementById("btn-finish-draw-geofence");
    if (bannerText) {
      bannerText.textContent = `⬡ Freiform-Zone '${cfg.name}': ${ptCount} Eckpunkt${ptCount > 1 ? 'e' : ''} gesetzt${ptCount < 3 ? ' (mind. 3 nötig)' : ''}`;
    }

    if (ptCount >= 3 && btnFinish) {
      btnFinish.style.display = "inline-block";
    }
  }
}

async function finishGeofenceDrawing() {
  if (!STATE.drawingGeofence) return;
  const cfg = STATE.drawingGeofence;
  if (cfg.shape === "polygon") {
    if (cfg.points.length < 3) {
      showToast("Unvollständig", "Ein Freiform-Polygon benötigt mindestens 3 Eckpunkte.", "warning");
      return;
    }
    await saveNewGeofence({
      name: cfg.name,
      zone_type: cfg.zone_type,
      shape: "polygon",
      polygon_coords: cfg.points
    });
  }
  cleanUpGeofenceDrawing();
}

function cancelGeofenceDrawing() {
  cleanUpGeofenceDrawing();
  showToast("Zonen-Erstellung", "Abgebrochen", "info", 2000);
}

function cleanUpGeofenceDrawing() {
  STATE.drawingGeofence = null;
  const btn = document.getElementById("btn-add-geofence");
  if (btn) btn.classList.remove("zone-mode-active");

  const mapEl = document.getElementById("map");
  if (mapEl) mapEl.style.cursor = "";

  const banner = document.getElementById("geofence-draw-banner");
  if (banner) banner.style.display = "none";

  const btnFinish = document.getElementById("btn-finish-draw-geofence");
  if (btnFinish) btnFinish.style.display = "none";

  // Temporäre Zeichen-Layer entfernen
  if (STATE.tempGeofenceLayers && STATE.tempGeofenceLayers.length > 0) {
    STATE.tempGeofenceLayers.forEach(l => {
      try { STATE.map?.removeLayer(l); } catch(e) {}
    });
    STATE.tempGeofenceLayers = [];
  }
}

async function saveNewGeofence(payload) {
  try {
    const res = await fetch("/api/geofences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.ok) {
      showToast("Zone gespeichert", `Zone '${payload.name}' wurde erfolgreich erstellt.`, "success");
    } else {
      showToast("Fehler", "Zone konnte nicht gespeichert werden.", "error");
    }
  } catch(e) {
    console.error("Fehler beim Speichern der Geofence:", e);
    showToast("Fehler", "Netzwerkfehler beim Anlegen der Zone.", "error");
  }
}

function focusOnGeofence(id) {
  const gf = STATE.geofences.find(g => g.id === id);
  if (!gf || !STATE.map) return;
  switchTab("map");
  const layer = STATE.mapGeofences ? STATE.mapGeofences[id] : null;
  if (layer) {
    if (typeof layer.getBounds === "function") {
      STATE.map.fitBounds(layer.getBounds(), { padding: [50, 50], maxZoom: 17 });
    } else if (gf.center_lat && gf.center_lon) {
      STATE.map.setView([gf.center_lat, gf.center_lon], 16);
    }
    setTimeout(() => {
      layer.openPopup();
    }, 250);
  } else if (gf.center_lat && gf.center_lon) {
    STATE.map.setView([gf.center_lat, gf.center_lon], 16);
  }
}

function onZoneBreach(msg) {
  const isDanger = msg.severity === "danger";
  const icon = isDanger ? "🚨" : (msg.severity === "warning" ? "⚠️" : "📍");
  const title = isDanger ? "GEFAHRENZONE BETRETEN" : (msg.severity === "warning" ? "ABSCHNITT VERLASSEN" : "Zonen-Ereignis");

  // Akustischer Sirenen-Ton bei Gefahrenzonen
  if (isDanger) {
    playAlarmSound();
  }

  showToast(`${icon} ${title}`, msg.message, msg.severity || "info", 9000);

  // Visuelles Hervorheben der betroffenen Zone auf der Karte
  if (msg.zone_id && STATE.mapGeofences && STATE.mapGeofences[msg.zone_id]) {
    const layer = STATE.mapGeofences[msg.zone_id];
    const origWeight = layer.options?.weight || 2.5;
    layer.setStyle({ weight: 6, fillOpacity: 0.4 });
    setTimeout(() => {
      layer.setStyle({ weight: origWeight, fillOpacity: layer.options?.fillOpacity || 0.15 });
    }, 4500);
  }
}

async function deleteGeofence(id) {
  const gf = STATE.geofences.find(g => g.id === id);
  const name = gf ? gf.name : `Zone #${id}`;
  if (!confirm(`Möchten Sie die Zone '${name}' wirklich löschen?`)) return;

  try {
    const res = await fetch(`/api/geofences/${id}`, { method: "DELETE" });
    const data = await res.json();
    if (data.ok) {
      if (STATE.mapGeofences && STATE.mapGeofences[id]) {
        STATE.map?.removeLayer(STATE.mapGeofences[id]);
        delete STATE.mapGeofences[id];
      }
      STATE.geofences = STATE.geofences.filter(g => g.id !== id);
      renderGeofencesList();
      showToast("Zone gelöscht", `Zone '${name}' wurde entfernt.`, "info");
    }
  } catch(e) {
    console.error("Geofence delete Fehler:", e);
    showToast("Fehler", "Zone konnte nicht gelöscht werden", "error");
  }
}

async function deletePlan(id) {
  try {
    await fetch(`/api/plans/${id}`, { method: "DELETE" });
    STATE.plans = STATE.plans.filter(p => p.id !== id);
    renderPlansList();
  } catch(e) { console.error("Plan delete Fehler:", e); }
}

async function activatePlan(id) {
  try {
    const plan = await fetch(`/api/plans/${id}/activate`, { method: "POST" }).then(r => r.json());
    showToast("✓ Lageplan aktiviert", plan.plan?.name || "", "success");
  } catch(e) { console.error("Activate plan Fehler:", e); }
}

// ── Taktischer Marker Modus & Modal ──────────────────────────────
function setMarkerMode(active) {
  STATE.addingMarker = active;
  if (active && STATE.drawingGeofence) {
    cleanUpGeofenceDrawing();
  }
  const btn = document.getElementById("btn-add-marker");
  const mapContainer = STATE.map?.getContainer();

  if (active) {
    if (btn) {
      btn.classList.add("marker-mode-active");
      btn.innerHTML = "📍 Klick auf Karte... (ESC)";
    }
    if (mapContainer) mapContainer.style.cursor = "crosshair";
    showToast("📍 Marker-Modus", "Klicke auf den Einsatzort in der Karte (oder ESC zum Abbrechen)", "info", 4000);
    switchTab("map");
  } else {
    if (btn) {
      btn.classList.remove("marker-mode-active");
      btn.innerHTML = "＋ Marker";
    }
    if (mapContainer) mapContainer.style.cursor = "";
  }
}

function openMarkerModal(lat, lng) {
  const modal = document.getElementById("modal-add-marker");
  const latInput = document.getElementById("new-marker-lat");
  const lonInput = document.getElementById("new-marker-lon");
  const descInput = document.getElementById("new-marker-desc");
  const prioSelect = document.getElementById("new-marker-prio");

  if (latInput) latInput.value = parseFloat(lat).toFixed(6);
  if (lonInput) lonInput.value = parseFloat(lng).toFixed(6);
  if (descInput) {
    descInput.value = "";
    setTimeout(() => descInput.focus(), 60);
  }
  if (prioSelect) prioSelect.value = "normal";

  selectMarkerType("feuer");
  if (modal) modal.style.display = "flex";
}

function closeMarkerModal() {
  const modal = document.getElementById("modal-add-marker");
  if (modal) modal.style.display = "none";
}

function selectMarkerType(type) {
  STATE.selectedMarkerType = type;
  document.querySelectorAll("#marker-type-selector .marker-type-card").forEach(c => {
    if (c.getAttribute("data-type") === type) {
      c.classList.add("active");
    } else {
      c.classList.remove("active");
    }
  });
}

async function submitNewMarker() {
  const latInput = document.getElementById("new-marker-lat");
  const lonInput = document.getElementById("new-marker-lon");
  const descInput = document.getElementById("new-marker-desc");
  const prioSelect = document.getElementById("new-marker-prio");

  const lat = parseFloat(latInput?.value);
  const lon = parseFloat(lonInput?.value);
  if (isNaN(lat) || isNaN(lon)) {
    showToast("Fehler", "Ungültige Koordinaten für Marker", "warning");
    return;
  }

  const typ = STATE.selectedMarkerType || "feuer";
  const beschreibung = descInput?.value?.trim() || "";
  const prioritaet = prioSelect?.value || "normal";

  closeMarkerModal();

  try {
    const author = STATE.settings?.cfg_leitstelle_name || "Leitstelle";
    const res = await fetch("/api/markers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lat,
        lon,
        typ,
        beschreibung,
        prioritaet,
        author
      }),
    });
    if (res.ok) {
      const typeInfo = TACTICAL_TYPES[typ] || { label: typ };
      showToast("✓ Marker gesetzt", `${typeInfo.label}: ${beschreibung || 'Auf Karte platziert'}`, "success");
    } else {
      showToast("Fehler", "Marker konnte nicht gespeichert werden", "warning");
    }
  } catch (e) {
    console.error("Marker Fehler:", e);
    showToast("Fehler", String(e), "warning");
  }
}

function toggleMapLabels() {
  STATE.showMapLabels = !STATE.showMapLabels;
  localStorage.setItem("hcc_map_labels", STATE.showMapLabels ? "true" : "false");

  const btn = document.getElementById("btn-toggle-labels");
  if (btn) {
    if (STATE.showMapLabels) btn.classList.add("active");
    else btn.classList.remove("active");
  }

  const displayVal = STATE.showMapLabels ? "block" : "none";
  document.querySelectorAll(".radio-marker-tag, .tactical-pin-tag").forEach(el => {
    el.style.display = displayVal;
  });
  showToast("Beschriftungen", STATE.showMapLabels ? "Namensschilder eingeblendet" : "Namensschilder ausgeblendet", "info", 2000);
}

// ─────────────────────────────────────────────────────────────────
// Toast Notifications
// ─────────────────────────────────────────────────────────────────
function showToast(title, msg, type = "info", duration = 5000) {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const icons = { emergency: "🚨", success: "✅", warning: "⚠️", info: "ℹ️" };
  const icon  = icons[type] || "ℹ️";

  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${icon}</span>
    <div class="toast-body">
      <div class="toast-title">${escapeHTML(title)}</div>
      ${msg ? `<div class="toast-msg">${escapeHTML(msg)}</div>` : ""}
    </div>
    <span class="toast-close" onclick="this.parentElement.remove()">✕</span>
  `;
  container.appendChild(toast);

  if (duration > 0) {
    setTimeout(() => toast.remove(), duration);
  }
}

// ─────────────────────────────────────────────────────────────────
// Tab Navigation
// ─────────────────────────────────────────────────────────────────
function switchTab(tabName) {
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));

  const panel = document.getElementById(`tab-${tabName}`);
  const nav   = document.getElementById(`nav-${tabName}`);
  if (panel) panel.classList.add("active");
  if (nav)   nav.classList.add("active");

  STATE.currentTab = tabName;

  // Tab-spezifische Aktionen
  if (tabName === "map") {
    setTimeout(() => STATE.map?.invalidateSize(), 100);
  }
  if (tabName === "sms") {
    STATE.unreadSMS = 0;
    updateSMSBadge();
  }
  if (tabName === "timeline") {
    loadTimeline();
  }
  if (tabName === "diagnostics") {
    loadPortStats();
  }
  if (tabName === "infrastructure") {
    loadHardwareStatus();
  }
}

async function loadPortStats() {
  try {
    const data = await fetch("/api/diagnostics/port_stats").then(r => r.json());
    updatePortStats(data);
  } catch(e) { /* Ignore */ }
}

async function loadHardwareStatus() {
  try {
    const data = await fetch("/api/hardware/status").then(r => r.json());
    if (data.repeater) onRepeaterUpdate({ state: data.repeater });
    if (data.ups)      onUPSUpdate({ data: data.ups });
    if (data.zte)      onZTEUpdate({ data: data.zte });
    if (data.omada)    onOmadaUpdate({ data: data.omada });
    loadRepeaterLogs();
  } catch(e) { /* Ignore */ }
}

// ─────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────
function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value ?? "—";
}

function setBadge(id, status) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = `status-badge ${status}`;
}

function setChip(id, online, alarm) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!online) { el.className = "chip chip-offline"; el.textContent = "Offline"; return; }
  if (alarm)   { el.className = "chip chip-warning"; el.textContent = "⚠ Alarm"; return; }
  el.className = "chip chip-online"; el.textContent = "● Online";
}

function setProgressBar(id, pct) {
  const el = document.getElementById(id);
  if (el) el.style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

function colorizeMetric(id, value, thresholds) {
  const el = document.getElementById(id);
  if (!el) return;
  for (const [limit, cls] of thresholds) {
    if (value <= limit) {
      el.className = `metric-value ${cls}`;
      return;
    }
  }
}

function colorizeProgressBar(id, value, thresholds) {
  const el = document.getElementById(id);
  if (!el) return;
  for (const [limit, cls] of thresholds) {
    if (value <= limit) {
      el.className = `progress-fill ${cls}`;
      return;
    }
  }
  el.className = "progress-fill good";
}

function getAvatarText(rid) {
  const alias = STATE.radios[rid]?.alias || `${rid}`;
  return alias.replace(/[^A-Za-z0-9]/g, "").substring(0, 3).toUpperCase() || rid.toString().slice(-3);
}

function escapeHTML(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatUptime(s) {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// ─────────────────────────────────────────────────────────────────
// Clock
// ─────────────────────────────────────────────────────────────────
function updateClock() {
  const el = document.getElementById("topbar-clock");
  if (el) el.textContent = new Date().toLocaleTimeString("de-DE");
}

// ─────────────────────────────────────────────────────────────────
// Event-Listener Setup
// ─────────────────────────────────────────────────────────────────
function setupEventListeners() {
  // Tab-Navigation
  document.querySelectorAll(".nav-item[data-tab]").forEach(el => {
    el.addEventListener("click", () => switchTab(el.dataset.tab));
  });

  // Radio hinzufügen
  document.getElementById("btn-add-radio")?.addEventListener("click", () => {
    document.getElementById("modal-add-radio").style.display = "flex";
  });
  document.getElementById("btn-confirm-add-radio")?.addEventListener("click", async () => {
    const rid   = parseInt(document.getElementById("new-radio-id").value);
    const alias = document.getElementById("new-radio-alias").value;
    const model = document.getElementById("new-radio-model").value;
    if (!rid || rid < 1000) { alert("Ungültige Radio-ID (min. 1000)"); return; }
    try {
      await fetch("/api/radios", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ radio_id: rid, alias, device_model: model }),
      });
      STATE.radios[rid] = { radio_id: rid, alias, device_model: model, online: false };
      document.getElementById("modal-add-radio").style.display = "none";
      renderSidebarUnits();
      renderRadiosTable();
      updateKPIs();
      showToast("✓ Gerät hinzugefügt", alias || `Radio ${rid}`, "success");
    } catch(e) { console.error("Add radio Fehler:", e); }
  });

  // BOS Schnellvorlagen für SMS
  document.querySelectorAll(".sms-tpl-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const text = btn.getAttribute("data-text");
      const input = document.getElementById("sms-text-input");
      if (input && text) {
        input.value = text;
        input.focus();
      }
    });
  });

  // SMS Broadcast Checkbox
  const broadcastCheck = document.getElementById("sms-broadcast-check");
  const targetIdInput = document.getElementById("sms-target-id");
  if (broadcastCheck && targetIdInput) {
    broadcastCheck.addEventListener("change", () => {
      if (broadcastCheck.checked) {
        targetIdInput.value = "16777215";
        targetIdInput.disabled = true;
      } else {
        targetIdInput.value = "";
        targetIdInput.disabled = false;
        targetIdInput.focus();
      }
    });
  }

  // SMS senden
  document.getElementById("btn-send-sms")?.addEventListener("click", async () => {
    const isBroadcast = document.getElementById("sms-broadcast-check")?.checked;
    const targetId = isBroadcast ? 16777215 : (parseInt(document.getElementById("sms-target-id").value) || 0);
    const text     = document.getElementById("sms-text-input").value.trim();
    if (!text) return;
    try {
      if (isBroadcast || targetId === 16777215) {
        await fetch("/api/sms/broadcast", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sender_id: 0, text }),
        });
        showToast("📢 SMS-Rundruf gesendet", text, "good");
      } else {
        await fetch("/api/sms/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sender_id: 0, target_id: targetId, text }),
        });
        showToast("✓ SMS gesendet", `An Radio ${targetId}`, "info");
      }
      document.getElementById("sms-text-input").value = "";
    } catch(e) { console.error("SMS Fehler:", e); }
  });

  // SMS Enter-Taste
  document.getElementById("sms-text-input")?.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      document.getElementById("btn-send-sms")?.click();
    }
  });

  // Einstellungen speichern
  document.getElementById("btn-save-settings")?.addEventListener("click", async () => {
    const settings = {
      hw_repeater_ip:         document.getElementById("cfg-repeater-ip")?.value,
      hw_repeater_trap_port:  parseInt(document.getElementById("cfg-trap-port")?.value) || 10162,
      hw_usv_ip:              document.getElementById("cfg-usv-ip")?.value,
      hw_usv_port:            parseInt(document.getElementById("cfg-usv-port")?.value) || 502,
      hw_zte_ip:              document.getElementById("cfg-zte-ip")?.value,
      hw_omada_ip:            document.getElementById("cfg-omada-ip")?.value,
      mock_active:            document.getElementById("cfg-mock-active")?.checked || false,
      audio_enabled:          document.getElementById("cfg-audio-enabled")?.checked || false,
      breadcrumbs_enabled:    document.getElementById("cfg-breadcrumbs")?.checked !== false,
      auto_pan:               document.getElementById("cfg-auto-pan")?.checked || false,
      cfg_map_provider:       document.getElementById("cfg-map-provider")?.value || "osm",
      cfg_map_api_key:        document.getElementById("cfg-map-api-key")?.value || "",
    };
    try {
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      });
      Object.assign(STATE.settings, settings);
      applySettings(settings);
      // Karten-Provider wechseln
      if (STATE.map) applyMapProvider(settings.cfg_map_provider);
      showToast("✓ Einstellungen gespeichert", "", "success");
    } catch(e) { console.error("Settings Fehler:", e); }
  });

  // Einsatz speichern
  document.getElementById("btn-save-mission")?.addEventListener("click", async () => {
    const data = {
      mission_title:    document.getElementById("mission-title")?.value,
      mission_code:     document.getElementById("mission-code")?.value,
      mission_leader:   document.getElementById("mission-leader")?.value,
      mission_location: document.getElementById("mission-location")?.value,
      mission_channel:  document.getElementById("mission-channel")?.value,
      mission_notes:    document.getElementById("mission-notes")?.value,
      mission_status:   document.getElementById("mission-status")?.value,
    };
    try {
      await fetch("/api/mission", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      applyMissionData(data);
      showToast("✓ Einsatz gespeichert", data.mission_title || "", "success");
    } catch(e) { console.error("Mission Fehler:", e); }
  });

  // Neuer Einsatz
  document.getElementById("btn-new-mission")?.addEventListener("click", () => {
    ["mission-title","mission-code","mission-leader","mission-location","mission-notes"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });
    showToast("Neuer Einsatz", "Felder zurückgesetzt", "info");
  });

  // Marker setzen & Labels umschalten
  document.getElementById("btn-add-marker")?.addEventListener("click", () => {
    setMarkerMode(!STATE.addingMarker);
  });

  // Taktische Zonen / Geofence anlegen
  document.getElementById("btn-add-geofence")?.addEventListener("click", openAddGeofenceModal);
  document.getElementById("new-geofence-name")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      startGeofenceDrawing();
    }
  });

  document.getElementById("btn-toggle-labels")?.addEventListener("click", toggleMapLabels);

  // Marker Modal Aktionen
  document.getElementById("btn-close-add-marker")?.addEventListener("click", closeMarkerModal);
  document.getElementById("btn-cancel-add-marker")?.addEventListener("click", closeMarkerModal);
  document.getElementById("btn-confirm-add-marker")?.addEventListener("click", submitNewMarker);

  document.getElementById("new-marker-desc")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submitNewMarker();
    }
  });

  // Marker Typ Selektor
  document.querySelectorAll("#marker-type-selector .marker-type-card").forEach(card => {
    card.addEventListener("click", () => {
      const type = card.getAttribute("data-type");
      if (type) selectMarkerType(type);
    });
  });

  // Timeline laden
  document.getElementById("btn-load-timeline")?.addEventListener("click", loadTimeline);

  // Paket-Dump
  document.getElementById("btn-toggle-dump")?.addEventListener("click", async () => {
    STATE.dumpActive = !STATE.dumpActive;
    const btn = document.getElementById("btn-toggle-dump");
    if (btn) btn.textContent = STATE.dumpActive ? "⏸ Dump stoppen" : "▶ Dump starten";
    try {
      await fetch("/api/settings/packet_dump", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enable: STATE.dumpActive }),
      });
      if (STATE.dumpActive) {
        // Bestehende Pakete laden
        const packets = await fetch("/api/diagnostics/packets?limit=100").then(r => r.json());
        STATE.packets = packets;
        renderPacketList();
      }
    } catch(e) { /* Ignore */ }
  });

  document.getElementById("btn-clear-dump")?.addEventListener("click", async () => {
    STATE.packets = [];
    renderPacketList();
    try { await fetch("/api/diagnostics/packets", { method: "DELETE" }); } catch(e) {}
  });

  // Hardware Alle aktualisieren
  document.getElementById("btn-poll-all")?.addEventListener("click", () => {
    loadHardwareStatus();
    showToast("🔄 Hardware-Status", "Aktualisierung angefordert", "info");
  });

  // USV Register-Scan
  document.getElementById("btn-ups-scan")?.addEventListener("click", async () => {
    try {
      showToast("🔬 Register-Scan", "Scanne alle Modbus-Register…", "info");
      const data = await fetch("/api/hardware/ups/scan").then(r => r.json());
      console.log("USV Register-Scan:", data);
      showToast("✓ Scan abgeschlossen", `${Object.keys(data).length} Register gelesen`, "success");
    } catch(e) { showToast("⚠ Scan Fehler", String(e), "warning"); }
  });

  // USV Zähler zurücksetzen
  document.getElementById("btn-ups-reset-counters")?.addEventListener("click", async () => {
    if (!confirm("Energieverbrauch (kWh) und Netzausfallzähler der USV auf 0 zurücksetzen?")) return;
    try {
      const res = await fetch("/api/hardware/ups/reset_counters", { method: "POST" });
      const data = await res.json();
      if (data.status === "ok") {
        showToast("✓ USV-Zähler", "Energie- und Ausfallzähler wurden zurückgesetzt", "good");
        setText("ups-energy-kwh", "0.000 kWh");
        setText("ups-transfers", "0 Mal (0s)");
      } else {
        showToast("⚠ Fehler", data.detail || "Zähler konnten nicht zurückgesetzt werden", "warning");
      }
    } catch (e) {
      showToast("⚠ Fehler", String(e), "warning");
    }
  });

  // IDs JSON export
  document.getElementById("btn-export-ids")?.addEventListener("click", () => {
    window.open("/api/export/ids_json", "_blank");
  });

  // Web-PTT Dispatcher & Mobile Transmitter Setup
  setupWebPttListeners();
}

// ─────────────────────────────────────────────────────────────────
// App Init
// ─────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  console.log("[HCC] Hytera Command Center gestartet");

  initTheme();
  setupEventListeners();
  updateClock();
  setInterval(updateClock, 1000);
  connectWS();
  initAudioPlayer();
  loadProfilesList();
  populateAudioInputDevices();

  // Port-Stats initial laden
  setTimeout(loadPortStats, 2000);
});

// ─────────────────────────────────────────────────────────────────
// Utility Functions
// ─────────────────────────────────────────────────────────────────

/**
 * Formatiert Bytes in lesbare Größe (B, KB, MB, GB)
 */
function formatBytes(bytes) {
  if (bytes == null || bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(Math.abs(bytes)) / Math.log(k));
  const idx = Math.min(i, sizes.length - 1);
  return `${(bytes / Math.pow(k, idx)).toFixed(1)} ${sizes[idx]}`;
}

/**
 * Formatiert Sekunden in lesbares Uptime-Format (z.B. "2T 14h 30m")
 */
function formatUptime(seconds) {
  if (seconds == null) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}T ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

/**
 * USV-Mini-Chart: Zeichnet einen SVG-Balkenchart für aktuelle USV-Werte.
 * Zeigt: Batterie %, Last %, Spannung Out, Strom Out
 */
const _upsHistory = { batt: [], load: [], volt: [], power: [] };
const _UPS_HISTORY_MAX = 30;

function updateUPSChart(d) {
  // History befüllen
  if (d.batt_pct    != null) { _upsHistory.batt.push(d.batt_pct);  if (_upsHistory.batt.length  > _UPS_HISTORY_MAX) _upsHistory.batt.shift();  }
  if (d.load_pct    != null) { _upsHistory.load.push(d.load_pct);  if (_upsHistory.load.length  > _UPS_HISTORY_MAX) _upsHistory.load.shift();  }
  if (d.output_volt != null) { _upsHistory.volt.push(d.output_volt); if (_upsHistory.volt.length > _UPS_HISTORY_MAX) _upsHistory.volt.shift(); }
  if (d.output_power_w != null) { _upsHistory.power.push(d.output_power_w); if (_upsHistory.power.length > _UPS_HISTORY_MAX) _upsHistory.power.shift(); }

  // Canvas-Chart zeichnen (falls Element vorhanden)
  drawSparkline("ups-chart-batt",  _upsHistory.batt,  0,   100,  "#3fb950");
  drawSparkline("ups-chart-load",  _upsHistory.load,  0,   100,  "#f0883e");
  drawSparkline("ups-chart-volt",  _upsHistory.volt,  200, 250,  "#2f81f7");
  drawSparkline("ups-chart-power", _upsHistory.power, 0,   2000, "#bc8cff");
}

/**
 * Zeichnet eine mini Sparkline in ein Canvas-Element.
 */
function drawSparkline(canvasId, data, minVal, maxVal, color) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !canvas.getContext) return;
  const ctx    = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;

  ctx.clearRect(0, 0, W, H);

  if (data.length < 2) return;

  const range = maxVal - minVal || 1;
  const step  = W / (data.length - 1);

  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth   = 1.5;
  ctx.lineJoin    = "round";

  data.forEach((v, i) => {
    const x = i * step;
    const y = H - ((v - minVal) / range) * H;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Fill
  ctx.lineTo((data.length - 1) * step, H);
  ctx.lineTo(0, H);
  ctx.closePath();
  ctx.fillStyle = color + "22"; // 13% opacity
  ctx.fill();
}

/**
 * Lädt den aktuellen Hardware-Status von der API.
 */
async function loadHardwareStatus() {
  try {
    const data = await fetch("/api/hardware/status").then(r => r.json());
    if (data.ups)     onUPSUpdate({ type: "ups_update",        ...data.ups     });
    if (data.zte)     onZTEUpdate({ type: "zte_update",        ...data.zte     });
    if (data.omada)   onOmadaUpdate({ type: "omada_update",    ...data.omada   });
    if (data.repeater) onRepeaterUpdate({ type: "repeater_update", ...data.repeater });
  } catch(e) {
    console.warn("[HW] Status-Abruf fehlgeschlagen:", e);
  }
}

// ─────────────────────────────────────────────────────────────────
// Dispatcher Audio Player (Leitstelle Schnell-Anhör-Funktion)
// ─────────────────────────────────────────────────────────────────
let currentPlayingPttId = null;

function initAudioPlayer() {
  const audio = document.getElementById("global-audio-element");
  const playPauseBtn = document.getElementById("audio-play-pause-btn");
  const rewindBtn = document.getElementById("audio-rewind-btn");
  const seekSlider = document.getElementById("audio-seek-slider");
  const currentTimeEl = document.getElementById("audio-current-time");
  const durationTimeEl = document.getElementById("audio-duration-time");
  const closeBtn = document.getElementById("audio-close-btn");
  const speedBtns = document.querySelectorAll(".audio-speed-btn");

  if (!audio) return;

  function formatTime(s) {
    if (isNaN(s) || s == null) return "0:00";
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec < 10 ? '0' : ''}${sec}`;
  }

  audio.addEventListener("timeupdate", () => {
    if (!audio.duration) return;
    const pct = (audio.currentTime / audio.duration) * 100;
    if (seekSlider && !seekSlider.matches(":active")) {
      seekSlider.value = pct;
    }
    if (currentTimeEl) currentTimeEl.textContent = formatTime(audio.currentTime);
  });

  audio.addEventListener("loadedmetadata", () => {
    if (durationTimeEl) durationTimeEl.textContent = formatTime(audio.duration);
    if (seekSlider) seekSlider.value = 0;
  });

  audio.addEventListener("play", () => {
    if (playPauseBtn) playPauseBtn.textContent = "⏸";
    updateActivePlayButtons(true);
  });

  audio.addEventListener("pause", () => {
    if (playPauseBtn) playPauseBtn.textContent = "▶";
    updateActivePlayButtons(false);
  });

  audio.addEventListener("ended", () => {
    if (playPauseBtn) playPauseBtn.textContent = "▶";
    updateActivePlayButtons(false);
    if (seekSlider) seekSlider.value = 0;
  });

  if (playPauseBtn) {
    playPauseBtn.addEventListener("click", () => {
      if (audio.paused) {
        audio.play().catch(e => console.warn("Audio Play Error:", e));
      } else {
        audio.pause();
      }
    });
  }

  if (rewindBtn) {
    rewindBtn.addEventListener("click", () => {
      audio.currentTime = Math.max(0, audio.currentTime - 5.0);
    });
  }

  if (seekSlider) {
    seekSlider.addEventListener("input", (e) => {
      if (!audio.duration) return;
      const targetTime = (e.target.value / 100) * audio.duration;
      audio.currentTime = targetTime;
    });
  }

  if (closeBtn) {
    closeBtn.addEventListener("click", () => {
      audio.pause();
      audio.currentTime = 0;
      currentPlayingPttId = null;
      updateActivePlayButtons(false);
      const bar = document.getElementById("global-audio-bar");
      if (bar) bar.style.display = "none";
    });
  }

  speedBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      speedBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const speed = parseFloat(btn.dataset.speed || "1.0");
      audio.playbackRate = speed;
    });
  });

  // Timeline-Filter Button
  const filterBtn = document.getElementById("btn-filter-ptt-audio");
  if (filterBtn) {
    filterBtn.addEventListener("click", () => {
      STATE.timelineAudioOnly = !STATE.timelineAudioOnly;
      filterBtn.classList.toggle("btn-primary", STATE.timelineAudioOnly);
      filterBtn.classList.toggle("btn-secondary", !STATE.timelineAudioOnly);
      loadTimeline();
    });
  }

  // Leitstellen Tastaturkürzel (Replay, Pause, Spulen, Marker, Labels)
  document.addEventListener("keydown", (e) => {
    // ESC bricht Modale und Marker-Modus immer ab, auch wenn Input fokussiert ist
    if (e.key === "Escape") {
      if (STATE.settingRadioPositionId) {
        e.preventDefault();
        STATE.settingRadioPositionId = null;
        const mapEl = document.getElementById("map");
        if (mapEl) mapEl.style.cursor = "";
        showToast("Positionierung", "Abgebrochen", "info", 2000);
        return;
      }
      if (STATE.addingMarker) {
        e.preventDefault();
        setMarkerMode(false);
        showToast("Marker", "Abgebrochen", "info", 2000);
        return;
      }
      if (STATE.drawingGeofence) {
        e.preventDefault();
        cancelGeofenceDrawing();
        return;
      }
      const geofenceModal = document.getElementById("modal-add-geofence");
      if (geofenceModal && geofenceModal.style.display !== "none") {
        e.preventDefault();
        closeAddGeofenceModal();
        return;
      }
      const markerModal = document.getElementById("modal-add-marker");
      if (markerModal && markerModal.style.display !== "none") {
        e.preventDefault();
        closeMarkerModal();
        return;
      }
    }

    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;

    // F8 oder Alt+R: Letzten Funkspruch abspielen
    if (e.key === "F8" || (e.altKey && (e.key === "r" || e.key === "R"))) {
      e.preventDefault();
      playLatestAudio();
      return;
    }

    // Taste L: Beschriftungen auf Karte an/aus
    if ((e.key === "l" || e.key === "L") && !e.ctrlKey && !e.altKey && STATE.currentTab === "map") {
      e.preventDefault();
      toggleMapLabels();
      return;
    }

    const audioEl = document.getElementById("global-audio-element");
    const barEl = document.getElementById("global-audio-bar");
    if (!audioEl || !barEl || barEl.style.display === "none") return;

    if (e.key === "k" || e.key === "K") {
      e.preventDefault();
      if (audioEl.paused) audioEl.play().catch(e => console.warn(e));
      else audioEl.pause();
    } else if (e.key === "ArrowLeft" || e.key === "j" || e.key === "J") {
      e.preventDefault();
      audioEl.currentTime = Math.max(0, audioEl.currentTime - 5.0);
    } else if (e.key === "ArrowRight" || e.key === "l" || e.key === "L") {
      e.preventDefault();
      audioEl.currentTime = Math.min(audioEl.duration || 0, audioEl.currentTime + 5.0);
    } else if (e.key === "Escape") {
      e.preventDefault();
      audioEl.pause();
      barEl.style.display = "none";
      currentPlayingPttId = null;
      updateActivePlayButtons(false);
    }
  });
}

// ── Audio Boost (Web Audio API) ───────────────────────────────────
let boostAudioCtx = null;
let boostGainNode = null;
let boostSourceNode = null;
let audioBoostActive = false;

function toggleAudioBoost() {
  const audio = document.getElementById("global-audio-element");
  const btn = document.getElementById("audio-boost-btn");
  if (!audio) return;
  try {
    if (!boostAudioCtx) {
      boostAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
      boostSourceNode = boostAudioCtx.createMediaElementSource(audio);
      boostGainNode = boostAudioCtx.createGain();
      boostSourceNode.connect(boostGainNode);
      boostGainNode.connect(boostAudioCtx.destination);
    }
    if (boostAudioCtx.state === "suspended") {
      boostAudioCtx.resume();
    }
    audioBoostActive = !audioBoostActive;
    if (boostGainNode) {
      boostGainNode.gain.value = audioBoostActive ? 2.5 : 1.0;
    }
    if (btn) {
      btn.classList.toggle("active", audioBoostActive);
      btn.textContent = audioBoostActive ? "🔊 Boost (+8dB) AN" : "🔊 Boost";
    }
    showToast(audioBoostActive ? "Sprachverstärkung (+8dB) aktiviert" : "Sprachverstärkung normal", "info");
  } catch (err) {
    console.warn("Audio Boost error:", err);
  }
}

// ── Leitstellen Schnell-Wiederholung (Letzter Funkspruch) ───────────
let latestRecordedCall = null;

function updateLatestCallButton() {
  const btn = document.getElementById("btn-replay-latest-call");
  const label = document.getElementById("btn-replay-label");
  if (!btn || !label) return;

  const c = STATE.calls.find(call => Boolean(call.audio_url || call.id));
  if (c) {
    latestRecordedCall = c;
    const radio = STATE.radios[c.radio_id];
    const alias = radio?.alias || `Radio ${c.radio_id}`;
    const time = (c.timestamp || c.created_at) ? new Date(c.timestamp || c.created_at).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : "";
    label.textContent = `↺ Letzter Funkspruch (${alias}${time ? ' ' + time : ''})`;
    btn.style.opacity = "1";
    btn.disabled = false;
  } else {
    label.textContent = `↺ Letzter Funkspruch`;
    btn.style.opacity = "0.6";
  }
}

function playLatestAudio() {
  if (latestRecordedCall) {
    const radio = STATE.radios[latestRecordedCall.radio_id];
    const alias = radio?.alias || `Radio ${latestRecordedCall.radio_id}`;
    const dur = latestRecordedCall.duration_ms ? `${(latestRecordedCall.duration_ms / 1000).toFixed(1)}s` : "";
    playAudio(latestRecordedCall.id, alias, dur);
    return;
  }
  fetch("/api/calls?limit=1")
    .then(r => r.json())
    .then(calls => {
      if (calls && calls.length > 0) {
        const c = calls[0];
        const radio = STATE.radios[c.radio_id];
        const alias = radio?.alias || `Radio ${c.radio_id}`;
        playAudio(c.id, alias, "");
      } else {
        showToast("Noch keine Funksprüche mit Audio aufgezeichnet.", "warning");
      }
    })
    .catch(() => showToast("Keine Funksprüche verfügbar.", "warning"));
}

function updateActivePlayButtons(isPlaying) {
  document.querySelectorAll(".btn-play-audio").forEach(btn => {
    const pttId = parseInt(btn.dataset.ptt);
    if (pttId === currentPlayingPttId && isPlaying) {
      btn.classList.add("playing");
      btn.textContent = "⏸ Pause";
    } else {
      btn.classList.remove("playing");
      btn.textContent = "▶ Anhören";
    }
  });
}

function playAudio(pttId, alias, details) {
  const audio = document.getElementById("global-audio-element");
  const bar = document.getElementById("global-audio-bar");
  const titleEl = document.getElementById("audio-bar-title");
  const metaEl = document.getElementById("audio-bar-meta");
  const downloadLink = document.getElementById("audio-download-link");

  if (!audio || !bar) return;

  const streamUrl = `/api/audio/${pttId}`;

  // Wenn derselbe Funkspruch bereits abgespielt wird, toggle Play/Pause
  if (currentPlayingPttId === pttId) {
    if (audio.paused) {
      audio.play().catch(e => console.warn(e));
    } else {
      audio.pause();
    }
    return;
  }

  currentPlayingPttId = pttId;
  bar.style.display = "flex";
  if (titleEl) titleEl.textContent = `🎙️ ${alias || 'Funkspruch'}`;
  if (metaEl) metaEl.textContent = `PTT #${pttId} ${details ? '• ' + details : ''}`;
  if (downloadLink) {
    downloadLink.href = streamUrl;
    downloadLink.setAttribute("download", `funkspruch_${pttId}.wav`);
  }

  audio.src = streamUrl;
  audio.currentTime = 0;
  audio.play().catch(err => {
    console.warn("Autoplay blockiert oder Datei nicht gefunden:", err);
    showToast("Audiodatei wird geladen oder ist noch nicht verfügbar.", "warning");
  });
}

function onAudioReady(msg) {
  // Update call in STATE.calls if present
  const c = STATE.calls.find(call => call.id === msg.ptt_id);
  if (c) {
    c.audio_url = msg.url;
    renderDashboardCalls();
  } else {
    fetch("/api/calls?limit=10")
      .then(r => r.json())
      .then(calls => { STATE.calls = calls; renderDashboardCalls(); updateKPIs(); });
  }
  updateLatestCallButton();
  showToast(`🎙️ Aufnahme bereit: PTT #${msg.ptt_id} (${msg.duration}s) – Klick oben auf '↺ Letzter Funkspruch'`, "info");
}

// ═══════════════════════════════════════════════════════════════════
// Web-PTT Dispatcher & Smartphone Audio Transmitter
// ═══════════════════════════════════════════════════════════════════
let pttAudioContext = null;
let pttMediaStream = null;
let pttScriptNode = null;
let pttAudioWs = null;
let isWebPttActive = false;
let pttTimerInterval = null;
let pttStartTimestamp = 0;

async function initPttAudio(deviceId = null) {
  if (pttMediaStream) {
    try { pttMediaStream.getTracks().forEach(t => t.stop()); } catch(e) {}
    pttMediaStream = null;
  }
  // Prüfen, ob der Browser Mikrofonzugriff im aktuellen Kontext erlaubt
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    const isLocal = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
    if (!isLocal && window.location.protocol === "http:") {
      showToast(
        "⚠️ Browser blockiert Mikrofon über LAN-IP",
        "Aufgrund der Browser-Sicherheitsrichtlinie muss die Seite auf diesem PC über 'http://localhost:8000' aufgerufen werden (oder via HTTPS).",
        "emergency",
        10000
      );
    } else {
      showToast(
        "⚠️ Mikrofon im Browser nicht erlaubt",
        "Bitte klicken Sie links neben der Webadresse (URL) auf das Schloss/Einstellungs-Icon und stellen Sie 'Mikrofon' auf 'Zulassen'.",
        "warning",
        8000
      );
    }
    return false;
  }

  try {
    const audioConstraints = {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    };
    const deviceSelect = document.getElementById("ptt-audio-device-select");
    const targetDevId = deviceId || deviceSelect?.value;
    if (targetDevId) {
      audioConstraints.deviceId = { exact: targetDevId };
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
    pttMediaStream = stream;
    pttAudioContext = new (window.AudioContext || window.webkitAudioContext)();
    const source = pttAudioContext.createMediaStreamSource(stream);

    // BufferSize: 2048 samples für flüssiges Streaming
    pttScriptNode = pttAudioContext.createScriptProcessor(2048, 1, 1);
    source.connect(pttScriptNode);
    // Durch Stummschaltung (Gain=0) schleifen, um Rückkopplung auf eigene Lautsprecher zu verhindern
    const silenceGain = pttAudioContext.createGain();
    silenceGain.gain.value = 0;
    pttScriptNode.connect(silenceGain);
    silenceGain.connect(pttAudioContext.destination);

    pttScriptNode.onaudioprocess = (e) => {
      const inputData = e.inputBuffer.getChannelData(0);

      // VU-Meter RMS berechnen
      let sum = 0;
      for (let i = 0; i < inputData.length; i++) {
        sum += inputData[i] * inputData[i];
      }
      const rms = Math.sqrt(sum / inputData.length);
      updatePttVuMeter(rms);

      // Falls PTT aktiv: in 16-Bit PCM konvertieren und per WS streamen
      if (isWebPttActive && pttAudioWs && pttAudioWs.readyState === WebSocket.OPEN) {
        const int16Buffer = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
          const s = Math.max(-1, Math.min(1, inputData[i]));
          int16Buffer[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        pttAudioWs.send(int16Buffer.buffer);
      }
    };

    connectPttAudioWs();
    populateAudioInputDevices();
    showToast("✓ Mikrofon bereit für PTT", "good", 3000);
    return true;
  } catch (err) {
    console.error("Mikrofon-Zugriff verweigert:", err);
    if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
      showToast(
        "⚠️ Mikrofon-Berechtigung verweigert",
        "Klicken Sie links neben der Webadresse (URL) auf das Schloss-/Website-Einstellungs-Symbol und wählen Sie bei Mikrofon 'Zulassen'.",
        "emergency",
        10000
      );
    } else if (err.name === "NotFoundError" || err.name === "DevicesNotFoundError") {
      showToast("Kein Mikrofon gefunden", "Es wurde kein aktives Mikrofon an diesem Computer erkannt.", "warning");
    } else {
      showToast("Mikrofon-Fehler", String(err.message || err), "warning");
    }
    return false;
  }
}

function connectPttAudioWs() {
  if (pttAudioWs && (pttAudioWs.readyState === WebSocket.OPEN || pttAudioWs.readyState === WebSocket.CONNECTING)) {
    return;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/tx_audio`;
  pttAudioWs = new WebSocket(wsUrl);
  pttAudioWs.binaryType = "arraybuffer";

  pttAudioWs.onopen = () => {
    console.log("[Web-PTT] Audio-WebSocket verbunden");
  };
  pttAudioWs.onclose = () => {
    console.log("[Web-PTT] Audio-WebSocket getrennt");
    setTimeout(connectPttAudioWs, 3000);
  };
  pttAudioWs.onmessage = (e) => {
    try {
      if (typeof e.data === "string") {
        const msg = JSON.parse(e.data);
        if (msg.type === "ptt_ack" && msg.action === "press" && !msg.success && msg.reason === "channel_busy") {
          console.warn("[Web-PTT] Sendung vom Server abgelehnt:", msg.message);
          playBusyBonkSound();
          showToast("⚠️ Kanal belegt", msg.message || "Kanal ist belegt.", "warning", 3500);
          if (isWebPttActive) stopWebPtt();
        }
      }
    } catch (err) {
      console.debug("[Web-PTT] WS Msg Error:", err);
    }
  };
  pttAudioWs.onerror = (e) => console.debug("[Web-PTT] WS Error:", e);
}

function updatePttVuMeter(rms) {
  const bars = [
    document.getElementById("vu-bar-1"),
    document.getElementById("vu-bar-2"),
    document.getElementById("vu-bar-3"),
    document.getElementById("vu-bar-4"),
    document.getElementById("vu-bar-5"),
  ];
  if (!bars[0]) return;

  const thresholds = [0.015, 0.05, 0.12, 0.25, 0.45];
  bars.forEach((bar, idx) => {
    if (rms > thresholds[idx]) {
      bar.className = `ptt-vu-bar level-${idx + 1}`;
    } else {
      bar.className = "ptt-vu-bar";
    }
  });
}

function onPttCallTypeSelectChange() {
  const sel = document.getElementById("ptt-calltype-select");
  const tgInput = document.getElementById("ptt-target-tg");
  const tgLabel = document.querySelector('label[for="ptt-target-tg"]');
  if (!sel || !tgInput) return;
  const val = sel.value;
  if (val === "2") { // All-Call
    tgInput.dataset.prevTg = tgInput.value;
    tgInput.value = "16777215";
    tgInput.disabled = true;
    tgInput.title = "DMR All-Call Broadcast (an alle Funkgeräte auf diesem Zeitschlitz)";
    if (tgLabel) tgLabel.textContent = "Ziel:";
  } else if (val === "0") { // Einzelruf
    if (tgInput.value === "16777215") {
      tgInput.value = tgInput.dataset.prevTg || "4001";
    }
    tgInput.disabled = false;
    tgInput.title = "Ziel-Funkgeräte-ID (z. B. 4001)";
    if (tgLabel) tgLabel.textContent = "Ziel-ID:";
  } else { // Gruppe
    if (tgInput.value === "16777215") {
      tgInput.value = tgInput.dataset.prevTg || "1";
    }
    tgInput.disabled = false;
    tgInput.title = "Ziel-Talkgroup (z. B. 1 oder 9)";
    if (tgLabel) tgLabel.textContent = "Ziel-TG:";
  }
}

function onPttSlotSelectChange() {
  updateChannelBusyUI();
}

function updateChannelBusyUI() {
  const slotSel = document.getElementById("ptt-slot-select");
  const slot = (slotSel ? slotSel.value : "TS1").toUpperCase();
  const badge = document.getElementById("ptt-channel-status-badge");
  const btn = document.getElementById("btn-web-ptt");
  const btnText = document.getElementById("ptt-btn-text");
  const overrideCheckbox = document.getElementById("ptt-priority-override");
  const isOverride = overrideCheckbox ? overrideCheckbox.checked : false;

  const slotInfo = STATE.channelBusy?.[slot] || { busy: false };
  const isBusy = Boolean(slotInfo.busy);

  if (badge) {
    if (isBusy) {
      const alias = STATE.radios[slotInfo.radio_id]?.alias || (slotInfo.radio_id ? `ID ${slotInfo.radio_id}` : "Funkgerät");
      badge.className = "ptt-channel-badge badge-busy";
      badge.textContent = `⚠️ ${slot} BELEGT (${alias})`;
      badge.title = `${slot} wird aktuell genutzt von ${alias}.`;
    } else {
      badge.className = "ptt-channel-badge badge-free";
      badge.textContent = `● ${slot} FREI`;
      badge.title = `Kanal ${slot} ist frei.`;
    }
  }

  // Button nur aktualisieren, wenn PTT gerade NICHT aktiv sendet
  if (!isWebPttActive && btn && btnText) {
    if (isBusy && !isOverride) {
      btn.classList.remove("btn-override");
      btn.classList.add("btn-busy");
      btnText.textContent = "⚠️ KANAL BELEGT";
      btn.title = `${slot} ist belegt durch Funkgerät. Haken bei 'Vorrang' setzen zum Überstimmen.`;
    } else if (isBusy && isOverride) {
      btn.classList.remove("btn-busy");
      btn.classList.add("btn-override");
      btnText.textContent = "⚡ VORRANG-PTT";
      btn.title = `Kanal ${slot} ist belegt – Tastendruck sendet Vorrang-Durchsage (Preemption)!`;
    } else {
      btn.classList.remove("btn-busy", "btn-override");
      btnText.textContent = "PTT SPRECHEN";
      btn.title = "Gedrückt halten zum Sprechen (oder Leertaste halten)";
    }
  }
}

function playBusyBonkSound() {
  try {
    const ctx = pttAudioContext || new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") ctx.resume();

    // Akustisches DMR-Besetzt-Signal (zwei abfallende tiefe Töne)
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sawtooth";
    osc.frequency.setValueAtTime(420, ctx.currentTime);
    osc.frequency.setValueAtTime(310, ctx.currentTime + 0.08);

    gain.gain.setValueAtTime(0.18, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.005, ctx.currentTime + 0.22);

    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.24);
  } catch (e) {
    console.debug("[Web-PTT] Bonk-Sound:", e);
  }
}

async function startWebPtt() {
  if (isWebPttActive) return;

  const slot = (document.getElementById("ptt-slot-select")?.value || "TS1").toUpperCase();
  const callType = parseInt(document.getElementById("ptt-calltype-select")?.value) || 1;
  const targetId = (callType === 2) 
    ? 16777215 
    : (parseInt(document.getElementById("ptt-target-tg")?.value) || 1);
  const overrideCheckbox = document.getElementById("ptt-priority-override");
  const isOverride = overrideCheckbox ? overrideCheckbox.checked : false;

  // Busy Channel Lockout (BCL) Prüfung: Verhindert versehentliches Drüberfunken
  const slotInfo = STATE.channelBusy?.[slot] || { busy: false };
  if (slotInfo.busy && !isOverride && callType !== 2) {
    const alias = STATE.radios[slotInfo.radio_id]?.alias || (slotInfo.radio_id ? `ID ${slotInfo.radio_id}` : "Funkgerät");
    playBusyBonkSound();
    showToast(
      `⚠️ Kanal ${slot} belegt`,
      `Gespräch durch ${alias} aktiv. Haken bei 'Vorrang' setzen, um Vorrang-Durchsage zu erzwingen.`,
      "warning",
      4000
    );
    return;
  }

  try {
    const ok = await initPttAudio();
    if (!ok) {
      showToast("Mikrofon nicht bereit", "Bitte Mikrofonzugriff im Browser erlauben oder auf '🎤 Mic' klicken.", "warning", 4000);
      return;
    }

    if (pttAudioContext && pttAudioContext.state === "suspended") {
      await pttAudioContext.resume();
    }

    isWebPttActive = true;
    pttStartTimestamp = Date.now();
    playPttStartChirp();

    // UI sofort auf aktiv setzen
    const btn = document.getElementById("btn-web-ptt");
    const bar = document.getElementById("web-ptt-bar");
    const btnText = document.getElementById("ptt-btn-text");
    const timerEl = document.getElementById("ptt-tx-timer");

    if (btn) {
      btn.classList.remove("btn-busy", "btn-override");
      btn.classList.add("active");
    }
    if (bar) bar.classList.add("tx-active");
    if (btnText) {
      if (callType === 2) {
        btnText.textContent = "🚨 ALL-CALL...";
      } else if (slotInfo.busy && isOverride) {
        btnText.textContent = "⚡ VORRANG...";
      } else {
        btnText.textContent = "🔴 SENDET...";
      }
    }
    if (timerEl) {
      timerEl.classList.add("active");
      timerEl.textContent = "00:00";
    }

    clearInterval(pttTimerInterval);
    pttTimerInterval = setInterval(() => {
      const elapsedSec = Math.floor((Date.now() - pttStartTimestamp) / 1000);
      const m = String(Math.floor(elapsedSec / 60)).padStart(2, "0");
      const s = String(elapsedSec % 60).padStart(2, "0");
      if (timerEl) timerEl.textContent = `${m}:${s}`;
    }, 500);

    // Befehl senden
    const cmd = {
      action: "ptt_press",
      slot: slot,
      target_id: targetId,
      call_type: callType,
      priority_override: isOverride,
      sample_rate: pttAudioContext.sampleRate,
    };

    if (pttAudioWs && pttAudioWs.readyState === WebSocket.OPEN) {
      pttAudioWs.send(JSON.stringify(cmd));
    } else {
      fetch("/api/tx/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cmd),
      })
      .then(r => r.json())
      .then(res => {
        if (!res.success && res.reason === "channel_busy") {
          playBusyBonkSound();
          showToast("⚠️ Kanal belegt", res.message || "Kanal belegt.", "warning", 4000);
          stopWebPtt();
        }
      })
      .catch(e => console.error("TX Start API Fehler:", e));
    }

    // Audio-Wiedergabe stoppen falls gerade aktiv
    const audio = document.getElementById("global-audio-element");
    if (audio && !audio.paused) audio.pause();
  } catch (err) {
    isWebPttActive = false;
    console.error("startWebPtt Fehler:", err);
    showToast("PTT Fehler", String(err?.message || err), "warning");
    updateChannelBusyUI();
  }
}

function stopWebPtt() {
  if (!isWebPttActive) return;
  isWebPttActive = false;
  clearInterval(pttTimerInterval);
  playRogerBeep();

  const btn = document.getElementById("btn-web-ptt");
  const bar = document.getElementById("web-ptt-bar");
  const btnText = document.getElementById("ptt-btn-text");
  const timerEl = document.getElementById("ptt-tx-timer");

  if (btn) btn.classList.remove("active");
  if (bar) bar.classList.remove("tx-active");
  if (btnText) btnText.textContent = "PTT SPRECHEN";
  if (timerEl) {
    timerEl.classList.remove("active");
    timerEl.textContent = "00:00";
  }
  updatePttVuMeter(0);

  const cmd = { action: "ptt_release" };
  if (pttAudioWs && pttAudioWs.readyState === WebSocket.OPEN) {
    pttAudioWs.send(JSON.stringify(cmd));
  }
  // Redundante Absicherung per REST: Garantiert, dass der Repeater IMMER sofort abfällt
  fetch("/api/tx/stop", { method: "POST" }).catch(e => console.error("TX Stop API Fehler:", e));

  // Button und Badge an aktuellen Kanalbelegungsstatus anpassen
  updateChannelBusyUI();
}

function onTxStateChanged(msg) {
  const pttInd = document.getElementById("ptt-indicator-container");
  const pttText = document.getElementById("ptt-indicator-text");
  const pttBar = document.getElementById("web-ptt-bar");
  const btn = document.getElementById("btn-web-ptt");
  const btnText = document.getElementById("ptt-btn-text");
  const timerEl = document.getElementById("ptt-tx-timer");

  if (msg.is_transmitting) {
    if (pttInd) pttInd.style.display = "flex";
    if (pttText) {
      if (msg.call_type === 2 || msg.target_id === 16777215) {
        pttText.textContent = `🚨 ALL-CALL RUNDRUF (${msg.slot} an ALLE GERÄTE)`;
      } else if (msg.call_type === 0) {
        pttText.textContent = `🔴 EINZELRUF (${msg.slot} an ID ${msg.target_id})`;
      } else {
        pttText.textContent = `🔴 LEITSTELLE SENDET (${msg.slot} an TG ${msg.target_id})`;
      }
    }
    if (pttBar) pttBar.classList.add("tx-active");
    if (btnText && isWebPttActive) btnText.textContent = (msg.call_type === 2 || msg.target_id === 16777215) ? "🚨 ALL-CALL..." : "🔴 SENDET...";
    if (timerEl && msg.duration_s !== undefined) {
      const m = String(Math.floor(msg.duration_s / 60)).padStart(2, "0");
      const s = String(Math.floor(msg.duration_s % 60)).padStart(2, "0");
      timerEl.textContent = `${m}:${s}`;
      timerEl.classList.add("active");
    }
  } else {
    if (pttInd) pttInd.style.display = "none";
    if (pttBar && !isWebPttActive) pttBar.classList.remove("tx-active");
    if (btn && !isWebPttActive) btn.classList.remove("active");
    if (btnText && !isWebPttActive) btnText.textContent = "PTT SPRECHEN";
    if (timerEl && !isWebPttActive) {
      timerEl.classList.remove("active");
      timerEl.textContent = "00:00";
    }
  }
}

function setupWebPttListeners() {
  const btn = document.getElementById("btn-web-ptt");
  const micBtn = document.getElementById("btn-ptt-mic-perm");

  // Audio-WebSocket im Hintergrund vorbereitend verbinden
  connectPttAudioWs();

  if (micBtn) {
    micBtn.addEventListener("click", () => initPttAudio());
  }

  if (btn) {
    // Maus-Events (Desktop)
    btn.addEventListener("mousedown", (e) => {
      e.preventDefault();
      startWebPtt();
    });
    btn.addEventListener("mouseup", () => stopWebPtt());
    btn.addEventListener("mouseleave", () => {
      if (isWebPttActive) stopWebPtt();
    });

    // Touch-Events (Smartphone & Tablets)
    btn.addEventListener("touchstart", (e) => {
      e.preventDefault();
      startWebPtt();
    }, { passive: false });

    btn.addEventListener("touchend", (e) => {
      e.preventDefault();
      stopWebPtt();
    }, { passive: false });

    btn.addEventListener("touchcancel", (e) => {
      e.preventDefault();
      stopWebPtt();
    }, { passive: false });
  }

  // Globaler Not-Stopp bei Maus-Release irgendwo auf dem Bildschirm
  window.addEventListener("mouseup", () => {
    if (isWebPttActive) {
      stopWebPtt();
    }
  });

  // Globaler Not-Stopp bei Fensterwechsel / Tab-Verlassen
  window.addEventListener("blur", () => {
    if (isWebPttActive) {
      console.log("[Web-PTT] Fenster hat Fokus verloren -> Not-Stopp");
      stopWebPtt();
    }
  });

  // Tastatur Hotkey: Leertaste halten
  window.addEventListener("keydown", (e) => {
    if ((e.code === "Space" || e.key === " " || e.key === "Spacebar") && !e.repeat) {
      const tag = document.activeElement ? document.activeElement.tagName.toLowerCase() : "";
      if (tag !== "input" && tag !== "textarea" && tag !== "select" && !document.activeElement?.isContentEditable) {
        e.preventDefault();
        startWebPtt();
      }
    }
  });

  window.addEventListener("keyup", (e) => {
    if (e.code === "Space" || e.key === " " || e.key === "Spacebar") {
      if (isWebPttActive) {
        e.preventDefault();
        stopWebPtt();
      }
    }
  });
}

// ═══════════════════════════════════════════════════════════════════
// ERGONOMIE & ANZEIGE-MODI (Outdoor Sonnenlicht / Taktisches Rotlicht)
// ═══════════════════════════════════════════════════════════════════

function setAppTheme(theme) {
  document.body.classList.remove("theme-outdoor", "theme-redlight");
  if (theme === "outdoor") {
    document.body.classList.add("theme-outdoor");
  } else if (theme === "redlight") {
    document.body.classList.add("theme-redlight");
  }
  localStorage.setItem("hcc_theme", theme);
  document.querySelectorAll(".theme-btn").forEach(btn => {
    btn.classList.toggle("active", btn.id === `theme-btn-${theme}`);
  });
  if (STATE.map) {
    setTimeout(() => STATE.map.invalidateSize(), 100);
  }
}

function initTheme() {
  const saved = localStorage.getItem("hcc_theme") || "dark";
  setAppTheme(saved);
}

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(err => {
      console.warn("Fullscreen nicht möglich:", err);
    });
  } else {
    if (document.exitFullscreen) {
      document.exitFullscreen();
    }
  }
}

// ═══════════════════════════════════════════════════════════════════
// AKUSTISCHE ROGER-BEEPS & PTT-CHIRPS (Web Audio API Synthesizer)
// ═══════════════════════════════════════════════════════════════════

function playPttStartChirp() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = "sine";
    const t = ctx.currentTime;
    osc.frequency.setValueAtTime(880, t);
    osc.frequency.exponentialRampToValueAtTime(1175, t + 0.07);
    gain.gain.setValueAtTime(0.2, t);
    gain.gain.linearRampToValueAtTime(0, t + 0.08);
    osc.start(t);
    osc.stop(t + 0.08);
  } catch(e) {}
}

function playRogerBeep() {
  const enabled = document.getElementById("ptt-roger-beep")?.checked !== false;
  if (!enabled) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = "sine";
    const t = ctx.currentTime;
    osc.frequency.setValueAtTime(1209, t);
    osc.frequency.setValueAtTime(880, t + 0.08);
    gain.gain.setValueAtTime(0.22, t);
    gain.gain.setValueAtTime(0.22, t + 0.08);
    gain.gain.linearRampToValueAtTime(0, t + 0.16);
    osc.start(t);
    osc.stop(t + 0.16);
  } catch(e) {}
}

// ═══════════════════════════════════════════════════════════════════
// SMARTPHONE-KOPPLUNG & QR-CODE
// ═══════════════════════════════════════════════════════════════════

async function openQrModal() {
  const modal = document.getElementById("modal-qr-connect");
  if (!modal) return;
  modal.style.display = "flex";

  try {
    const res = await fetch("/api/system/network_interfaces");
    const data = await res.json();
    const select = document.getElementById("qr-interface-select");
    if (select && data.interfaces) {
      select.innerHTML = "";
      data.interfaces.forEach(iface => {
        const opt = document.createElement("option");
        opt.value = iface.ip;
        opt.textContent = `${iface.name}: ${iface.ip}`;
        if (iface.ip === data.recommended_ip) opt.selected = true;
        select.appendChild(opt);
      });
    }
  } catch(e) {
    console.warn("Fehler beim Laden der Interfaces:", e);
  }

  reloadQrCode();
}

function closeQrModal() {
  const modal = document.getElementById("modal-qr-connect");
  if (modal) modal.style.display = "none";
}

async function reloadQrCode() {
  const select = document.getElementById("qr-interface-select");
  const ip = select?.value || "";
  const container = document.getElementById("qr-code-container");
  const directUrlInput = document.getElementById("qr-direct-url");

  const port = window.location.port ? `:${window.location.port}` : "";
  const targetIp = ip || window.location.hostname;
  const fullUrl = `${window.location.protocol}//${targetIp}${port}/`;

  if (directUrlInput) directUrlInput.value = fullUrl;

  try {
    const res = await fetch(`/api/system/qr_connect?ip=${encodeURIComponent(ip)}`);
    const svgText = await res.text();
    if (container) container.innerHTML = svgText;
  } catch(e) {
    if (container) container.innerHTML = `<span style="color:var(--red);">QR-Code konnte nicht geladen werden</span>`;
  }
}

function copyDirectUrl() {
  const input = document.getElementById("qr-direct-url");
  if (input) {
    navigator.clipboard.writeText(input.value).then(() => {
      showToast("✓ In Zwischenablage kopiert", input.value, "info");
    }).catch(() => {
      input.select();
      document.execCommand("copy");
      showToast("✓ Kopiert", input.value, "info");
    });
  }
}

// ═══════════════════════════════════════════════════════════════════
// BROWSER-GPS ("MEIN STANDORT") AUF LAGEKARTE
// ═══════════════════════════════════════════════════════════════════

let myLocationMarker = null;
let myLocationCircle = null;

function centerOnMyLocation() {
  if (!navigator.geolocation) {
    showToast("GPS nicht verfügbar", "Ihr Browser unterstützt keine Standortermittlung.", "warning");
    return;
  }

  showToast("📍 Standortsuche", "Ermittle aktuellen GPS-Standort...", "info");

  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;
      const accuracy = pos.coords.accuracy;

      if (!STATE.map) {
        switchTab("map");
      }
      if (!STATE.map) return;

      if (myLocationMarker) STATE.map.removeLayer(myLocationMarker);
      if (myLocationCircle) STATE.map.removeLayer(myLocationCircle);

      const icon = L.divIcon({
        className: "my-location-marker",
        html: `<div style="background:#0284c7;border:3px solid #ffffff;border-radius:50%;width:18px;height:18px;box-shadow:0 0 12px #0284c7;"></div>`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      });

      myLocationMarker = L.marker([lat, lon], { icon }).addTo(STATE.map);
      myLocationMarker.bindPopup(`<b>📍 Mein Standort (Command Center)</b><br>Genauigkeit: ±${Math.round(accuracy)}m`);

      myLocationCircle = L.circle([lat, lon], {
        radius: accuracy,
        color: "#0284c7",
        fillColor: "#0284c7",
        fillOpacity: 0.15,
        weight: 1
      }).addTo(STATE.map);

      STATE.map.setView([lat, lon], 16);
      showToast("✓ Standort gefunden", `Genauigkeit: ±${Math.round(accuracy)}m`, "good");
    },
    (err) => {
      console.warn("GPS-Fehler:", err);
      showToast("GPS-Fehler", err.message || "Standort konnte nicht ermittelt werden.", "warning");
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 }
  );
}

// ═══════════════════════════════════════════════════════════════════
// OFFLINE-KARTEN TILE PRE-CACHING
// ═══════════════════════════════════════════════════════════════════

function openTileCacheModal() {
  const modal = document.getElementById("modal-tile-cache");
  if (!modal) return;
  modal.style.display = "flex";
  updateTileCacheStatus();
}

function closeTileCacheModal() {
  const modal = document.getElementById("modal-tile-cache");
  if (modal) modal.style.display = "none";
}

async function updateTileCacheStatus() {
  try {
    const res = await fetch("/api/map/cache_status");
    const data = await res.json();
    const hint = document.getElementById("tile-cache-size-hint");
    if (hint) {
      hint.textContent = `Bereits im Cache: ${data.total_tiles_cached} Kacheln (${data.total_size_mb} MB)`;
    }
  } catch(e) {
    console.warn("Fehler beim Abruf des Cache-Status:", e);
  }
}

async function startTileDownload() {
  if (!STATE.map) {
    showToast("Karte nicht initialisiert", "Bitte zuerst Lagekarte aufrufen", "warning");
    return;
  }

  const center = STATE.map.getCenter();
  const radiusKm = parseFloat(document.getElementById("tile-cache-radius")?.value || "5");
  const maxZoom = parseInt(document.getElementById("tile-cache-zoom")?.value || "17");

  const progressBox = document.getElementById("tile-cache-progress-box");
  const statusLabel = document.getElementById("tile-cache-status-label");
  const downloadBtn = document.getElementById("btn-do-cache-tiles");

  if (progressBox) progressBox.style.display = "block";
  if (downloadBtn) downloadBtn.disabled = true;
  if (statusLabel) statusLabel.textContent = `Berechne Kacheln im Umkreis von ${radiusKm} km um [${center.lat.toFixed(4)}, ${center.lng.toFixed(4)}]...`;

  try {
    const payload = {
      lat: center.lat,
      lon: center.lng,
      radius_km: radiusKm,
      min_zoom: 12,
      max_zoom: maxZoom
    };
    const res = await fetch("/api/map/cache_area", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const result = await res.json();

    if (result.status === "ok") {
      if (statusLabel) statusLabel.textContent = `✓ ${result.downloaded} Kacheln heruntergeladen (${result.already_cached} waren bereits vorhanden).`;
      showToast("✓ Offline-Karten bereit", `${result.total_needed} Kacheln lokal gesichert`, "good");
      updateTileCacheStatus();
    } else {
      if (statusLabel) statusLabel.textContent = `⚠ Fehler: ${result.detail || "Unbekannt"}`;
      showToast("Cache-Fehler", result.detail || "Konnte Kacheln nicht laden", "warning");
    }
  } catch(e) {
    if (statusLabel) statusLabel.textContent = `⚠ Fehler beim Herunterladen: ${e.message}`;
  } finally {
    if (downloadBtn) downloadBtn.disabled = false;
  }
}

// ═══════════════════════════════════════════════════════════════════
// SUBMETZ-AUTODISCOVERY SCANNER
// ═══════════════════════════════════════════════════════════════════

function openSubnetScanModal() {
  const modal = document.getElementById("modal-subnet-scan");
  if (!modal) return;
  modal.style.display = "flex";
  const rptIp = document.getElementById("cfg-repeater-ip")?.value || "192.168.0.230";
  const parts = rptIp.split(".");
  if (parts.length >= 3) {
    const subnetInput = document.getElementById("scan-subnet-input");
    if (subnetInput) subnetInput.value = `${parts[0]}.${parts[1]}.${parts[2]}`;
  }
}

function closeSubnetScanModal() {
  const modal = document.getElementById("modal-subnet-scan");
  if (modal) modal.style.display = "none";
}

async function startSubnetScan() {
  const subnetInput = document.getElementById("scan-subnet-input");
  const statusText = document.getElementById("scan-status-text");
  const resultsList = document.getElementById("scan-results-list");
  const scanBtn = document.getElementById("btn-start-subnet-scan");

  const subnet = subnetInput?.value?.trim() || "";
  if (!subnet) {
    showToast("Ungültiges Subnetz", "Bitte z.B. 192.168.0 eingeben", "warning");
    return;
  }

  if (scanBtn) scanBtn.disabled = true;
  if (statusText) statusText.textContent = `Scanne ${subnet}.1 bis ${subnet}.254 (Port 161, 502, 80)...`;
  if (resultsList) resultsList.innerHTML = `<div style="text-align:center;color:var(--text-muted);padding:20px;">Scanne Netzwerk... Bitte warten...</div>`;

  try {
    const res = await fetch("/api/hardware/scan_subnet", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subnet_prefix: subnet })
    });
    const data = await res.json();

    if (scanBtn) scanBtn.disabled = false;
    if (statusText) statusText.textContent = `Scan beendet: ${data.count} Geräte in ${data.scan_time_seconds}s gefunden`;

    if (!data.results || data.results.length === 0) {
      if (resultsList) resultsList.innerHTML = `<div style="text-align:center;color:var(--text-muted);padding:24px;">Keine Hytera-, USV- oder Gateway-Geräte im Subnetz ${subnet}.0/24 gefunden.</div>`;
      return;
    }

    let html = "";
    data.results.forEach(dev => {
      const typeBadge = dev.type === "repeater"
        ? `<span class="chip chip-online">Repeater (Port 161)</span>`
        : dev.type === "ups"
        ? `<span class="chip chip-warning">USV (Port 502)</span>`
        : `<span class="chip" style="background:var(--accent-glow);color:var(--accent);">Gateway (Web)</span>`;

      html += `
        <div class="scan-result-row">
          <div>
            <span class="scan-result-ip">${dev.ip}</span>
            <div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">${dev.description}</div>
          </div>
          <div style="display:flex;align-items:center;gap:6px;">
            ${typeBadge}
            ${dev.type === "repeater" ? `<button class="btn btn-xs btn-primary" onclick="setRepeaterIp('${dev.ip}')">Als Repeater</button>` : ''}
            ${dev.type === "ups" ? `<button class="btn btn-xs btn-warning" onclick="setUsvIp('${dev.ip}')">Als USV</button>` : ''}
            ${dev.type === "gateway" ? `<button class="btn btn-xs btn-secondary" onclick="setGatewayIp('${dev.ip}')">Als Router</button>` : ''}
          </div>
        </div>
      `;
    });
    if (resultsList) resultsList.innerHTML = html;
  } catch(e) {
    if (scanBtn) scanBtn.disabled = false;
    if (statusText) statusText.textContent = `Fehler: ${e.message}`;
  }
}

function setRepeaterIp(ip) {
  const el = document.getElementById("cfg-repeater-ip");
  if (el) el.value = ip;
  showToast("✓ Repeater-IP gesetzt", ip, "good");
  closeSubnetScanModal();
}

function setUsvIp(ip) {
  const el = document.getElementById("cfg-usv-ip");
  if (el) el.value = ip;
  showToast("✓ USV-IP gesetzt", ip, "good");
  closeSubnetScanModal();
}

function setGatewayIp(ip) {
  const zte = document.getElementById("cfg-zte-ip");
  const omada = document.getElementById("cfg-omada-ip");
  if (zte) zte.value = ip;
  if (omada) omada.value = ip;
  showToast("✓ Gateway-IP gesetzt", ip, "good");
  closeSubnetScanModal();
}

// ═══════════════════════════════════════════════════════════════════
// STANDORT- UND NETZWERK-PROFILE
// ═══════════════════════════════════════════════════════════════════

async function loadProfilesList() {
  try {
    const res = await fetch("/api/system/profiles");
    const data = await res.json();
    const select = document.getElementById("cfg-profile-select");
    const chip = document.getElementById("current-profile-chip");
    if (select && data.profiles) {
      select.innerHTML = "";
      Object.entries(data.profiles).forEach(([id, prof]) => {
        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = `${prof.name} (${prof.description || ''})`;
        if (id === data.active_profile) opt.selected = true;
        select.appendChild(opt);
      });
    }
    if (chip && data.active_profile && data.profiles[data.active_profile]) {
      chip.textContent = data.profiles[data.active_profile].name;
    }
  } catch(e) {
    console.warn("Fehler beim Laden der Profile:", e);
  }
}

async function applySelectedProfile() {
  const select = document.getElementById("cfg-profile-select");
  const profileId = select?.value;
  if (!profileId) return;

  try {
    const res = await fetch("/api/system/profiles/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile_id: profileId })
    });
    const result = await res.json();
    if (result.status === "ok") {
      showToast("✓ Profil angewendet", `Profil "${result.applied_profile}" ist jetzt aktiv`, "good");
      loadHardwareStatus();
      loadSettings();
      loadProfilesList();
    } else {
      showToast("Profil-Fehler", result.detail || "Profil konnte nicht angewendet werden", "warning");
    }
  } catch(e) {
    showToast("Fehler", e.message, "warning");
  }
}

async function saveCurrentAsProfile() {
  const name = prompt("Name für das neue Standort-Profil (z.B. 'Einsatzort Waldbrand'):");
  if (!name) return;
  const id = name.toLowerCase().replace(/[^a-z0-9_-]/g, "_");

  const rptIp = document.getElementById("cfg-repeater-ip")?.value || "192.168.0.230";
  const usvIp = document.getElementById("cfg-usv-ip")?.value || "192.168.0.232";
  const zteIp = document.getElementById("cfg-zte-ip")?.value || "192.168.0.1";
  const omadaIp = document.getElementById("cfg-omada-ip")?.value || "192.168.0.1";

  const payload = {
    id: id,
    name: name,
    description: `Standort-Profil ${name}`,
    settings: {
      repeater_ip: rptIp,
      usv_ip: usvIp,
      zte_ip: zteIp,
      omada_ip: omadaIp
    }
  };

  try {
    const res = await fetch("/api/system/profiles/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.status === "ok") {
      showToast("✓ Profil gespeichert", `Profil "${name}" erfolgreich gesichert`, "good");
      loadProfilesList();
    }
  } catch(e) {
    showToast("Fehler", e.message, "warning");
  }
}

// ═══════════════════════════════════════════════════════════════════
// AUDIO-EINGABEGERÄTE FÜR WEB-PTT
// ═══════════════════════════════════════════════════════════════════

async function populateAudioInputDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const audioInputs = devices.filter(d => d.kind === "audioinput");
    const select = document.getElementById("ptt-audio-device-select");
    if (select && audioInputs.length > 0) {
      select.innerHTML = "";
      audioInputs.forEach((dev, idx) => {
        const opt = document.createElement("option");
        opt.value = dev.deviceId;
        opt.textContent = dev.label || `Mikrofon ${idx + 1}`;
        select.appendChild(opt);
      });
      select.onchange = () => {
        initPttAudio(select.value);
      };
    }
  } catch(e) {
    console.warn("Konnte Audiogeräte nicht auflisten:", e);
  }
}

// ═══════════════════════════════════════════════════════════════════
// SICHERHEITSSYSTEM: FUNKGERÄTE-SPERRE, BLACKLIST & ORTUNG
// ═══════════════════════════════════════════════════════════════════

function playSecurityAlarmSound() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    // Eindringlicher Zwei-Ton-Sirenen-Warble für Rogue-Radio Erkennung
    const freqs = [950, 650, 950, 650, 1100, 700];
    let t = ctx.currentTime;
    freqs.forEach((freq) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = freq;
      osc.type = "sawtooth";
      gain.gain.setValueAtTime(0, t);
      gain.gain.linearRampToValueAtTime(0.25, t + 0.02);
      gain.gain.linearRampToValueAtTime(0, t + 0.16);
      osc.start(t);
      osc.stop(t + 0.18);
      t += 0.20;
    });
  } catch(e) {
    console.warn("[Security] Sirenen-Audio fehlgeschlagen:", e);
  }
}

function onSecurityAlert(msg) {
  playSecurityAlarmSound();

  STATE.lastBlockedAlertRadio = msg.radio_id;

  const banner = document.getElementById("security-alert-banner");
  const title = document.getElementById("sec-alert-title");
  const meta = document.getElementById("sec-alert-meta");

  if (banner) {
    banner.style.display = "flex";
  }
  if (title) {
    title.textContent = `🚨 SICHERHEITSALARM: Gesperrtes Funkgerät aktiv! [${msg.alias || 'Radio ' + msg.radio_id}]`;
  }
  if (meta) {
    const rssiStr = msg.rssi != null ? `${msg.rssi.toFixed(0)} dBm` : "—";
    const reasonStr = msg.blocked_reason ? `Grund: ${msg.blocked_reason}` : "Blacklist";
    const eventStr = msg.event_type || "Sendeaktivität";
    meta.textContent = `ID: ${msg.radio_id} | RSSI: ${rssiStr} | ${reasonStr} | Signal: ${eventStr}`;
  }

  // Falls GPS-Daten mitgeliefert wurden, sofort auf der Karte aktualisieren
  if (msg.lat != null && msg.lon != null) {
    STATE.gpsPositions[msg.radio_id] = {
      radio_id: msg.radio_id,
      lat: msg.lat,
      lon: msg.lon,
      rssi: msg.rssi,
      timestamp: new Date().toISOString()
    };
    if (typeof updateRadioMarker === "function") {
      updateRadioMarker(msg.radio_id, msg.lat, msg.lon, msg.rssi);
    }
  }

  showToast(
    `🚨 SICHERHEITSALARM: ${msg.alias || 'Radio ' + msg.radio_id}`,
    `Gesperrtes Funkgerät sendet (${msg.event_type})! RSSI: ${msg.rssi != null ? msg.rssi + ' dBm' : 'k.A.'}`,
    "emergency",
    12000
  );
}

function onRadioBlockUpdate(msg) {
  if (STATE.radios[msg.radio_id]) {
    STATE.radios[msg.radio_id].is_blocked = msg.is_blocked ? 1 : 0;
    STATE.radios[msg.radio_id].blocked_reason = msg.blocked_reason || null;
    STATE.radios[msg.radio_id].blocked_at = msg.blocked_at || null;
  }
  renderRadiosTable();
  renderSidebarUnits();
}

function updateRepeaterKnockdownButton(isKnockdown) {
  const btn = document.getElementById("btn-repeater-knockdown");
  if (!btn) return;
  if (isKnockdown) {
    btn.textContent = "▶ Freigeben";
    btn.style.background = "#238636";
    btn.title = "Stummschaltung aufheben und Relaisbetrieb wieder freigeben";
  } else {
    btn.textContent = "⛔ Stummschalten";
    btn.style.background = "#dc2626";
    btn.title = "Repeater Sende-Relais stummschalten (Knockdown aktiv)";
  }
}

async function toggleRepeaterKnockdownManual() {
  const isKnockdown = STATE.repeaterKnockdown || false;
  const target = !isKnockdown;
  const promptText = target
    ? "Möchten Sie den Relais-Sender stummschalten (Knockdown aktiv)? Funkübertragungen über das Relais werden unterdrückt."
    : "Möchten Sie die Stummschaltung aufheben und den normalen Repeater-Betrieb wieder freigeben?";
  if (!confirm(promptText)) return;
  try {
    const res = await fetch("/api/hardware/repeater/knockdown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ knockdown: target })
    });
    const data = await res.json();
    if (res.ok) {
      STATE.repeaterKnockdown = data.knockdown;
      updateRepeaterKnockdownButton(data.knockdown);
      showToast(target ? "⛔ Repeater stummgeschaltet" : "✓ Repeater aktiv", data.message, target ? "warning" : "good");
    } else {
      showToast("Fehler", data.detail || "Konnte Knockdown-Status nicht ändern", "warning");
    }
  } catch (e) {
    showToast("Netzwerkfehler", String(e), "warning");
  }
}

function onRepeaterKnockdownChanged(msg) {
  STATE.repeaterKnockdown = Boolean(msg.knockdown);
  updateRepeaterKnockdownButton(msg.knockdown);
  if (msg.knockdown) {
    showToast("⛔ REPEATER STUMMGESCHALTET", "Knockdown-Modus aktiv: Sender unterdrückt HF-Aussendungen.", "emergency", 8000);
  } else {
    showToast("✓ REPEATER AKTIV", "Knockdown aufgehoben: Normaler Relaisfunkbetrieb wiederhergestellt.", "good", 5000);
  }
}

function dismissSecurityAlert() {
  const banner = document.getElementById("security-alert-banner");
  if (banner) banner.style.display = "none";
}

function locateBlockedRadio() {
  if (!STATE.lastBlockedAlertRadio) {
    showToast("Ortung", "Keine kürzlich alarmierte Radio-ID bekannt.", "info");
    return;
  }
  focusOnMap(STATE.lastBlockedAlertRadio);
}

async function quickKnockdownFromAlert() {
  if (!confirm("Möchten Sie den Relais-Sender SOFORT stummschalten (Knockdown aktiv)? Dadurch werden alle Aussendungen unterbunden.")) {
    return;
  }
  try {
    const res = await fetch("/api/hardware/repeater/knockdown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ knockdown: true })
    });
    const data = await res.json();
    if (res.ok) {
      showToast("⛔ Repeater stummgeschaltet", "Repeating-Knockdown erfolgreich ausgelöst.", "warning", 6000);
    } else {
      showToast("Fehler bei Knockdown", data.detail || "Konnte Relais nicht stummschalten", "warning");
    }
  } catch(e) {
    showToast("Netzwerkfehler", String(e), "warning");
  }
}

function openRadioBlockModal(radioId) {
  STATE.modalTargetRadioId = radioId;
  const radio = STATE.radios[radioId];
  const modal = document.getElementById("modal-radio-block");
  const info = document.getElementById("modal-radio-block-info");
  const warning = document.getElementById("block-model-warning");
  const stunBtn = document.getElementById("btn-ota-stun");
  const select = document.getElementById("radio-block-reason-select");
  const customInput = document.getElementById("radio-block-reason-custom");

  if (!modal) return;

  if (info) {
    info.innerHTML = `<strong>Gerät:</strong> ${radio?.alias || 'Radio ' + radioId} (ID: ${radioId}) | Modell: ${radio?.device_model || 'Unbekannt'}`;
  }

  const isRt81 = (radio?.device_model || "").toLowerCase().includes("rt81");
  if (warning) {
    warning.style.display = isRt81 ? "block" : "none";
  }
  if (stunBtn) {
    stunBtn.style.display = isRt81 ? "none" : "inline-flex";
  }

  if (select) select.value = "Verlust an Einsatzstelle";
  if (customInput) {
    customInput.value = "";
    customInput.style.display = "none";
  }

  modal.style.display = "flex";
}

function closeRadioBlockModal() {
  const modal = document.getElementById("modal-radio-block");
  if (modal) modal.style.display = "none";
  STATE.modalTargetRadioId = null;
}

function onRadioBlockReasonChange() {
  const select = document.getElementById("radio-block-reason-select");
  const customInput = document.getElementById("radio-block-reason-custom");
  if (!select || !customInput) return;
  customInput.style.display = select.value === "custom" ? "block" : "none";
}

async function submitRadioBlock() {
  const radioId = STATE.modalTargetRadioId;
  if (!radioId) return;

  const select = document.getElementById("radio-block-reason-select");
  const customInput = document.getElementById("radio-block-reason-custom");
  let reason = select ? select.value : "Gesperrt";
  if (reason === "custom" && customInput && customInput.value.trim()) {
    reason = customInput.value.trim();
  }

  try {
    const res = await fetch(`/api/radios/${radioId}/block`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_blocked: true, reason: reason })
    });
    const data = await res.json();
    if (res.ok) {
      if (STATE.radios[radioId]) {
        STATE.radios[radioId].is_blocked = 1;
        STATE.radios[radioId].blocked_reason = reason;
        STATE.radios[radioId].blocked_at = new Date().toISOString();
      }
      renderRadiosTable();
      renderSidebarUnits();
      closeRadioBlockModal();
      showToast("🚫 Funkgerät gesperrt", `${data.alias || 'Radio ' + radioId} zur Blacklist hinzugefügt.`, "warning", 5000);
    } else {
      showToast("Fehler beim Sperren", data.detail || "Konnte Funkgerät nicht sperren", "warning");
    }
  } catch(e) {
    showToast("Netzwerkfehler", String(e), "warning");
  }
}

async function unblockRadio(radioId) {
  if (!confirm(`Sperre für Funkgerät ${STATE.radios[radioId]?.alias || radioId} wirklich aufheben?`)) {
    return;
  }
  try {
    const res = await fetch(`/api/radios/${radioId}/block`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_blocked: false })
    });
    const data = await res.json();
    if (res.ok) {
      if (STATE.radios[radioId]) {
        STATE.radios[radioId].is_blocked = 0;
        STATE.radios[radioId].blocked_reason = null;
        STATE.radios[radioId].blocked_at = null;
      }
      renderRadiosTable();
      renderSidebarUnits();
      showToast("🔓 Funkgerät freigegeben", `${data.alias || 'Radio ' + radioId} wurde entsperrt.`, "good", 5000);
    } else {
      showToast("Fehler beim Entsperren", data.detail || "Konnte Funkgerät nicht entsperren", "warning");
    }
  } catch(e) {
    showToast("Netzwerkfehler", String(e), "warning");
  }
}

async function triggerOtaStunFromModal() {
  const radioId = STATE.modalTargetRadioId;
  if (!radioId) return;

  const radio = STATE.radios[radioId];
  if (!confirm(`DMR CSBK Radio Disable Frame per Funk an ${radio?.alias || 'Radio ' + radioId} senden?`)) {
    return;
  }

  try {
    const res = await fetch(`/api/radios/${radioId}/ota_stun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });
    const data = await res.json();
    if (res.ok) {
      if (data.status === "warning") {
        showToast("⚠️ Hinweis", data.message, "warning", 7000);
      } else {
        showToast("⚡ OTA-Stun gesendet", `DMR CSBK Radio Disable Frame an ID ${radioId} übertragen.`, "good", 6000);
      }
    } else {
      showToast("OTA-Stun Fehler", data.detail || "Fehler beim Senden des Stun-Befehls", "warning");
    }
  } catch(e) {
    showToast("Netzwerkfehler", String(e), "warning");
  }
}

// ═══════════════════════════════════════════════════════════════════
// FESTE POSITIONEN FÜR FUNKGERÄTE (Z.B. RETEVIS RT81 / FESTSTATIONEN)
// ═══════════════════════════════════════════════════════════════════

function startSetRadioPosition(radioId) {
  STATE.settingRadioPositionId = radioId;
  const radio = STATE.radios[radioId];
  switchTab("map");
  const mapEl = document.getElementById("map");
  if (mapEl) mapEl.style.cursor = "crosshair";
  showToast(
    "📍 Feste Position festlegen",
    `Klicke auf die Karte, um den Standort für ${radio?.alias || 'Radio ' + radioId} zu platzieren (oder ESC zum Abbrechen).`,
    "info",
    8000
  );
}

async function saveRadioFixedPosition(radioId, lat, lon) {
  try {
    const res = await fetch(`/api/radios/${radioId}/position`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat, lon })
    });
    const data = await res.json();
    if (res.ok) {
      if (STATE.radios[radioId]) {
        STATE.radios[radioId].fixed_lat = lat;
        STATE.radios[radioId].fixed_lon = lon;
        STATE.radios[radioId].last_lat = lat;
        STATE.radios[radioId].last_lon = lon;
      }
      STATE.gpsPositions[radioId] = {
        radio_id: radioId,
        lat: lat,
        lon: lon,
        rssi: STATE.radios[radioId]?.last_rssi,
        is_fixed: true,
        timestamp: new Date().toISOString()
      };
      updateMapMarker(radioId, STATE.gpsPositions[radioId]);
      renderRadiosTable();
      showToast(
        "✓ Feste Position gespeichert",
        `${STATE.radios[radioId]?.alias || 'Radio ' + radioId} auf Karte fixiert (${lat.toFixed(5)}, ${lon.toFixed(5)})`,
        "good"
      );
      if (STATE.mapMarkers[radioId]) {
        STATE.mapMarkers[radioId].openPopup();
      }
    } else {
      showToast("Fehler", data.detail || "Position konnte nicht gespeichert werden", "warning");
    }
  } catch(e) {
    showToast("Netzwerkfehler", String(e), "warning");
  }
}

async function clearRadioFixedPosition(radioId) {
  if (!confirm(`Feste Position für ${STATE.radios[radioId]?.alias || 'Radio ' + radioId} wirklich entfernen?`)) {
    return;
  }
  try {
    const res = await fetch(`/api/radios/${radioId}/position`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: null, lon: null })
    });
    if (res.ok) {
      if (STATE.radios[radioId]) {
        STATE.radios[radioId].fixed_lat = null;
        STATE.radios[radioId].fixed_lon = null;
      }
      if (STATE.gpsPositions[radioId]?.is_fixed) {
        delete STATE.gpsPositions[radioId];
        if (STATE.mapMarkers[radioId]) {
          STATE.map?.removeLayer(STATE.mapMarkers[radioId]);
          delete STATE.mapMarkers[radioId];
        }
      }
      renderRadiosTable();
      showToast("Position entfernt", `${STATE.radios[radioId]?.alias || 'Radio ' + radioId} hat keine feste Position mehr`, "info");
    }
  } catch(e) {
    showToast("Netzwerkfehler", String(e), "warning");
  }
}





