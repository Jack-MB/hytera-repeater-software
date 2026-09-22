# 📡 Hytera Tactical Suite (Lagezentrum & Funkführung)

Vollständiges, autarkes Softwaresystem für das Tracking, die Vorfallserfassung und die Funkführung von DMR-Funkgeräten am **Hytera HR1065 DMR-Repeater**.

---

## 🚀 Portabilität: Auf einen anderen PC übertragen

Dieser gesamte Ordner **`hytera_tactical_suite`** ist **100 % in sich geschlossen und autark**:
1. Kopieren Sie einfach den Ordner `hytera_tactical_suite` auf einen USB-Stick oder direkt auf den Ziel-PC (z. B. Laptop im Einsatzleitwagen / ELW).
2. Starten Sie das System per Doppelklick auf:
   - **`START_SUITE.bat`** (oder im übergeordneten Ordner auf `START_TACTICAL_SUITE.bat`)
3. Der Standard-Browser öffnet sich automatisch unter:
   - **Hauptoberfläche:** `http://localhost:8000`
   - **Druckfertiger Einsatzbericht:** `http://localhost:8000/report`

---

## 🛠️ Enthaltene Funktionen

### 1. 🚨 DMR Notruf- & Totmann-Management (Emergency / Man-Down)
- Automatische Erkennung von Hytera Emergency-Paketen (Notruftaste / Lagesensor).
- Vollflächiges Notruf-Warnbanner mit Stoppuhr und Einheitennamen.
- Akustische Sirene via Web Audio API.
- Auto-Focus auf das verunfallte Funkgerät mit rotem Notruf-Pulsring.
- 1-Klick Quittierung durch den Disponenten.

### 2. 🏢 Multi-Plan & Stockwerks-Verwaltung (Indoor-Pläne)
- Schneller Etagen-Umschalter (`Außen`, `UG`, `EG`, `1. OG`, `2. OG`).
- Verwaltung mehrerer georeferenzierter Pläne (PDF/Bilder) pro Stockwerk.
- Zuweisung von Funkgeräten zu Stockwerken direkt über das Einheiten-Popup.

### 3. 📶 RSSI-Funkabdeckungsanalyse & Heatmap (HF-Diagnose)
- Live-Pegelanzeige (dBm) direkt am Marker jeder Einheit.
- Zuschaltbare HF-Funkabdeckungs-Heatmap zur sofortigen Identifikation von Funklöchern.
- Farbcodierte Bewegungsspuren je nach Empfangsqualität.

### 4. 📐 Taktische Zeichen (DV 102), Distanzmessung & Geofencing
- Offizielle BOS-Symbole (ELW, HLF, RTW, Sammelplatz, Brand, Gefahrgut, Sperre, Bereitstellung).
- Integriertes Lineal-Werkzeug (`📏 MESSEN`) zur Distanz- und Wegmessung auf der Karte.
- Geofencing: Definition von Gefahrenbereichen mit automatischem Warnton bei Betreten.

### 5. 📄 1-Klick Einsatzbericht (Druck- & PDF-Export)
- Unter `/report` abrufbarer, nach BOS-Richtlinien formatierter Lagebericht.
- Enthält Vorfallstabelle, Funkstellenliste, chronologisches Funkprotokoll und Notstromstatus.
- Direkt als A4-PDF druck- und archivierbar.

### 6. 📴 Offline-Karten-Cache (Autarkie bei Netzausfall)
- Integrierter lokaler Kachel-Proxy (`/api/tiles/{z}/{x}/{y}.png`).
- Kacheln werden auf der Festplatte gespeichert (`static/tiles_cache/`).
- 1-Klick Pre-Cache-Funktion für das Einsatzgebiet.

### 7. 🔊 Funkspruch-Audioaufnahme & Wiedergabe (Voice-Log)
- Automatische Archivierung und Synthese von Funksprüchen als WAV-Dateien.
- Play-Button `▶️` im schwebenden Ticker (`🎙️ LETZTE FUNKSPRÜCHE`) zum sofortigen Nachhören.

### 8. 🗺️ Flexible Basiskarten (Google Maps & API-Key Unterstützung)
- Umschaltbar zwischen:
  - **OpenStreetMap** (lokaler Offline-Cache)
  - **Google Maps** (Straßenkarte, Satellit, Hybrid, Gelände)
  - **Mapbox** Satellit
