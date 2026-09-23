# Hytera Repeater Software – Einsatz- & Leitstellensystem

Dieses Repository umfasst die vollständige, militärisch und BOS-erprobte Software-Suite zur Anbindung, Überwachung, Einsatzführung und Protokollierung der Hytera DMR-Repeater **HR1065**, **HR655** (kompakte H-Serie) sowie **RD985** (Legacy-Serie) und aller angeschlossenen Funkstellen, Leitstellen-Arbeitsplätze und Infrastrukturkomponenten.

Das System enthält eine vollständige, reverse-engineered Protokoll-Implementierung des Hytera Network Application Interface (NAI) über UDP/IP – einschließlich Call-Control, RRS-Registrierung, GPS-LIP-Ortung, DMR-SMS, bidirektionaler Sprachübertragung (RTP G.711 µ-law), Remote-PTT, Busy Channel Lockout (BCL) und Notfall-Sperrung (OTA Stun / Repeater Knockdown).

---

## 📑 Detaillierte Protokoll-Spezifikation (Reverse-Engineering)

Für Entwickler, Funktechniker und Auditoren steht eine lückenlose, technische Spezifikation aller Paketformate, Checksummen und Opcodes bereit:

👉 **[Vollständige Hytera DMR & NAI Protokoll-Aufschlüsselung (HYTERA_PROTOCOL_SPECIFICATION.md)](./docs/HYTERA_PROTOCOL_SPECIFICATION.md)**

### Abgedeckte Protokoll-Ebenen:
* **HSTRP (Hytera Site Trunking Repeater Protocol):** 12-Byte Header-Framing, Sequenzzählung, SYN/SYN-ACK Handshake, Heartbeats (30s), Paketquittierung (ACK).
* **RCP (Radio Control Protocol):** Prüfsummen-Algorithmus `(~(sum(hdr) + sum(payload)) + 0x33) & 0xFF`, Call Setup (Opcode `0x0841`), PTT Keying (Opcode `0x0041`), OTA Radio Stun / Revive (Opcode `0x0847`).
* **Call Control & RSSI:** Opcode `0x0004` (NAI Call-Status), Opcode `0xB845` (kalibrierter Repeater RSSI $\text{dBm} = \frac{\text{raw}}{-2.0}$), Watchdog (5s) und Debounce (0.6s).
* **Audio RTP & Codecs:** 12-Byte RTP-Header mit Hytera Extension Profile `0x0015`, 160-Byte G.711 µ-law Frames (8.000 Hz, 20ms), JitterBuffer v2, 48 kHz/44.1 kHz Resampling-Pipeline.
* **RRS (Radio Registration Service):** Port 30001/30002, dynamische 24-Bit ID-Erkennung für automatisches Online/Offline-Tracking.
* **GNSS / LIP Telemetrie:** Port 30003/30004, $10^{-7}$ Dezimalgrad-Skalierung, Geschwindigkeit, Kurs, Fix-Status und HDOP.
* **TMP (Text Message Protocol):** Port 30007/30008, DMR-SMS (Gruppen- & Einzelnachrichten) mit UTF-16LE-Dekodierung und BOM-Filterung.
* **SNMP MIB & Remote Knockdown:** OID-Baum `1.3.6.1.4.1.40297...` für PA-Temperatur, VSWR, Sendeleistung, Akkuüberwachung und Repeater Knockdown (`rptRepeatingState` OID `1.3.6.1.4.1.40297.1.2.2.6.0`).
* **BCL & Leitstellen-Vorrang:** Hardware-Zustandsmaschine zur Kollisionsvermeidung mit Notfall-Übersteuerung (Priority Preemption).

---

## 📁 Repository-Struktur

