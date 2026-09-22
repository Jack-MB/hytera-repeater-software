# Hytera Repeater Software – Einsatz- & Leitstellensystem

Dieses Repository umfasst die vollständige Software-Suite zur Anbindung, Überwachung, Einsatzführung und Protokollierung des DMR-Repeaters **Hytera HR1065** sowie der angeschlossenen Funkstellen und Infrastrukturkomponenten.

---

## 📁 Repository-Struktur

| Verzeichnis / Modul | Status | Beschreibung |
|---|---|---|
| **[`hytera_command_center/`](./hytera_command_center)** | 🟢 **Aktiv (Primär)** | **Unified Command Center**: Modernes FastAPI- & Leaflet-Leitstellensystem mit Echtzeit-WebSockets, AudioRecorder v2 (JitterBuffer, G.711 µ-law), Browser-Audio-Replay, 360°-Kartenrotation, Georeferenzierung, Hardware-Monitoring (USV Modbus TCP, ZTE, Omada ER605) und Unittest-Suite (76 Tests). |
| **[`hytera_tactical_suite/`](./hytera_tactical_suite)** | 🟡 Stabil | **Hytera Tactical Suite**: Webbasierter Lageplan und taktische Timeline mit Leaflet, SQLite und Alarmüberwachung. |
| **[`hytera_project/`](./hytera_project)** | 🔵 Desktop-Basis | Ursprünglicher Tkinter-Dispatcher mit NAI/HSTRP-Protokollstack, PyAudio Live-Wiedergabe und ausführlicher technischer Referenz ([`DOKUMENTATION.md`](./hytera_project/DOKUMENTATION.md)). |
| **[`map_module/`](./map_module)** | 🟡 Modul | Standalone-Lagekartenmodul mit Georeferenzierung von Feuerwehrlaufkarten und PDF-Overlays. |
| **[`tablet_recorder/`](./tablet_recorder)** | 📱 Mobil | Flutter-basierte mobile App für Begleit-Tablets im Einsatzfahrzeug. |

---

## 🚀 Schnellstart

### 1. Hytera Command Center (Empfohlen)

Das Command Center vereint alle Protokolle, Audioaufnahmen, Karten und Hardwareüberwachung in einer Weboberfläche:

```bash
cd hytera_command_center
pip install -r requirements.txt
python run.py
```

* **Web-Zugriff (Leitstellen-PC):** [http://localhost:8000](http://localhost:8000)
* **Web-Zugriff (Funkraum / ELW-WLAN):** `http://<LOKALE-IP>:8000`

### 2. Schnellstart via Batch-Skripte

Im Hauptverzeichnis stehen vorkonfigurierte Starter bereit:
* `START_TACTICAL_SUITE.bat`: Startet die Tactical Suite.
* `START_LAGEPLAN.bat`: Startet das Lagekartenmodul.

---

## 📡 Netzwerktopologie & Schnittstellen

```
┌─────────────────────────────────────────────────────────┐
│              ZTE 5G Router (192.168.0.1)                │
│                 (Internet & Uplink)                     │
└────────────────────────────┬────────────────────────────┘
                             │ WAN
┌────────────────────────────▼────────────────────────────┐
│          TP-Link Omada ER605 (192.168.1.1)              │
│                 (Lokales Einsatz-LAN)                   │
└───────┬───────────────────┬───────────────────┬─────────┘
        │                   │                   │
┌───────▼───────────┐ ┌─────▼───────────┐ ┌─────▼─────────┐
│   Hytera HR1065   │ │ BlueWalker USV  │ │ Leitstellen-  │
│  (192.168.1.100)  │ │ (Modbus TCP 502)│ │  PC / Server  │
│ UDP 30012 / 30014 │ │                 │ │ (Port 8000)   │
└───────────────────┘ └─────────────────┘ └───────────────┘
```

* **NAI Audio (RTP):** UDP-Port 30012 (Zeitschlitz 1) & 30014 (Zeitschlitz 2) – ITU-T G.711 µ-law 8 kHz
* **Call Control & Status:** UDP-Port 30002 (TS1) & 30004 (TS2) – Opcode 0x0004
* **GNSS / GPS:** UDP-Port 30001 (TS1) & 30003 (TS2)
* **SMS / RRS:** UDP-Port 30005 & 30006
* **USV-Monitoring:** Modbus TCP Port 502 (Registerblock A & B)

---

## 🔒 Konfiguration & Funkgeräte

Echte Funkgeräte-IDs und taktische Rufnamen werden in der Datei `ids.json` (z. B. in [`hytera_command_center/ids.json`](./hytera_command_center/ids.json)) hinterlegt:

```json
{
  "10001": {
    "name": "Florian 1/11",
    "group": "Führung",
    "type": "Handfunkgerät"
  }
}
```

---

## 🧪 Tests & Qualitätssicherung

Alle Tests des primären Command Centers ausführen:

```bash
cd hytera_command_center
pytest backend/tests/test_all.py
```
*(76/76 Tests verifiziert: Protokoll-Parser, JitterBuffer, Audio-Streaming, Hardware-Monitoring, REST-API & WebSocket-Flows).*
