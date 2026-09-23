# Hytera Command Center (ELW / Leitstellen-System)

Echtzeit-Leitstellen-, Disponenten- und Einsatzführungssystem für **Hytera DMR-Repeater (HR1065 / RD985)** über das Hytera ADK / NAI (Network Application Interface).

Entwickelt für den autarken Offline-Betrieb auf Einsatzleitwagen (ELW), Funkzentralen und mobilen Führungsstellen ohne zwingende externe Internetverbindung.

---

## 📑 Protokoll-Spezifikation (Reverse-Engineering)

Die vollständige, tiefentechnische Dokumentation aller Hytera-Protokolle mit Byte-Offsets, Checksummen-Formeln und Hex-Diagrammen ist direkt einsehbar unter:

👉 **[HYTERA_PROTOCOL_SPECIFICATION.md](./HYTERA_PROTOCOL_SPECIFICATION.md)**

---

## 🚀 Schnellstart

### Option 1: Sicherer HTTPS-Betrieb (Empfohlen für Web-Mikrofon / PTT)

Da moderne Browser (Chrome, Safari, Firefox, Edge) den Mikrofon-Zugriff (`getUserMedia`) aus Sicherheitsgründen **ausschließlich über verschlüsselte HTTPS-Verbindungen** gestatten, verfügt das System über eine integrierte SSL-Zertifikatsgenerierung:

