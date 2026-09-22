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
  mapGeofences:  [],   // Leaflet layers
  tileLayer:     null,
  unreadSMS:     0,
  emergencyCount: 0,
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

    case "geofence_added":
      STATE.geofences.push(msg.geofence);
      drawGeofenceOnMap(msg.geofence);
      renderGeofencesList();
      break;

    case "geofence_deleted":
      STATE.geofences = STATE.geofences.filter(g => g.id !== msg.id);
      renderGeofencesList();
      break;

    case "system_status":
      updatePortStats(msg.port_stats || []);
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

  // Calls
  STATE.calls = msg.calls || [];

  // SMS
  STATE.sms = msg.sms || [];

  // Marker
  (msg.markers || []).forEach(m => {
    STATE.markers[m.id] = m;
  });

  // Settings
  STATE.settings = msg.settings || {};
  applySettings(STATE.settings);

  // Mission
  applyMissionData(msg.mission || {});

  // SNMP-Events
  STATE.snmpEvents = msg.snmp_events || [];

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
  const hasAlarm  = (s.alarm_count || 0) > 0;

  setBadge("badge-repeater", hasAlarm ? "alarm" : (isOnline ? "online" : "offline"));
  setChip("dash-repeater-chip", isOnline, hasAlarm);
  setChip("infra-repeater-chip", isOnline, hasAlarm);

  // Dashboard Quick-View
  setText("dash-rpt-temp",  s.temp_wert  != null ? `${s.temp_wert.toFixed(1)} \u00b0C`  : "\u2014");
  setText("dash-rpt-volt",  s.volt_wert  != null ? `${s.volt_wert.toFixed(1)} V`   : "\u2014");
  setText("dash-rpt-txrx",
    s.work_state != null ? (s.work_state === 1 ? "TX (Senden)" : "RX (Empfang)") : "\u2014"
  );

  // Infrastruktur-Tab
  setText("rpt-temp",   s.temp_wert   != null ? `${s.temp_wert.toFixed(1)} \u00b0C`   : "\u2014");
  setText("rpt-volt",   s.volt_wert   != null ? `${s.volt_wert.toFixed(1)} V`    : "\u2014");
  setText("rpt-txpwr",  s.fw_pwr_watt != null ? `${s.fw_pwr_watt.toFixed(1)} W`  : "\u2014");
  setText("rpt-vswr",   s.vswr_wert   != null ? s.vswr_wert.toFixed(2)           : "\u2014");
  setText("rpt-txfreq", s.tx_freq_mhz != null ? `${s.tx_freq_mhz} MHz`           : "\u2014");
  setText("rpt-rxfreq", s.rx_freq_mhz != null ? `${s.rx_freq_mhz} MHz`           : "\u2014");
  setText("rpt-rssi1",  s.rssi_slot1  != null ? `${s.rssi_slot1} dB`             : "\u2014");
  setText("rpt-rssi2",  s.rssi_slot2  != null ? `${s.rssi_slot2} dB`             : "\u2014");
  // Trap-Counter
  setText("rpt-trap-count",  s.trap_count  != null ? String(s.trap_count)  : "0");
  setText("rpt-alarm-count", s.alarm_count != null ? String(s.alarm_count) : "0");

  // Alarm-Flags aus SNMP-OID-Alarmen
  const alarmsEl = document.getElementById("rpt-alarms");
  if (alarmsEl) {
    const active = s.active_alarms || [];
    if (active.length > 0) {
      alarmsEl.innerHTML = active.map(a =>
        `<div class="chip chip-emergency">&#9888; ${a.oid_label || a.oid_key}: ${a.value || ""}</div>`
      ).join("");
    } else if (isOnline) {
      alarmsEl.innerHTML = '<div class="chip chip-online">\u2713 Alle Systeme normal</div>';
    } else {
      alarmsEl.innerHTML = '<div class="chip chip-offline">Keine Verbindung</div>';
    }
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
    showToast("\u26a0 USV Batterietemperatur", `${d.batt_temp_c} \u00b0C \u2013 \u00fcberpriifen!`, "warning");
  }

  // Fault-Alarm
  if (d.fault_active && d.fault_code) {
    showToast("\u26a0 USV Fehler", `Fehlercode: ${d.fault_code}`, "emergency");
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
  setText("ups-out-volt",  d.output_volt     != null ? `${d.output_volt} V`      : "\u2014");
  setText("ups-out-freq",  d.output_freq     != null ? `${d.output_freq} Hz`     : "\u2014");
  setText("ups-out-curr",  d.output_curr     != null ? `${d.output_curr} A`      : "\u2014");
  setText("ups-out-power", d.output_power_w  != null ? `${d.output_power_w} W`  : "\u2014");

  // Batterie
  setText("ups-batt-volt", d.batt_volt_v    != null ? `${d.batt_volt_v} V`      : "\u2014");
  setText("ups-batt-curr", d.batt_curr_a    != null ? `${d.batt_curr_a} A`      : "\u2014");
  setText("ups-batt-temp", d.batt_temp_c    != null ? `${d.batt_temp_c} \u00b0C`     : "\u2014");

  // Intern
  setText("ups-temp",      d.internal_temp_c != null ? `${d.internal_temp_c} \u00b0C` : "\u2014");
  setText("ups-bypass",    d.bypass_active   === true ? "Aktiv" : "Inaktiv");
  setText("ups-fault-code",d.fault_code      != null ? String(d.fault_code) : "\u2014");

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

  // Karten-Klick → Marker setzen wenn Marker-Modus aktiv
  map.on("click", e => {
    if (STATE.addingMarker) {
      setMarkerMode(false);
      openMarkerModal(e.latlng.lat, e.latlng.lng);
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

  let statusBadge = `<span class="t-popup-badge prio-normal">Online</span>`;
  if (em) statusBadge = `<span class="t-popup-badge prio-critical">🚨 Notruf</span>`;
  else if (ptt) statusBadge = `<span class="t-popup-badge prio-high">🎙 PTT</span>`;
  else if (!radio.online && radio.online !== undefined) statusBadge = `<span class="t-popup-badge prio-low">Offline</span>`;

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
        <div class="t-popup-row"><span class="t-key">Position</span><span class="t-val">${coords}</span></div>
        <div class="t-popup-row"><span class="t-key">Geschw. / Kurs</span><span class="t-val">${speedStr} · ${headingStr}</span></div>
        <div class="t-popup-row"><span class="t-key">Signal (RSSI)</span><span class="t-val">${rssiStr}</span></div>
      </div>
      <div class="t-popup-actions">
        ${em ? `<button class="btn btn-sm btn-danger" onclick="ackEmergency(${rid})" style="flex:1;padding:3px 6px;font-size:11px;">✓ Quittieren</button>` : ""}
        <button class="btn btn-sm btn-secondary" onclick="focusOnMap(${rid})" style="flex:1;padding:3px 6px;font-size:11px;">⊙ Zentrieren</button>
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
  if (!STATE.map) return;
  let layer;
  if (gf.shape === "circle" && gf.center_lat != null) {
    const color = gf.zone_type === "danger" ? "#f85149" : "#2f81f7";
    layer = L.circle([gf.center_lat, gf.center_lon], {
      radius: gf.radius_m || 100,
      color, fillColor: color, fillOpacity: 0.08,
      weight: 2, dashArray: "6 4",
    }).addTo(STATE.map).bindPopup(`<b>⬡ ${gf.name}</b>`);
  }
  if (layer) STATE.mapGeofences.push(layer);
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
    else if (r.online) { avatarClass += " online"; }

    let itemClass = "unit-item";
    if (em)     itemClass += " emergency-active";
    else if (isPTT) itemClass += " ptt-active";

    return `
      <div class="${itemClass}" id="unit-item-${rid}" onclick="focusOnMap(${rid})">
        <div class="${avatarClass}">${avatarContent}</div>
        <div class="unit-info">
          <div class="unit-name">${r.alias || "Radio " + rid}</div>
          <div class="unit-sub">${r.device_model || (r.online ? "Online" : "Offline")}</div>
        </div>
        <span class="unit-rssi" id="unit-rssi-${rid}">${rssi != null ? rssi.toFixed(0) + "d" : ""}</span>
      </div>`;
  }).join("");
}

function renderRadiosTable() {
  const tbody = document.getElementById("radios-table-body");
  if (!tbody) return;

  const radios = Object.values(STATE.radios);
  if (radios.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:30px">
      Keine Funkgeräte konfiguriert. Klicke „+ Gerät".
    </td></tr>`;
    return;
  }

  tbody.innerHTML = radios.map(r => {
    const rid = r.radio_id;
    const em  = STATE.emergencies[rid];
    const gps = STATE.gpsPositions[rid];
    const lat = r.last_lat ?? gps?.lat;
    const lon = r.last_lon ?? gps?.lon;
    const gpsStr = (lat && lon) ? `${lat.toFixed(5)}, ${lon.toFixed(5)}` : "—";
    const rssi = r.last_rssi ?? gps?.rssi;
    const rssiStr = rssi != null ? `${rssi.toFixed(0)} dBm` : "—";

    const statusChip = em
      ? `<span class="chip chip-emergency">🚨 NOTRUF</span>`
      : (r.online
        ? `<span class="chip chip-online">● Online</span>`
        : `<span class="chip chip-offline">○ Offline</span>`);

    return `<tr>
      <td class="mono" style="font-size:11px">${rid}</td>
      <td style="font-weight:600">${r.alias || "—"}</td>
      <td style="color:var(--text-secondary);font-size:12px">${r.device_model || "—"}</td>
      <td>${statusChip}</td>
      <td style="font-size:11px;font-family:var(--font-mono);color:var(--text-secondary)">${gpsStr}</td>
      <td style="font-family:var(--font-mono);font-size:12px">${rssiStr}</td>
      <td style="font-size:12px">${r.floor_level || "—"}</td>
      <td>
        <div style="display:flex;gap:4px;">
          <button class="btn btn-sm btn-icon" onclick="focusOnMap(${rid})" title="Auf Karte">🗺</button>
          ${em ? `<button class="btn btn-sm btn-danger" onclick="ackEmergency(${rid})">✓ Quit.</button>` : ""}
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
  if (STATE.geofences.length === 0) {
    el.innerHTML = `<div class="empty-state" style="padding:20px"><span style="opacity:0.3">⬡</span><span style="font-size:11px">Keine Geofences</span></div>`;
    return;
  }
  el.innerHTML = STATE.geofences.map(g =>
    `<div class="infra-row" style="padding:8px var(--space-3);">
      <span>⬡ ${g.name}</span>
      <div style="display:flex;align-items:center;gap:6px;">
        <span class="chip ${g.zone_type === 'danger' ? 'chip-emergency' : 'chip-online'}" style="font-size:10px">${g.zone_type}</span>
        <button class="btn btn-sm btn-icon" onclick="deleteGeofence(${g.id})" style="color:var(--text-muted)">✕</button>
      </div>
    </div>`
  ).join("");
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

async function deleteGeofence(id) {
  try {
    await fetch(`/api/geofences/${id}`, { method: "DELETE" });
  } catch(e) { console.error("Geofence delete Fehler:", e); }
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

  // SMS senden
  document.getElementById("btn-send-sms")?.addEventListener("click", async () => {
    const targetId = parseInt(document.getElementById("sms-target-id").value) || 0;
    const text     = document.getElementById("sms-text-input").value.trim();
    if (!text) return;
    try {
      await fetch("/api/sms/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sender_id: 0, target_id: targetId, text }),
      });
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

  // IDs JSON export
  document.getElementById("btn-export-ids")?.addEventListener("click", () => {
    window.open("/api/export/ids_json", "_blank");
  });
}

// ─────────────────────────────────────────────────────────────────
// App Init
// ─────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  console.log("[HCC] Hytera Command Center gestartet");

  setupEventListeners();
  updateClock();
  setInterval(updateClock, 1000);
  connectWS();
  initAudioPlayer();

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
      if (STATE.addingMarker) {
        e.preventDefault();
        setMarkerMode(false);
        showToast("Marker", "Abgebrochen", "info", 2000);
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

    if (e.code === "Space") {
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


