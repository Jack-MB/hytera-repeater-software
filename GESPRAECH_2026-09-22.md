# Gesprächsprotokoll & Entwicklungsdokumentation (22.09.2026)

**Projekt:** Hytera HR1065 DMR-Repeater & Leitstellen-Software (`hytera-repeater-software`)  
**Datum:** 22. September 2026  
**Session-ID:** `bc795fbd-578e-4cfc-85de-2c47aeb210f8`  
**Repository:** [Jack-MB/hytera-repeater-software (privat)](https://github.com/Jack-MB/hytera-repeater-software)  
**Entwicklungsstatus:** 81 / 81 Unittests bestanden (100% grün)

---

## 1. Rückblick & Analyse aller vorangegangenen Konversationen

Zu Beginn der Sitzung wurden alle historischen Transkripte und Artefakte der vergangenen 4 Sessions vollständig eingelesen und strukturiert:

| # | Session-ID | Zeitraum | Kerninhalt & Meilensteine |
|---|---|---|---|
| **1** | `638c827c-c4c9-4908-a085-0726aaf049bf` | 08.09.2026 | **Entstehung der `hytera_tactical_suite`:** Überführung des Tkinter-Desktop-Dispatchers in eine Web-Architektur (FastAPI, Leaflet). Integration von Feature-Toggles, Feuerwehrlaufkarten/PDF-Overlays auf der Karte, Georeferenzierung, Demodaten-Bereinigung und Kapselung in einen portablen Ordner. |
| **2** | `8c12a33b-ece4-44e4-9fb1-211606972a37` | 09.09.2026 | **Netzwerk & Multi-User:** Multi-User-Echtzeitbetrieb im Einsatzleitwagen (ELW), Netzwerktopologie mit TP-Link Omada ER605 hinter ZTE 5G-Router, Tailscale-VPN-Anbindung, Windows-Encoding-Fix (`chcp 65001`) und responsive UI-Skalierung. |
| **3** | `1dd68ef2-e67c-4909-a155-6ed54dc5f747` | 11.09.2026 | **Kartenrotation & taktische Ausrichtung:** 360°-Kartenrotation mit Kompasssteuerung, zentrierte Drehung von Gebäudeplänen beim Georeferenzieren, automatische Gegenrotation von Marker-Labels für dauerhafte Lesbarkeit sowie Track-Up-Modus. |
| **4** | `57a052b1-52a9-4a24-a2dc-22f19597b3d9` | 17.–18.09.2026 | **Hytera Command Center & Audio-Replay:** Konsolidierung im neuen `hytera_command_center`. Protokoll-Tiefenanalyse (G.711 µ-law RTP UDP 30012/30014, kein AMBE-Stick nötig). AudioRecorder v2 (16-Bit JitterBuffer, Streaming WAV). Browser-Replay-Bar (`↺ 5s`, Geschwindigkeiten `0.8x-1.2x`, `+8 dB` Speech-Boost, Hotkey `F8`/`Alt+R`). 3-Runden-Härtung (1 MB Socket-Puffer, SQLite WAL mit Busy-Timeout) und Marker-Überarbeitung (taktische Badges, Fahrtrichtungspfeil). |

---

## 2. Erstellung des privaten GitHub-Repositories

Der Benutzer beauftragte die Erstellung eines neuen, privaten GitHub-Repositories zur dauerhaften und ortsunabhängigen Sicherung des Codes.

### Durchgeführte Schritte:
1. **Authentifizierung:**
   * Nutzung der im Windows Credential Manager hinterlegten OAuth-Anmeldedaten für Benutzer `Jack-MB` (Scopes: `gist, repo, workflow`).
2. **Repository-Erstellung:**
   * Anlegen des privaten Repositories via GitHub REST API: `https://github.com/Jack-MB/hytera-repeater-software.git`
3. **Konfiguration von [`.gitignore`](./.gitignore):**
   * Ausschluss sensibler und voluminöser Binärdaten: SQLite-Datenbanken (`*.db`, `*.sqlite`), Audioaufnahmen (`*.wav`, `rec/`, `recordings/`), heruntergeladene Kartenkacheln (`tiles_cache/`), PyInstaller- und Flutter-Build-Artefakte (`build/`, `dist/`, `.dart_tool/`) sowie temporäre Textdateien.
4. **Submodul-Auflösung:**
   * Konvertierung des historischen Ordners `hytera_project_v1_original/hylink` aus einem unvollständigen Git-Submodul in direkt nachverfolgte Quelldateien.
5. **Initialer Push:**
   * Initialisierung des Git-Branches `main`, Commit und Push aller Projektmodule.

---

## 3. LibreNMS-Recherche & Analyse der MIB-Definitionen

Auf die Frage des Nutzers nach Nutzungsmöglichkeiten des LibreNMS-Repositories wurde eine tiefgehende Recherche der offiziellen LibreNMS-Quellen durchgeführt.

### Wichtige Entdeckungen im LibreNMS-Repository:
1. **`HYTERA-REPEATER-MIB` (`mibs/hytera/HYTERA-REPEATER-MIB`):**
   * Enterprise-OID: `1.3.6.1.4.1.40297.1.2`
   * Liefert den vollständigen SNMP-Objektbaum für Hytera-Repeater:
     * Alarme (`1.3.6.1.4.1.40297.1.2.1.1.x`): Unterspannung (`.1`), Übertemperatur (`.2`), Lüfter (`.3`), Vorwärtsleistung (`.4`), Reflektierte Leistung (`.5`), **VSWR-Alarm (`.6`)**, PLL-Unlock (`.7`/`.8`), Batterie-Alarm (`.9`).
     * Telemetrie (`1.3.6.1.4.1.40297.1.2.1.2.x`): Betriebsspannung (`.1`), PA-Temperatur (`.2`), Lüfterdrehzahl (`.3`), **Stehwellenverhältnis VSWR (`.4`)**, Vorwärtsleistung Watt (`.5`), Reflektierte Leistung Watt (`.6`), TS1/TS2-RSSI (`.9`/`.10`), Stromversorgungsart DC/Batterie (`.11`), Batteriestatus (`.12`).
     * Fernsteuerung (`1.3.6.1.4.1.40297.1.2.2.x`): Remote-Reboot (`.1`), Leistungsumschaltung (`.2`), Kanalwahl (`.3`), Knockdown/Disable (`.4`).
2. **`EPPC-MIB` (`mibs/powerwalker/EPPC-MIB`):**
   * Enterprise-OID: `1.3.6.1.4.1.935.10.1.2.x`
   * SNMP-Traps für PowerWalker / BlueWalker USVs bei Netzausfall (`upsEOnBattery`), Akku fast leer (`upsEBatteryLow`) und Überlastung (`upsEOverLoad`).

---

## 4. Umsetzung von Option A: Autarke & native LibreNMS-Integration

Der Benutzer entschied sich für **Option A** (direkte, native Portierung in das Python-Backend ohne externe Server- oder PHP-Abhängigkeiten).

### Implementierte Komponenten:

1. **OID-Katalog aktualisiert ([`backend/hardware/snmp_trap.py`](./hytera_command_center/backend/hardware/snmp_trap.py)):**
   * Korrektur fehlerhafter OID-Zuordnungen (z. B. `.4` Vorwärtsleistung vs. `.6` echter VSWR-Alarm).
   * Hinzufügen der PowerWalker USV-Traps für Stromausfall und Akkuwarnungen.
2. **Neuer aktiver Poller ([`backend/hardware/snmp_poller.py`](./hytera_command_center/backend/hardware/snmp_poller.py)):**
   * Eigener, leichtgewichtiger ASN.1 BER/DER-Encoder für SNMPv2c `GetRequest-PDU` (0xA0).
   * BER-Decoder für `GetResponse-PDU` (0xA2) mit automatischer IEEE 754 4-Byte Float-Dekodierung (`struct.unpack('>f')`).
   * Asynchroner Polling-Loop (30s-Intervall) zur Erfassung von PA-Temperatur, Stehwelle, Sendeleistung, Spannung und RSSI.
   * Dynamische Schwellenwert-Logik:
     * **VSWR:** Warnung ab `>= 2.0`, Kritisch/Alarm ab `>= 2.8` (Schutz der Senderendstufe vor Antennenschaden).
     * **PA-Temperatur:** Warnung ab `>= 60.0 °C`, Kritisch ab `>= 75.0 °C`.
     * **Betriebsspannung:** Warnung bei Unterspannung `< 12.0 V` oder Überspannung `> 15.2 V`.
3. **Backend-Integration & API ([`backend/main.py`](./hytera_command_center/backend/main.py)):**
   * Aggregation von Trap-Monitor und aktivem Poller in `get_repeater_full_state()`.
   * Live-Streaming aller Telemetriewerte via WebSocket-Event `repeater_update` direkt in die Web-UI.
   * Bereitstellung der Daten über REST: `/api/status`, `/api/hardware/status` sowie On-Demand-Trigger `POST /api/hardware/repeater/poll`.
4. **Konfiguration ([`backend/config.py`](./hytera_command_center/backend/config.py)):**
   * `SNMP_PORT = 161`, `SNMP_COMMUNITY = "public"`, `SNMP_POLL_INTERVAL_S = 30` und Warnschwellen hinterlegt.
5. **Test-Suite erweitert ([`backend/tests/test_all.py`](./hytera_command_center/backend/tests/test_all.py)):**
   * 5 neue Tests in `TestSNMPPollerAndMIB` für Encoding, Float-Parsing, OID-Vollständigkeit, Schwellenwerte und REST-Endpoints.
   * **81 / 81 Tests erfolgreich bestanden.**

---

## 5. Synchronisations-Status

* Alle Änderungen wurden erfolgreich per Git committet und zu GitHub übertragen:
  * Commit `5a6ac70`: Initial commit
  * Commit `921ec40`: Track hylink source files directly
  * Commit `76d321d`: Integrate LibreNMS MIBs: native SNMP poller, live RF telemetry & VSWR alarms
* Das Arbeitsverzeichnis ist sauber (`working tree clean`).