| Verzeichnis / Modul | Status | Beschreibung |
|---|---|---|
| **[`hytera_command_center/`](./hytera_command_center)** | 🟢 **Aktiv (Primär)** | **Unified Command Center**: Modernes FastAPI- & Leaflet-Leitstellensystem mit Echtzeit-WebSockets, Web-PTT Sprachübertragung, Busy Channel Lockout (BCL), AudioRecorder v2 (JitterBuffer, G.711 µ-law), Browser-Audio-Replay, 360°-Kartenrotation, Georeferenzierung, Hardware-Monitoring (USV Modbus TCP, ZTE, Omada) und 127 Unittests. |
| **[`docs/`](./docs)** | 📘 Referenz | Enthält die vollständige [Hytera Protokoll-Spezifikation](./docs/HYTERA_PROTOCOL_SPECIFICATION.md) mit Byte-Diagrammen. |
| **[`hytera_tactical_suite/`](./hytera_tactical_suite)** | 🟡 Stabil | **Hytera Tactical Suite**: Webbasierter Lageplan und taktische Timeline mit Leaflet, SQLite und Alarmüberwachung. |
| **[`hytera_project/`](./hytera_project)** | 🔵 Desktop-Basis | Ursprünglicher Tkinter-Dispatcher mit NAI/HSTRP-Protokollstack, PyAudio Live-Wiedergabe und ausführlicher Referenz ([`DOKUMENTATION.md`](./hytera_project/DOKUMENTATION.md)). |
| **[`map_module/`](./map_module)** | 🟡 Modul | Standalone-Lagekartenmodul mit Georeferenzierung von Feuerwehrlaufkarten und PDF-Overlays. |
| **[`tablet_recorder/`](./tablet_recorder)** | 📱 Mobil | Flutter-basierte mobile App für Begleit-Tablets im Einsatzfahrzeug. |

---

## 🚀 Schnellstart

### 1. Hytera Command Center im sicheren HTTPS-Modus (Empfohlen)

Für die Nutzung des **Web-Mikrofons (Web-PTT)** auf Tablets oder Remote-Laptops im WLAN muss das System zwingend über HTTPS aufgerufen werden:

```bash
cd hytera_command_center
START_HTTPS.bat
```
* **Lokal am Leitstellen-PC:** [https://localhost:8443](https://localhost:8443)
* **Mobile Tablets / Funkraum (WLAN):** `https://<LOKALE-IP>:8443` (z. B. `https://192.168.178.88:8443`)

*(Das lokale SSL-Zertifikat mit LAN-SANs wird beim ersten Start vollautomatisch im Verzeichnis `ssl/` erzeugt. Im Browser einmalig "Erweitert -> Weiter zu..." bestätigen).*

### 2. Standard HTTP-Modus (ohne SSL)

```bash
cd hytera_command_center
START.bat
# oder: python run.py
```
* Zugriff über [http://localhost:8000](http://localhost:8000).

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
│  (192.168.0.230)  │ │ (Modbus TCP 502)│ │  PC / Server  │
│ UDP 30001–30014   │ │                 │ │ Port 8000/8443│
│ SNMP 161 / 10162  │ │                 │ │               │
└───────────────────┘ └─────────────────┘ └───────────────┘
```

* **NAI Audio (RTP):** UDP-Port 30012 (Zeitschlitz 1) & 30014 (Zeitschlitz 2) – ITU-T G.711 µ-law 8 kHz
* **Call Control & PTT:** UDP-Port 30009 (TS1) & 30010 (TS2) – Opcodes 0x0004, 0x0841, 0x0041
* **GNSS / GPS:** UDP-Port 30003 (TS1) & 30004 (TS2)
* **SMS / TMP:** UDP-Port 30007 (TS1) & 30008 (TS2)
* **RRS Registrierung:** UDP-Port 30001 (TS1) & 30002 (TS2)
* **SNMP Telemetrie & Traps:** UDP-Port 161 (Polling) & Port 10162 (Traps)
* **USV-Monitoring:** Modbus TCP Port 502 (PowerWalker VFI 2000 ICR IoT)

---

## 🔒 Konfiguration & Funkgeräte

Reale Funkgeräte-IDs und taktische Funkrufnamen werden in der Datei `ids.json` hinterlegt:

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

Die gesamte Core-Logik wird durch automatisierte Unittests validiert:

```bash
cd hytera_command_center
pytest backend/tests/test_all.py
```
**Testergebnis:** `127 passed, 100% Erfolgsquote`
* HSTRP Protokoll-Parser, Byte-Order & Sequenz-Handling
* RCP Call Setup (0x0841), Button Request (0x0041) & Checksum-Algorithmus
* Call Control, Watchdog-Timer & Debounce
* Busy Channel Lockout (BCL) & Vorrangschaltung (Preemption)
* G.711 µ-law JitterBuffer, Audio-Resampling & StreamingWAVWriter
* RRS Registrierung, GPS-LIP Dekodierung & TMP-SMS UTF-16LE
* SNMP Poller, MIB-Parsing & SNMP Trap-Empfänger (Port 10162)
* WebSocket-Broadcast, Thread-Isolation & REST-API Endpunkte
