# Hytera Command Center (ELW / Leitstellen-System)

Echtzeit-Leitstellen- und Einsatzführungssystem für **Hytera DMR-Repeater (HR1065 / RD985)** über das Hytera ADK / NAI (Network Application Interface).

Entwickelt für den autarken Offline-Betrieb auf Einsatzleitwagen (ELW), Funkzentralen und mobilen Führungsstellen ohne zwingende Internetverbindung.

---

## 🚀 Schnellstart

1. **System starten:** Doppelklick auf [`START.bat`](file:///c:/Users/jc/Hytera%20Repetaer%20Software/hytera_command_center/START.bat) (oder in der Konsole: `python run.py`).
2. **Browser aufrufen:**
   - **Am Einsatzleit-PC:** [http://localhost:8000](http://localhost:8000)
   - **Von Tablets / Laptops im Funkraum / WLAN:** `http://<LAN-IP>:8000` (z. B. [http://192.168.178.88:8000](http://192.168.178.88:8000))

---

## 📻 Systemfunktionen

### 1. Funk- & Sprachaufzeichnung (AudioRecorder v2)
- **Automatischer Mitschnitt:** Jeder Funkspruch auf Timeslot 1 und 2 wird sekundengenau aufgezeichnet.
- **Audiocodec:** ITU-T G.711 µ-law (`PCMU`), 8.000 Hz Mono, 160 Bytes pro 20ms Frame.
- **JitterBuffer:** 16-Bit Sequence-Reordering gleicht Netzwerklatenzen aus und verwirft Duplikate.
- **Streaming-WAV:** Direktes atomares Schreiben auf Festplatte (`.part` → `.wav`), absolut absturzsicher.
- **Direktes Leitstellen-Replay:**
  - Klick auf `▶ Anhören` bei jedem Funkspruch im Dashboard oder Protokoll.
  - Floating Dispatcher Player am Bildschirmrand mit Scrubber, Speed (`0.8x`, `1.0x`, `1.2x`), `↺ 5s` Rücksprung, `+8 dB` Sprachverstärkung und WAV-Download.
  - **Sofort-Wiederholung des letzten Funkspruchs:** Tastenkombination `F8` oder `Alt+R`.
  - **Tastatur-Steuerung:** `Leertaste` (Pause/Play), `Pfeil links` / `J` (-5s), `Pfeil rechts` / `L` (+5s), `Escape` (Schließen).

### 2. Taktische Lagekarte & GPS-Tracking
- **Live-Positionen:** Automatische Dekodierung eingehender GPS-LIP-Pakete von Hytera Handfunkgeräten.
- **Offline-Kartenserver:** Integrierte Tile-Versorgung ohne externen Internetzugang.
- **Taktische Zeichen:** Setzen von Markern, Absperrungen, Geofences und Einsatzabschnitten.

### 3. Funkgeräte- & Teilnehmer-Verwaltung
- **RRS (Radio Registration Service):** Automatische Erkennung, wenn sich Funkgeräte im Repeater einbuchen oder ausschalten.
- **Alias-Zuordnung:** Funkgeräte-IDs werden über [`ids.json`](file:///c:/Users/jc/Hytera%20Repetaer%20Software/hytera_command_center/ids.json) mit Funkrufnamen (z. B. `10001` → `Florian 1/11`) verknüpft.

### 4. Textnachrichten (DMR-SMS)
- Empfangen und Senden von DMR-Kurznachrichten direkt an Handfunkgeräte oder Gruppen.

### 5. Hardware- & Umgebungsüberwachung
- **USV-Monitoring:** Schneider / APC Modbus-TCP Polling (Batteriestatus, Netzspannung, Last, Autonomiezeit).
- **Router / LTE-Monitoring:** Status des LTE-Routers (Signalstärke, Verbindungsstatus) und Omada-Netzwerks.
- **SNMP-Trap Empfänger:** UDP Port 10162 zur Protokollierung von Repeater-Alarmen (PA-Temperatur, VSWR, Lüfterausfall).

---

## 🌐 Netzwerk-Ports & Schnittstellen

| Port (Protokoll) | Richtung | Dienst / Funktion |
|---|---|---|
| **UDP 30001 / 30002** | Inbound | RRS (Radio Registration Service) – TS1 / TS2 |
| **UDP 30003 / 30004** | Inbound | GPS / LIP Telemetrie – TS1 / TS2 |
| **UDP 30005 / 30006** | Inbound | Repeater Telemetrie & Diagnose – TS1 / TS2 |
| **UDP 30007 / 30008** | In/Out | DMR-SMS Kurznachrichten – TS1 / TS2 |
| **UDP 30009 / 30010** | Inbound | Call Control (PTT Start / End) – TS1 / TS2 |
| **UDP 30012 / 30014** | Inbound | Audio RTP Stream (G.711 µ-law) – TS1 / TS2 |
| **UDP 10162** | Inbound | SNMP Traps (Repeater Alarmmeldungen) |
| **TCP 8000** | Inbound | HTTP Webserver & WebSocket (`/ws`) |

---

## 🧹 Bereinigung von Testdaten für den Echteinsatz

Das System bietet zwei Wege zur vollständigen Bereinigung aller Testeinsätze:

1. **Per API / Web:**
   - Aufruf von `POST /api/system/reset-demo-data`.
   - Alle Browser-Clients leeren ihre Live-Tabellen automatisch per WebSocket-Broadcast `system_reset`.
2. **Datenbank & Audio-Dateien:**
   - Datenbank: `command_center.db` enthält nach dem Reset ausschließlich die in `ids.json` konfigurierten realen Funkgeräte.
   - Aufnahmen: Werden aus `frontend/static/recordings/` bereinigt.

---

## 🧪 Automatisierte Tests

Das System verfügt über eine vollständige Test-Suite mit 74 Unit- und Integrationstests:

```bash
pytest backend/tests/test_all.py
```

Getestete Komponenten:
- UDP-Socket Lifecycle & Puffer-Initialisierung
- HSTRP Protokoll-Dekodierung & Safe Dispatcher
- Call Control & PTT Watchdog Timer
- G.711 µ-law JitterBuffer & StreamingWAVWriter
- Paralleler WebSocket-Broadcast mit Client-Fehlerisolation
- SQLite WAL-Modus & Busy-Timeout Handling
- API-Endpunkte & Datenbereinigung