1. Doppelklick auf [`START_HTTPS.bat`](./START_HTTPS.bat) (oder in der Konsole: `python run_https.py`).
2. **Browser aufrufen:**
   - **Am Einsatzleit-PC:** [https://localhost:8443](https://localhost:8443)
   - **Von Tablets / Laptops im Funkraum / WLAN:** `https://<LAN-IP>:8443` (z. B. `https://192.168.178.88:8443`)
3. Im Browser einmalig die Sicherheitswarnung ("Erweitert" -> "Weiter zu...") bestätigen. Danach ist das Mikrofon auf allen Geräten im LAN voll einsatzbereit!

### Option 2: Standard HTTP-Betrieb

1. Doppelklick auf [`START.bat`](./START.bat) (oder: `python run.py`).
2. Browser aufrufen: [http://localhost:8000](http://localhost:8000).

---

## 📻 Hauptfunktionen

### 1. Web-PTT & Funk-Aussendung (TX Transmitter)
- **Direktes Senden aus dem Browser:** Sprachübertragung von der Leitstelle oder vom Begleit-Tablet direkt auf den Funkkanal.
- **Ziel-Modi:**
  - **All-Call (Sammelruf):** Erreicht alle Funkteilnehmer zeitgleich (DMR-ID `16777215`).
  - **Direktruf / Gruppenruf:** Gezieltes Ansprechen einzelner Handfunkgeräte oder taktischer Gruppen.
- **AudioWorklet & Resampling:** Erfasst PCM-Audio im Browser und resampelt dynamisch von 44.1/48 kHz auf 8.000 Hz ITU-T G.711 µ-law.
- **PTT-Steuerung:** Betätigen per Mausklick, Touch oder `Leertaste` (Push-to-Talk).
- **Time-Out-Timer (TOT) Schutz:** Serverseitiger 30-Sekunden Schutz gegen versehentliches Dauersenden.

### 2. Busy Channel Lockout (BCL) & Leitstellen-Vorrang (Preemption)
- **Kollisionsvermeidung:** Wenn ein Funkteilnehmer spricht, sperrt das System die PTT automatisch ("KANAL BELEGT") und verhindert Kanalüberlagerungen.
- **Leitstellen-Vorrang (Priority Preemption):** Im Notfall kann die Einsatzleitung den laufenden Funkverkehr per Klick übersteuern und die Aussendung erzwingen.

### 3. Funk- & Sprachaufzeichnung (AudioRecorder v2)
- **Automatischer Mitschnitt:** Jeder Funkspruch auf Timeslot 1 und 2 wird sekundengenau aufgezeichnet.
- **Audiocodec:** ITU-T G.711 µ-law (`PCMU`), 8.000 Hz Mono, 160 Bytes pro 20ms Frame.
- **JitterBuffer:** 16-Bit Sequence-Reordering gleicht Netzwerklatenzen aus und verwirft Duplikate.
- **Streaming-WAV:** Direktes atomares Schreiben auf Festplatte (`.part` → `.wav`), absolut absturzsicher.
- **Direktes Leitstellen-Replay:**
  - Klick auf `▶ Anhören` bei jedem Funkspruch im Dashboard oder Protokoll.
  - Floating Dispatcher Player am Bildschirmrand mit Scrubber, Speed (`0.8x`, `1.0x`, `1.2x`), `↺ 5s` Rücksprung, `+8 dB` Sprachverstärkung und WAV-Download.
  - **Sofort-Wiederholung des letzten Funkspruchs:** Tastenkombination `F8` oder `Alt+R`.
  - **Tastatur-Steuerung:** `Leertaste` (Pause/Play), `Pfeil links` / `J` (-5s), `Pfeil rechts` / `L` (+5s), `Escape` (Schließen).

### 4. Taktische Lagekarte, Geofencing & GPS-Tracking
- **Live-Positionen:** Automatische Dekodierung eingehender GPS-LIP-Pakete von Hytera Handfunkgeräten.
- **Taktische Zonen & Geofencing:** Zeichnen von Einsatzabschnitten, Gefahrenbereichen und Bereitstellungsräumen.
- **Automatischer Alarm:** Akustische und optische Meldung bei Zonenübertritt oder Verlassen des Einsatzbereichs.
- **Offline-Kartenserver:** Integrierte Tile-Versorgung ohne externen Internetzugang.
- **Taktische Zeichen:** BOS-konforme Symbole nach DV 102.

### 5. Funkgeräte-Sicherheit & Flotten-Schutz
- **OTA Radio Stun (Fernsperrung):** Bei Geräteverlust oder Kompromittierung kann das Funkgerät über die Luftschnittstelle (Opcode `0x0847`) per Mausklick gesperrt und wieder entsperrt werden.
- **Repeater Remote Knockdown:** Sofortige Außerbetriebnahme des Repeaters via SNMP OID `1.3.6.1.4.1.40297.1.2.2.6.0` bei anhaltender Sabotage oder schwerer Störung.
- **RRS (Radio Registration Service):** Automatische Erkennung, wenn sich Funkgeräte im Repeater einbuchen oder ausschalten.
- **Alias-Zuordnung:** Funkgeräte-IDs werden über [`ids.json`](./ids.json) mit Funkrufnamen (z. B. `10001` → `Florian 1/11`) verknüpft.

### 6. DMR-Kurznachrichten (TMP / SMS)
- Empfangen und Senden von DMR-Kurznachrichten direkt an Handfunkgeräte oder Gruppen (UTF-16LE mit BOM-Filterung).

### 7. Hardware- & Umgebungsüberwachung
- **Repeater-Telemetrie:** Echtzeitüberwachung von PA-Temperatur, Stehwellenverhältnis (VSWR), Vorwärts-/Rückwärtsleistung, Betriebsspannung und RSSI.
- **SNMP-Trap Empfänger:** UDP Port 10162 zur sofortigen Alarmierung bei Hardware-Defekten (Lüfterausfall, Antennenschaden).
- **USV-Monitoring:** Schneider / APC / PowerWalker Modbus-TCP Polling (Batteriestatus, Netzspannung, Last, Autonomiezeit).
- **Router / LTE-Monitoring:** Status des LTE-Routers (Signalstärke, Verbindungsstatus) und Omada-Netzwerks.

---

## 🌐 Netzwerk-Ports & Schnittstellen

| Port (Protokoll) | Richtung | Dienst / Funktion |
|---|---|---|
| **UDP 30001 / 30002** | Inbound | RRS (Radio Registration Service) – TS1 / TS2 |
| **UDP 30003 / 30004** | Inbound | GPS / LIP Telemetrie – TS1 / TS2 |
| **UDP 30005 / 30006** | Inbound | Repeater Telemetrie & Diagnose – TS1 / TS2 |
| **UDP 30007 / 30008** | In/Out | DMR-SMS Kurznachrichten – TS1 / TS2 |
| **UDP 30009 / 30010** | In/Out | Call Control & RCP Befehle (Call Setup, PTT, Stun) |
| **UDP 30012 / 30014** | In/Out | Audio RTP Stream (G.711 µ-law) – TS1 / TS2 |
| **UDP 161** | Outbound| SNMPv1/v2c Polling (RF-Leistung, Temperatur, VSWR) |
| **UDP 10162** | Inbound | SNMP Traps (Repeater Alarmmeldungen) |
| **TCP 502** | Outbound| Modbus TCP (USV-Status) |
| **TCP 8000** | Inbound | HTTP Webserver & WebSocket (`/ws`) |
| **TCP 8443** | Inbound | HTTPS Webserver & Secure WebSocket (`/ws`) |

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

Das System verfügt über eine vollständige Test-Suite mit **127 Unit- und Integrationstests**:

```bash
cd hytera_command_center
pytest backend/tests/test_all.py
```

Getestete Komponenten:
- UDP-Socket Lifecycle & Puffer-Initialisierung
- HSTRP Protokoll-Dekodierung & Safe Dispatcher
- RCP Rufaufbau (0x0841), Button Request (0x0041) & Checksummen
- Call Control & PTT Watchdog Timer
- Busy Channel Lockout (BCL) & Vorrangschaltung (Preemption)
- G.711 µ-law JitterBuffer, Resampling & StreamingWAVWriter
- Paralleler WebSocket-Broadcast mit Client-Fehlerisolation
- SQLite WAL-Modus & Busy-Timeout Handling
- SNMP Poller, MIB OID-Extraktion & SNMP Traps
- API-Endpunkte, SSL-Zertifikatsprüfung & Datenbereinigung