- Optionaler Google Maps API-Key direkt in den `⚙️ OPTIONEN` eintragbar (wird dauerhaft in SQLite gespeichert).
- Schnellauswahl über die Schaltfläche **`🗺️ EBENEN & FILTER`** in der Menüleiste.

### 9. 🟢 Standard-Echtbetrieb (Live DMR)
- Das System startet standardmäßig im **Echtbetrieb** und empfängt Hytera DMR-Daten auf UDP 30003.
- Vollständig bereinigt: **Keine störenden Demo-Einheiten oder Dummy-Vorfälle**.
- Für Schulungen oder Tests kann der Simulationsmodus bei Bedarf mit `--mock` oder im Optionen-Menü zugeschaltet werden.

### 10. 📻 Funkgeräte-Flottenverwaltung & Einsatz-Manager
- **100 % unabhängig vom alten Desktop-Dispatcher:** Funkgeräte (DMR-ID, Alias/Name, Modell, GPS ja/nein, Standardetage) können direkt im Web-Interface unter `➕ GERÄTE` angelegt, bearbeitet und gelöscht werden.
- **Automatische Synchronisation:** Jede Änderung wird in der SQLite-Datenbank persistiert und automatisch nach `ids.json` exportiert.
- **Einsatz-Stammdaten (`📋 EINSATZ`):** Erfassung von Einsatzstichwort, Einsatz-Nr., Einsatzleiter, Einsatzort, Funkkanal und Lagebeurteilung/Notizen – fließt live in den Druckbericht ein.

### 11. 🧹 Saubere Rücksetzung auf Nullpunkt (Clean Reset)
- Das System kann jederzeit mit einem einzigen Befehl wieder in den absoluten Auslieferungszustand (ohne Vorfälle, ohne Funkgeräte, ohne Logs) zurückgesetzt werden:
  ```bash
  python clean_database.py
  ```

### 12. 🌐 Fernzugriff & Multi-User via Tailscale VPN (Überwindet LTE-CGNAT & Omada-Doppel-NAT)
- **Problem:** Wenn der Omada-Router hinter einem LTE-Router (z. B. ZTE Speedbox) hängt, blockieren Mobilfunk-CGNAT und Doppel-NAT traditionelle Portweiterleitungen.
- **Lösung:** Tailscale baut eine direkte, ausgehende WireGuard-Verbindung auf (`100.x.x.x`).
- **Funktionen der Suite:**
  - Automatische Erkennung der Tailscale-IP beim Start und im laufenden Betrieb.
  - Direkte Anzeige des externen Links im Konsolen-Banner und in der Menüleiste (`🌐 VPN: 100.x.x.x`).
  - 1-Klick-Kopierfunktion für den externen Dashboard-Link.
- **Subnet-Routing (Omada-Switch & Repeater aus der Ferne erreichbar machen):**
  Führen Sie einmalig in einer Administrator-PowerShell auf dem Host-PC aus:
  ```bash
  tailscale up --advertise-routes=192.168.0.0/24
  ```
  Im Tailscale-Adminpanel die Route freigeben. Externe Disponenten können nun nicht nur das Lagezentrum (`:8000`) aufrufen, sondern auch direkt die Weboberflächen des **Omada ER605** (`http://192.168.0.1`) und des **Hytera Repeaters** (`http://192.168.0.230`) erreichen.

---

## ⌨️ Tastatur-Shortcuts für Disponenten

- `Leertaste`: Karte auf Einsatzgebiet zentrieren
- `N`: Neuen Vorfallsmarker erfassen
- `L`: Sofort zum Live-Stream zurückkehren
- `S`: Seitenleiste ein-/ausklappen
- `T`: Timeline ein-/ausklappen
- `Esc`: Dialoge, Messwerkzeug oder Kalibrierung schließen

---

## 📦 Systemvoraussetzungen

- Python 3.11 oder neuer
- Installierte Pakete (in `requirements.txt` enthalten):
  ```bash
  pip install -r requirements.txt
  ```
- Unterstützte Betriebssysteme: Windows 10/11, Linux, macOS.
