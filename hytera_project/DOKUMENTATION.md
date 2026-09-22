# Hytera HR1065 – Mission Control Dispatcher
## Vollständige technische Dokumentation

> **Version:** 2026-08  
> **Plattform:** Windows 10/11, Python 3.10+  
> **Erstellt:** automatisch generiert

---

## Inhaltsverzeichnis

1. [Systemübersicht](#1-systemübersicht)
2. [Architektur](#2-architektur)
3. [Installation & Start](#3-installation--start)
4. [Modulbeschreibungen](#4-modulbeschreibungen)
5. [Konfiguration](#5-konfiguration)
6. [Netzwerk & Ports](#6-netzwerk--ports)
7. [DMR-IDs (ids.json)](#7-dmr-ids-idsjson)
8. [Einsatz-Verwaltung (Mission)](#8-einsatz-verwaltung-mission)
9. [USV-Monitoring](#9-usv-monitoring)
10. [Live-Dashboard (Browser)](#10-live-dashboard-browser)
11. [Tailscale VPN-Integration](#11-tailscale-vpn-integration)
12. [EXE-Build](#12-exe-build)
13. [Diagnose-Tools](#13-diagnose-tools)
14. [Bekannte Grenzen & Hinweise](#14-bekannte-grenzen--hinweise)

---

## 1. Systemübersicht

Der **Hytera HR1065 Mission Control Dispatcher** ist eine Python-basierte Desktop-Anwendung zur Überwachung und Protokollierung des Hytera HR1065 DMR-Repeaters. Er empfängt alle Datenströme des Repeaters über das **NAI (Network Application Interface)** per UDP und stellt sie in einem grafischen Dashboard (Tkinter, Cyberpunk-Design) dar.

### Hauptfunktionen

| Funktion | Beschreibung |
|---|---|
| **Live-Audio** | G.711 µ-law → PCM-Wiedergabe in Echtzeit über PyAudio |
| **Call-Control** | PTT-Erkennung, Anruftyp (Gruppe/Privat/All-Call), RSSI-Anzeige |
| **GPS/GNSS** | Positionsdaten aller Radios auf Karte |
| **SMS/TMP** | Empfang und Anzeige von DMR-Textnachrichten |
| **RRS** | Radio Registration Service – An-/Abmeldung der Funkgeräte |
| **Telemetrie** | Digitale/analoge I/O-Kanäle der Radios |
| **USV-Monitoring** | BlueWalker PowerWalker 2000 ICR IoT via Modbus TCP |
| **Einsatz-Logger** | Zeitstrahl aller Ereignisse, HTML-Export, Audio-Segmente |
| **Live-Browser-Dashboard** | SSE-basierter Echtzeit-Stream für Tablets/Remote-Geräte |
| **Tailscale VPN** | Automatische externe URL via Tailscale-Netz |

---

## 2. Architektur

```
┌─────────────────────────────────────────────────────┐
│                   Hytera HR1065                     │
│              (DMR-Repeater, 192.168.1.100)          │
└──────────────────────┬──────────────────────────────┘
                       │ UDP (NAI / HSTRP-Protokoll)
          ┌────────────▼────────────────────────────┐
          │         hytera_protocol.py              │
          │   HSTRPListener (Basis-Thread-Klasse)   │
          │  ├── AudioListener    (TS1/TS2)         │
          │  ├── CallControlListener (CC1/CC2)      │
          │  ├── GNSSListener    (GPS1/GPS2)        │
          │  ├── SMSListener     (SMS1/SMS2)        │
          │  ├── RRSListener     (RRS1/RRS2)        │
          │  └── TelemetryListener (Tele1/Tele2)   │
          └────────────┬────────────────────────────┘
                       │ Callbacks
          ┌────────────▼────────────────────────────┐
          │              app.py (App)               │
          │   Tkinter-GUI / Tabs / AudioPlayer      │
          └──┬─────────┬──────────┬────────────────┘
             │         │          │
    ┌────────▼──┐ ┌────▼────┐ ┌──▼──────────────┐
    │usv_monitor│ │mission_ │ │ mission_server   │
    │   .py     │ │logger.py│ │     .py          │
    │Modbus TCP │ │JSON+HTML│ │HTTP+SSE Port 19080│
    └───────────┘ └─────────┘ └──────────────────┘
                                        │
                              ┌─────────▼────────┐
                              │timeline_viewer.html│
                              │(Browser-Dashboard)│
                              └──────────────────┘
```

### Datenfluss (Beispiel: PTT-Ereignis)

1. Radio drückt PTT → Repeater sendet RTP-Audio-Pakete auf UDP-Port 30012 (TS1)
2. `AudioListener.on_rtp()` empfängt das Paket, dekodiert die Radio-ID aus dem RTP Extension Header
3. `app.py` erhält Callback → aktualisiert UI, startet Aufnahme, ruft `MissionLogger.log_ptt_start()` auf
4. `MissionLogger` persistiert das Ereignis in `mission.json` und broadcastet es per SSE
5. Browser-Clients erhalten das Event sofort über den `/events`-SSE-Stream

---

## 3. Installation & Start

### Voraussetzungen

```
Python 3.10 oder neuer
pip-Pakete (manuell installieren):
  pip install pyaudio
  pip install numpy
  pip install pymodbus
  pip install pyinstaller   # nur für EXE-Build
```

> **Hinweis:** `pyaudio` benötigt auf Windows oft das entsprechende
> Wheel: `pip install pipwin && pipwin install pyaudio`

### Starten

**Option A – Direkt mit Python:**
```
START_DISPATCHER.bat  (Doppelklick)
```
oder manuell:
```
python app.py
```

**Option B – Als EXE (vorher gebaut):**
```
dist\HyteraDispatcher\HyteraDispatcher.exe
```

### EXE erstellen

```
BUILD_EXE.bat  (Doppelklick)
```
Erstellt `dist\HyteraDispatcher\` – den **gesamten Ordner** weitergeben,
nicht nur die `.exe`.

---

## 4. Modulbeschreibungen

### 4.1 `app.py` – Haupt-Applikation

**Klassen:**

| Klasse | Beschreibung |
|---|---|
| `App(tk.Tk)` | Hauptfenster, koordiniert alle Komponenten |
| `AudioPlayer` | G.711 µ-law → 16-bit PCM Wiedergabe via PyAudio |

**Tabs der GUI:**

| Tab | Inhalt |
|---|---|
| 📊 Dashboard | Slot-Status TS1/TS2, aktiver Anruf, Radio-Online-Liste, USV-Schnellstatus, Protokoll-Log |
| 📋 Einsatz | Einsatz starten/stoppen, Notizen, Bilder, Timeline-Browser, Einsatz-Archiv |
| 🔊 Audio | Ausgabegerät wählen, Lautstärke, Aufnahmestatus |
| 🗺 GPS/SMS | GPS-Positionen, SMS-Eingang (optional ausblendbar) |
| ⚡ USV | Detaillierte USV-Statusanzeige (Batterie, Last, Spannung, Temperatur) |
| ⚙ Einstellungen | IP/Port-Konfiguration, Tab-Sichtbarkeit, Radio-ID-Verwaltung |

**Hilfsfunktionen:**

```python
_apply_gain(pcm_bytes, factor)  # Soft-Clipping Lautstärkeregelung (tanh-Limiter)
_lbl(), _btn(), _sep(), _panel()  # Widget-Factories im Cyberpunk-Stil
load_config() / save_config()   # JSON-Konfiguration laden/speichern
load_ids() / save_ids()         # DMR-ID-Datenbank laden/speichern
resolve_id(radio_id)            # ID → "Name [Gerät] (ID xxx)"
```

**Farbpalette (Cyberpunk / Night City):**

```python
C = {
    "bg":    "#0a0a0f",   # Hintergrund
    "panel": "#0d0d1a",   # Panel
    "acc":   "#fce300",   # Night City Yellow (Akzentfarbe)
    "acc2":  "#00fff7",   # Cyan
    "red":   "#ff003c",
    "grn":   "#39ff14",
    "org":   "#ff6b00",
}
```

---

### 4.2 `hytera_protocol.py` – HSTRP-Protokoll & Listener

Implementiert das Hytera HSTRP-Protokoll (Hytera Streaming Protocol) und alle NAI-Datenströme.

**HSTRP-Pakettypen:**

| Konstante | Wert | Bedeutung |
|---|---|---|
| `HSTRP_SYN` | 0x24 | Verbindungsaufbau vom Repeater |
| `HSTRP_SYNACK` | 0x05 | Antwort auf SYN |
| `HSTRP_HEARTBEAT` | 0x02 | Keepalive (alle ~5s) |
| `HSTRP_ACK` | 0x01 | Bestätigung eines Datenpakets |
| `HSTRP_FROM_RAD` | 0x20 | Daten vom Repeater |

**Klassen:**

#### `HSTRPListener(threading.Thread)` – Basisklasse

Öffnet einen oder mehrere UDP-Ports, führt HSTRP-Handshake durch,
verwendet `selectors` für effizientes Multiplexing mehrerer Ports.

```python
# Methoden die Subklassen überschreiben:
on_packet(data, addr)   # HSTRP-Nutzlast empfangen
on_rtp(payload, addr)   # RTP-Paket empfangen
_on_idle_check()        # Jede ~20ms aufgerufen (Idle-Erkennung)
```

#### `AudioListener` – Audio-Daten (TS1 / TS2)

- Empfängt RTP-Pakete mit G.711 µ-law Audio
- Extrahiert Radio-ID aus RTP Extension Header (3 Byte, Hytera-proprietär)
- Erkennt PTT-Start/Ende anhand von Paket-Timeout (`timeout`-Sekunden ohne Paket)
- Callbacks: `audio_feed`, `status_cb`, `bytes_cb`, `radio_id_cb`, `ptt_start_cb`, `ptt_end_cb`, `write_cb`

**RTP-Extension-Header (Hytera-proprietär):**
```
Byte 0-3:  uint32 BE, untere 24 Bit = DMR Radio-ID
Gültig:    100 ≤ radio_id ≤ 16.776.415
```

#### `CallControlListener` – Anruf-Steuerung

Dekodiert RCP-Pakete (Radio Control Protocol, Little-Endian):

| Opcode | Bedeutung |
|---|---|
| `0xB845` | `RCP_REPEATER_BROADCAST_TX_STATUS` – Anruf mit RSSI |
| `0xB843` | `RCP_BROADCAST_TX_STATUS` – einfacher Anruf-Status |
| `0x0003` | RRS Register (Radio online) |
| `0x0001` | RRS Offline |

**RSSI-Dekodierung:**
```
raw (signed int16) → rssi_dbm = -raw // 2  (Hytera: halbe dBm-Schritte)
Gültig: -130 ≤ rssi_dbm ≤ 0
```

**RSSI-Qualitätsstufen:**
```
≥ -70 dBm  → ausgezeichnet
≥ -80 dBm  → gut
≥ -90 dBm  → mäßig
≥ -100 dBm → schwach
< -100 dBm → sehr schwach
```

#### `GNSSListener` – GPS-Positionen

Hytera GNSS-Payload-Format:
```
Offset 0:  uint32 BE – Radio-IP (untere 24 Bit = ID)
Offset 4:  int32 – Latitude × 1e7
Offset 8:  int32 – Longitude × 1e7
```

#### `SMSListener` – Textnachrichten (TMP)

| Opcode | Bedeutung |
|---|---|
| `0x00A1` | TMP Privat mit ACK |
| `0x80A1` | TMP Privat ohne ACK |
| `0x00B1` | TMP Gruppe |

Text-Kodierung: UTF-16-LE

#### `RRSListener` – Radio Registration Service

Registriert An-/Abmeldung von Radios:
- Opcode `0x0003` → Radio online
- Opcode `0x0001` → Radio offline

#### `TelemetryListener` – Telemetrie

Digitale und analoge I/O-Kanaldaten der Radios.

**Datenklassen:**

```python
CallEvent    # slot, call_type, sender_id, target_id, status, rssi
GPSFix       # radio_id, lat, lon, speed, heading, accuracy
SMSMessage   # sender_id, target_id, text, is_group
RadioStatus  # radio_id, online
TelemetryEvent # radio_id, channel, value, analog
```


---

### 4.3 `usv_monitor.py` – USV-Ueberwachung (Modbus TCP)

Ueberwacht die **BlueWalker PowerWalker VFI 2000 ICR IoT** USV via Modbus TCP.

**Voraussetzung:** Modbus TCP in der USV aktivieren:
`LCD -> Settings -> Communication -> Modbus TCP -> ON`

**Register-Map (durch Live-Scan ermittelt):**

| Block | Adresse | Offset | Wert | Einheit |
|---|---|---|---|---|
| A | 129 | 0 | Eingangsfrequenz | Hz (div10) |
| A | 132 | 3 | Eingangsspannung | V (div10) |
| A | 144 | 15 | Ausgangsfrequenz | Hz (div10) |
| A | 147 | 18 | Ausgangsspannung | V (div10) |
| A | 169 | 40 | Batterieladung | % |
| A | 170 | 41 | Restlaufzeit | Minuten |
| B | 225 | 0 | Last | % |
| B | 226 | 1 | Temperatur | Grad C |
| B | 235 | 10 | Netz-Status-Flag | 0=Batterie, 1=Normal |
| B | 240 | 15 | Alarm-Flag | 0=OK, 1=Alarm |

**Batteriestatus-Logik:**
- Netz Normal + Ladestand < 100% -> "Laden"
- Ladestand >= 80% -> "Normal"
- Ladestand >= 30% -> "Schwach"
- Ladestand < 30% -> "Leer!"

---

### 4.4 `mission_logger.py` – Einsatz-Logger

Persistiert alle Ereignisse eines Einsatzes und exportiert HTML-Reports.

**Ereignistypen (MissionEvent.etype):**

| Typ | Ausloeser | Daten |
|---|---|---|
| `ptt_start` | PTT-Taste gedrueckt | radio_id, slot, byte_offset, rssi |
| `ptt_end` | PTT freigegeben | radio_id, slot, duration, byte_count |
| `online` | Radio meldet sich an | radio_id |
| `offline` | Radio meldet sich ab | radio_id |
| `gps` | GPS-Position empfangen | radio_id, lat, lon, speed, heading |
| `sms` | SMS empfangen | sender_id, target_id, text, is_group |
| `note` | Manuelle Notiz | text, radio_id |
| `image` | Bild hinzugefuegt | filename, caption |
| `usv` | USV-Statusaenderung | batt_pct, batt_status, runtime_min |
| `rssi` | RSSI-Messpunkt | radio_id, slot, rssi |

**Performance: 5-Sekunden-Batch fuer GPS/USV**

GPS- und USV-Ereignisse werden 5s gepuffert (`_add_batched`) um Disk-I/O zu reduzieren.
PTT, SMS, Notizen und Bilder werden sofort persistiert.

**Audio-Aufnahme-Mechanismus:**

Waehrend eines Einsatzes werden fuer jeden Slot kontinuierlich rohe G.711-Bytes
in Master-Dateien geschrieben:
```
missions/<name>/TS1_master.raw   # Lueckenloser G.711-ulaw-Stream, 8kHz
missions/<name>/TS2_master.raw
```

Jeder `ptt_start`-Event enthaelt `byte_offset` (Position in Masterdatei).
`ptt_end` enthaelt `byte_count` (Anzahl Bytes dieses Segments).

Beim HTML-Export werden die Segmente als WAV-Dateien in `audio/` geschnitten:
```
missions/<name>/audio/TS1_001.wav
missions/<name>/audio/TS1_002.wav ...
```

**HTML-Exporte:**

| Datei | Inhalt |
|---|---|
| `mission.json` | Alle Ereignisse als JSON |
| `timeline.html` | Interaktiver Canvas-Zeitstrahl (Standalone) |
| `Funkprotokoll.html` | Tabellarisches Protokoll mit Audio-Playern |

---

### 4.5 `mission_server.py` – HTTP/SSE-Server

Leichtgewichtiger HTTP-Server auf Port **19080** mit Server-Sent Events.

**HTTP-Endpunkte:**

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/` | timeline_viewer.html ausliefern |
| GET | `/events` | SSE-Stream (neue Ereignisse live) |
| GET | `/state` | Aktueller Zustand als JSON |
| GET | `/report` | Live-Funkprotokoll als HTML |
| GET | `/audio/<slot>/<byte_off>/<byte_count>` | Audio-Segment als WAV |
| GET | `/img/<filename>` | Bild aus Einsatz-Ordner |
| GET | `/status` | USV + Repeater-Status als JSON |
| POST | `/note` | Notiz hinzufuegen |
| POST | `/image` | Bild hochladen |

---

### 4.6 `tailscale_helper.py` – VPN-Integration

Erkennt automatisch Tailscale und erzeugt externe URLs.

```python
get_tailscale_ip()          # Gibt 100.x.x.x zurueck oder None
is_tailscale_running()      # True/False
get_remote_url(local_url)   # Ersetzt LAN-IP durch Tailscale-IP
```

---

## 5. Konfiguration

Gespeichert in `config.json`.

| Schluessel | Standard | Beschreibung |
|---|---|---|
| `repeater_ip` | `192.168.1.100` | IP des Hytera HR1065 |
| `listen_ip` | `0.0.0.0` | Lokale Bind-IP |
| `ts1_port` | `30012` | Audio Zeitschlitz 1 |
| `ts2_port` | `30014` | Audio Zeitschlitz 2 |
| `cc1_port` | `30009` | Call Control TS1 |
| `cc2_port` | `30010` | Call Control TS2 |
| `gps_port` | `30003` | GNSS TS1 |
| `gps2_port` | `30004` | GNSS TS2 |
| `sms_port` | `30007` | SMS/TMP TS1 |
| `sms2_port` | `30008` | SMS/TMP TS2 |
| `rrs1_port` | `30001` | RRS TS1 |
| `rrs2_port` | `30002` | RRS TS2 |
| `tele_port` | `30005` | Telemetrie TS1 |
| `tele2_port` | `30006` | Telemetrie TS2 |
| `timeout` | `2.0` | Sekunden ohne Audio -> PTT-Ende |
| `audio_device` | `-1` | Ausgabegeraet (-1 = System-Standard) |
| `audio_enabled` | `true` | Live-Audio aktiviert |
| `usv_ip` | `192.168.1.102` | IP der USV |
| `usv_interval` | `30` | USV-Abfrageintervall in Sekunden |
| `usv_enabled` | `true` | USV-Monitoring aktiviert |
| `tab_audio` | `true` | Audio-Tab anzeigen |
| `tab_gps_sms` | `false` | GPS/SMS-Tab anzeigen |

---

## 6. Netzwerk & Ports

### Repeater -> Dispatcher (eingehend UDP)

| Port | Dienst | Zeitschlitz |
|---|---|---|
| 30001 | RRS | TS1 |
| 30002 | RRS | TS2 |
| 30003 | GNSS/GPS | TS1 |
| 30004 | GNSS/GPS | TS2 |
| 30005 | Telemetrie | TS1 |
| 30006 | Telemetrie | TS2 |
| 30007 | SMS/TMP | TS1 |
| 30008 | SMS/TMP | TS2 |
| 30009 | Call Control | TS1 |
| 30010 | Call Control | TS2 |
| 30012 | Audio RTP | TS1 |
| 30014 | Audio RTP | TS2 |

### Dispatcher -> Extern

| Port | Protokoll | Ziel |
|---|---|---|
| 502 | Modbus TCP | USV (192.168.1.102) |
| 19080 | HTTP/SSE | Browser-Clients |

### Windows-Firewall (Admin PowerShell)

```powershell
New-NetFirewallRule -DisplayName "Hytera Dispatcher" -Direction Inbound -Protocol UDP -LocalPort 30001-30014 -Action Allow
New-NetFirewallRule -DisplayName "Hytera Dashboard"  -Direction Inbound -Protocol TCP -LocalPort 19080 -Action Allow
```

---

## 7. DMR-IDs (ids.json)

Format:
```json
{
  "1001": { "name": "Mitarbeiter 1", "geraet": "RT81 #1" },
  "9001": { "name": "Einsatzleitung", "geraet": "RT81 #4" }
}
```

Schluesse mit `_`-Praefix werden ignoriert (Kommentare).

---

## 8. Einsatz-Verwaltung (Mission)

### Ordnerstruktur

```
missions/
  Einsatz_2026-08-13/
    mission.json          # Alle Ereignisse
    timeline.html         # Interaktiver Zeitstrahl
    Funkprotokoll.html    # Tabellarisches Protokoll
    TS1_master.raw        # G.711 Audio-Master TS1
    TS2_master.raw        # G.711 Audio-Master TS2
    audio/
      TS1_001.wav
      TS1_002.wav
    img_1234567890_foto.jpg
```

### Workflow

1. **Einsatz starten:** Name eingeben -> "Start"
   - Ordner wird angelegt, SSE-Server startet
   - Timeline oeffnet sich automatisch im Browser
2. **Laufend:** PTT/GPS/SMS/RRS automatisch geloggt, Notizen/Bilder manuell
3. **Einsatz beenden:** Audio-Segmente werden als WAV exportiert, HTML generiert

---

## 9. USV-Monitoring

| Feld | Beschreibung |
|---|---|
| Batterieladung | % + Status |
| Netz-Status | Normal / Batterie! / Alarm |
| Eingangsspannung | V |
| Ausgangsspannung | V |
| Last | % |
| Restlaufzeit | Minuten |
| Temperatur | Grad C |

---

## 10. Live-Dashboard (Browser)

**URL nach Einsatz-Start:**
- Lokal: `http://192.168.1.xxx:19080/`
- Protokoll: `http://192.168.1.xxx:19080/report`
- VPN (Tailscale): `http://100.x.x.x:19080/`

---

## 11. Tailscale VPN-Integration

1. Tailscale auf Windows-PC installieren (tailscale.com)
2. Kostenlosen Account erstellen, einloggen
3. Dispatcher erkennt Tailscale automatisch bei Einsatz-Start
4. VPN-URL wird im Einsatz-Tab angezeigt

---

## 12. EXE-Build

`BUILD_EXE.bat` erstellt eine EXE mit PyInstaller:

```
Ausgabe: dist\HyteraDispatcher\HyteraDispatcher.exe
```

Den **gesamten Ordner** `dist\HyteraDispatcher\` weitergeben, nicht nur die EXE!

---

## 13. Diagnose-Tools

| Datei | Zweck |
|---|---|
| `netzwerk_check.py` | UDP-Verbindungstest aller Ports |
| `ptt_diagnose.py` | Audio-Port-Analyse |
| `ptt_diagnose2.py` | PTT-Diagnose mit Radio-ID-Extraktion |
| `ptt_full_test.py` | Vollstaendiger Protokoll-Test |
| `usv_diagnose.py` | Modbus-Register-Scan |
| `rrs_diagnose.py` | RRS-Protokoll-Analyse |
| `audio_diagnose.py` | Audio-Stream-Analyse + WAV-Export |
| `simulate_test.py` | Repeater-Simulation (ohne echtes Geraet) |

---

## 14. Bekannte Grenzen & Hinweise

- **Audio-Codec:** Nur G.711 ulaw (8kHz mono) – kein AMBE/IMBE
- **RSSI:** Kalibrierung geraetespezifisch, Werte je nach Firmware variabel
- **Radio-ID-Erkennung:** Aus RTP Extension Header (sofort) oder CC-Listener (~100ms Verzoegerung)
- **USV Modbus:** pymodbus >= 3.0 erforderlich (API: `device_id` statt `unit_id`)
- **SSE-Queue:** Max. 200 Events gepuffert, tote Verbindungen nach 20s erkannt

---

## 15. Weiterentwicklung: Hytera Command Center (ELW v2.0)

Das System wurde in den Ordner `hytera_command_center/` als eigenständige, hochmoderne Leitstellenanwendung überführt:

- **AudioRecorder v2:** 16-Bit Sequence-JitterBuffer, atomarer StreamingWAV-Writer, integrierte Retention (30 Tage).
- **Leitstellen-Replay:** Sofortige Funkspruch-Wiedergabe im Browser (`▶ Anhören`), Floating Dispatcher Player Bar (`↺ 5s`, `0.8x-1.2x`, `+8 dB` Sprachverstärkung, Hotkey `F8`/`Alt+R`).
- **WebSocket-Architektur:** Vollduplex-Echtzeitkommunikation mit parallelem Broadcast und Client-Fehlerisolation.
- **Offline-Fähigkeit:** Integrierte Karten-Tiles, SQLite im WAL-Modus mit 5000ms Busy-Timeout.
- **Vollständige LibreNMS MIB-Telemetrie:** Reiner Python BER/DER ASN.1 SNMP-Poller (Multi-Batch ohne MTU-Fragmentierung), Erfassung sämtlicher Repeater-Parameter (`HYTERA-REPEATER-MIB` & MIB-2 `sysUpTime`):
  - **HF & Leistung:** TX-Sendefrequenz & RX-Empfangsfrequenz (MHz), Vorwärtsleistung (W), Reflektierte Leistung (W), VSWR-Stehwellenverhältnis mit dynamischer Ampel-Bewertung.
  - **Betrieb & Kanal:** Aktiver Kanalname, Zonenname, Sendeleistungsstufe (High/Low), Repeater-Alias, Repeater DMR-ID.
  - **Hardware & Umgebung:** PA-Temperatur (°C), Betriebsspannung (V), Lüfterdrehzahl (RPM), Stromquelle (DC/AC/Battery), System-Uptime.
  - **Identifikation & Firmware:** Gerätemodell, Seriennummer, Firmware-Version, RCDB-Version (mit automatischer UTF-16LE/UTF-8 Null-Byte Bereinigung).
  - **Echtzeit-Schutzalarme:** PA-Übertemperatur, VSWR-Fehler, Über-/Unterspannung, Lüfterausfall, TX/RX-PLL-Fehler, Überstrom, Batterie schwach.
- **Automatisierte Tests:** 83/83 Unittests in `backend/tests/test_all.py`.

---

*Dokumentation aktualisiert: 2026-09-22 | Hytera HR1065 Command Center*

