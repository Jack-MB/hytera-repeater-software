# Hytera DMR Protocol & Network Application Interface (NAI) Specification
## Reverse-Engineered Deep-Technical Protocol Reference

> **Dokumentenversion:** 2.4.0 (Stand: September 2026)  
> **Ziel-Hardware:** Hytera HR1065 / HR655 (H-Serie) & RD985 DMR Tier II / Tier III Repeater  
> **Schnittstelle:** Ethernet Network Application Interface (NAI) via UDP / IP  
> **Status:** Vollständig reverse-engineered, implementiert und mit 127/127 Unittests verifiziert.

---

## Inhaltsverzeichnis

1. [Architektur & Netzwerk-Topologie](#1-architektur--netzwerk-topologie)
2. [UDP-Port-Zuordnung (NAI Port Map)](#2-udp-port-zuordnung-nai-port-map)
3. [HSTRP – Hytera Site Trunking Repeater Protocol](#3-hstrp--hytera-site-trunking-repeater-protocol)
   - [3.1 Paketstruktur & 12-Byte Header](#31-paketstruktur--12-byte-header)
   - [3.2 Verbindungs-Handshake & Keepalive](#32-verbindungs-handshake--keepalive)
   - [3.3 Flusssteuerung & Quittierung (ACK)](#33-flusssteuerung--quittierung-ack)
4. [RCP – Radio Control Protocol](#4-rcp--radio-control-protocol)
   - [4.1 RCP-Framing & Prüfsummen-Algorithmus](#41-rcp-framing--prüfsummen-algorithmus)
   - [4.2 Opcode 0x0841: Call Setup (Rufaufbau)](#42-opcode-0x0841-call-setup-rufaufbau)
   - [4.3 Opcode 0x0041: Button Request (PTT Taste Drücken/Loslassen)](#43-opcode-0x0041-button-request-ptt-taste-drückenloslassen)
   - [4.4 Opcode 0x0847: OTA Stun & Revive (Funkgeräte-Sperrung)](#44-opcode-0x0847-ota-stun--revive-funkgeräte-sperrung)
5. [Call Control (Rufüberwachung & Status)](#5-call-control-rufüberwachung--status)
   - [5.1 Opcode 0x0004: HR1065 NAI Call-Status](#51-opcode-0x0004-hr1065-nai-call-status)
   - [5.2 Opcode 0xB845: Repeater Broadcast TX-Status & Echter RSSI](#52-opcode-0xb845-repeater-broadcast-tx-status--echter-rssi)
   - [5.3 Opcode 0xB843: Legacy Broadcast Status](#53-opcode-0xb843-legacy-broadcast-status)
   - [5.4 Watchdog- & Debounce-Zeitglieder](#54-watchdog--debounce-zeitglieder)
6. [DMR Audio-Streaming (RTP & Codecs)](#6-dmr-audio-streaming-rtp--codecs)
   - [6.1 RTP-Header & Hytera Extension Profile](#61-rtp-header--hytera-extension-profile)
   - [6.2 ITU-T G.711 µ-law Nutzlast & Framing](#62-itu-t-g711-µ-law-nutzlast--framing)
   - [6.3 Resampling-Pipeline (48 kHz / 44.1 kHz → 8.000 Hz)](#63-resampling-pipeline-48-khz--441-khz--8000-hz)
   - [6.4 JitterBuffer v2: Sequenz-Reordering & Duplikat-Filterung](#64-jitterbuffer-v2-sequenz-reordering--duplikat-filterung)
7. [RRS – Radio Registration Service](#7-rrs--radio-registration-service)
   - [7.1 Registrierungs-Pakete (Online / Offline / Query)](#71-registrierungs-pakete-online--offline--query)
   - [7.2 Heuristische Radio-ID-Extraktion](#72-heuristische-radio-id-extraktion)
8. [GNSS / LIP – Location Information Protocol](#8-gnss--lip--location-information-protocol)
   - [8.1 GPS-Paketaufbau & Skalierungsfaktoren](#81-gps-paketaufbau--skalierungsfaktoren)
   - [8.2 Koordinatenberechnung & Telemetrie-Validierung](#82-koordinatenberechnung--telemetrie-validierung)
9. [TMP – Text Message Protocol (DMR-SMS)](#9-tmp--text-message-protocol-dmr-sms)
   - [9.1 Einzel- & Gruppen-SMS Nachrichtenaufbau](#91-einzel--gruppen-sms-nachrichtenaufbau)
   - [9.2 Text-Encoding & BOM-Bereinigung (UTF-16LE)](#92-text-encoding--bom-bereinigung-utf-16le)
10. [SNMP Telemetrie & MIB-Katalog](#10-snmp-telemetrie--mib-katalog)
    - [10.1 Hytera Private Enterprise MIB (1.3.6.1.4.1.40297)](#101-hytera-private-enterprise-mib-13614140297)
    - [10.2 Repeater Remote Knockdown (Repeating State OID)](#102-repeater-remote-knockdown-repeating-state-oid)
    - [10.3 SNMP Traps (Port 10162)](#103-snmp-traps-port-10162)
11. [Busy Channel Lockout (BCL) & Vorrangschaltung (Preemption)](#11-busy-channel-lockout-bcl--vorrangschaltung-preemption)
12. [Browser Web-PTT & AudioWorklet-Verschlüsselung](#12-browser-web-ptt--audioworklet-verschlüsselung)

---

## 1. Architektur & Netzwerk-Topologie

Der Hytera DMR-Repeater (HR1065 / RD985) stellt über seine rückseitige Ethernet-Schnittstelle das **NAI (Network Application Interface)** bereit. Sämtliche Nutzdaten (Sprache, GPS, SMS) und Steuersignale (PTT, Registrierung, Call-Status) werden über unverschlüsselte oder proprietär gekapselte UDP-Pakete ausgetauscht.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Hytera HR1065 Repeater                          │
│                   IP: 192.168.0.230 (Standard)                         │
└───────────────┬───────────────────────────────┬────────────────────────┘
                │ UDP Ports 30001–30014         │ SNMP (Port 161 / 10162)
                │ (HSTRP / RTP Streams)         │ (Telemetrie & Traps)
┌───────────────▼───────────────────────────────▼────────────────────────┐
│               Hytera Command Center (Backend Core)                     │
│                                                                        │
│   ┌─────────────────┐   ┌──────────────────┐   ┌───────────────────┐   │
│   │  HSTRP Manager  │   │ CallControlParser│   │ AudioRecorder v2  │   │
│   │  (UDP Multiplex)│   │ (PTT & BCL State)│   │ (G.711 / Jitter)  │   │
│   └────────┬────────┘   └────────┬─────────┘   └─────────┬─────────┘   │
│            │                     │                       │             │
│   ┌────────▼─────────────────────▼───────────────────────▼─────────┐   │
│   │           IP TX Transmitter (Web-PTT & Remote RCP)             │   │
│   └──────────────────────────────┬─────────────────────────────────┘   │
└──────────────────────────────────┼─────────────────────────────────────┘
                                   │ HTTPS / WSS (Port 8443)
┌──────────────────────────────────▼─────────────────────────────────────┐
│                 Leitstellen-Arbeitsplatz & Tablets                     │
│              Browser Web Audio API + AudioWorklet (PTT)                │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. UDP-Port-Zuordnung (NAI Port Map)

Die Übertragung erfolgt kanal- und zeitschlitzgetrennt. Der Repeater ordnet jedem DMR-Zeitschlitz (Timeslot 1 und Timeslot 2) dedizierte UDP-Ports zu:

| Port (UDP) | Zeitschlitz | Dienst | Beschreibung |
|:---|:---:|:---|:---|
| **30001** | TS1 | `RRS_TS1` | Radio Registration Service (An-/Abmeldung) |
| **30002** | TS2 | `RRS_TS2` | Radio Registration Service (An-/Abmeldung) |
| **30003** | TS1 | `GPS_TS1` | GPS / LIP Positions- und Geschwindigkeitsdaten |
| **30004** | TS2 | `GPS_TS2` | GPS / LIP Positions- und Geschwindigkeitsdaten |
| **30005** | TS1 | `TELE_TS1` | Telemetrie, digitale & analoge Repeater I/O |
| **30006** | TS2 | `TELE_TS2` | Telemetrie, digitale & analoge Repeater I/O |
| **30007** | TS1 | `SMS_TS1` | TMP Text Message Protocol (Kurznachrichten) |
| **30008** | TS2 | `SMS_TS2` | TMP Text Message Protocol (Kurznachrichten) |
| **30009** | TS1 | `CC_TS1` | Call Control (Rufstatus, PTT-Start, PTT-Ende) |
| **30010** | TS2 | `CC_TS2` | Call Control (Rufstatus, PTT-Start, PTT-Ende) |
| **30012** | TS1 | `AUDIO_TS1`| RTP Audio-Stream (ITU-T G.711 µ-law, 8.000 Hz) |
| **30014** | TS2 | `AUDIO_TS2`| RTP Audio-Stream (ITU-T G.711 µ-law, 8.000 Hz) |
| **161**   | —   | `SNMP` | SNMPv1/v2c Polling (RF-Leistung, Temperatur, VSWR) |
| **10162** | —   | `SNMP_TRAP`| Asynchrone Alarmmeldungen des Repeaters |

---

## 3. HSTRP – Hytera Site Trunking Repeater Protocol

Alle Kontroll- und Nutzdatenpakete (mit Ausnahme von reinem RTP-Audio) sind durch einen einheitlichen **12-Byte HSTRP-Header** gekapselt.

### 3.1 Paketstruktur & 12-Byte Header

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                 Signature (0x32 0x42 0x00)    |  Pkt Type     |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|       Reserved (0x0000)       |   Sequence Number (uint16 LE) |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|   Payload Length (uint16 LE)  |      Opcode (uint16 LE)       |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                       Payload (N Bytes)                       |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

#### Byte-Aufschlüsselung:

| Offset | Länge | Feld | Format | Beschreibung |
|:---:|:---:|:---|:---:|:---|
| **0–2** | 3 Bytes | `Signature` | Byte-Array | Feste Kennung `0x32 0x42 0x00` (`b'\x32\x42\x00'`). |
| **3** | 1 Byte | `Type` | uint8 | Pakettyp (siehe unten). |
| **4–5** | 2 Bytes | `Reserved` | uint16 LE | Immer `0x0000`. |
| **6–7** | 2 Bytes | `Sequence` | uint16 LE | Fortlaufende Sequenznummer (oder quittierte Seq bei ACK). |
| **8–9** | 2 Bytes | `Length` | uint16 LE | Länge der Nutzdaten nach dem 12-Byte Header. |
| **10–11**| 2 Bytes | `Opcode` | uint16 LE | RCP-Opcode (nur bei `FROM_RADIO` oder `TO_RADIO`). |

#### HSTRP Paket-Typen:

- `0x24` (**HSTRP_SYN**): Verbindungsaufbau-Anfrage vom Repeater an den Leitstellen-PC.
- `0x05` (**HSTRP_SYNACK**): Verbindungsbestätigung vom PC an den Repeater.
- `0x02` (**HSTRP_HEARTBEAT**): Keepalive-Signal (zyklisch alle ca. 30 Sekunden).
- `0x01` (**HSTRP_ACK**): Quittierung eines empfangenen Datenpakets.
- `0x20` (**HSTRP_FROM_RADIO**): Eingehende Daten vom Repeater / Funkteilnehmer.
- `0x00` (**HSTRP_TO_RADIO**): Ausgehende Steuerbefehle / Rufe an Funkgeräte.

### 3.2 Verbindungs-Handshake & Keepalive

```
Repeater (HR1065)                          Leitstellen-Server (Command Center)
       │                                                   │
       │─────── HSTRP_SYN (Typ 0x24, Seq N) ──────────────>│
       │<────── HSTRP_SYNACK (Typ 0x05, Seq N) ────────────│  [Verbindung steht]
       │                                                   │
       │====== Normaler Betrieb / Datenverkehr ============│
       │                                                   │
       │─────── HSTRP_HEARTBEAT (Typ 0x02) ───────────────>│
       │<────── HSTRP_HEARTBEAT (Typ 0x02) ────────────────│  [Alle ~30 Sekunden]
       │                                                   │
```

### 3.3 Flusssteuerung & Quittierung (ACK)

Sobald ein `HSTRP_FROM_RADIO` Paket (Typ `0x20`) am Leitstellen-PC eingeht, muss **unverzüglich** ein `HSTRP_ACK` (Typ `0x01`) zurückgesendet werden, wobei das Sequenzfeld exakt der Sequenznummer des Originalpakets entsprechen muss:

```python
def build_ack(seq: int) -> bytes:
    # 12-Byte HSTRP ACK Paket
    return struct.pack('<3sBHHH', b'\x32\x42\x00', 0x01, 0x0000, seq & 0xFFFF, 0x0000) + b'\x00\x00'
```

---

## 4. RCP – Radio Control Protocol

Ausgehende Steuerbefehle an den Repeater (z. B. Rufaufbau, PTT-Drücken, Funkgeräte-Sperre) verwenden das eingebettete **Radio Control Protocol (RCP)**.

### 4.1 RCP-Framing & Prüfsummen-Algorithmus

Ein RCP-Block wird in ein `HSTRP_TO_RADIO` Paket gekapselt:
```
[HSTRP TO_RADIO Header: 6 Bytes]
  0x32 0x42 0x00   (Signatur)
  0x00             (HSTRP_TO_RADIO)
  <Sequence: 2B BE>
[RCP Nachricht]
  0x02             (RCP Message Header)
  <Opcode: 2B LE>  (RCP Befehl)
  <Length: 2B LE>  (Länge der RCP-Nutzdaten)
  <Payload: N Bytes>
  <Checksum: 1 Byte>
  0x03             (RCP Trailer)
```

#### Hytera RCP Prüfsummen-Berechnung:
Die Prüfsumme sichert die Header-Bytes 1 bis 4 (Opcode + Length) sowie die gesamte Payload ab:
$$\text{Checksum} = \left(\sim \left(\sum \text{Header}[1:5] + \sum \text{Payload}\right) + 0x33\right) \ \& \ 0xFF$$

```python
def calculate_rcp_checksum(header_1_to_5: bytes, payload: bytes) -> int:
    total = sum(header_1_to_5) + sum(payload)
    return (~total + 0x33) & 0xFF
```

### 4.2 Opcode 0x0841: Call Setup (Rufaufbau)

Startet einen DMR-Rufkanal auf dem Repeater, bevor Audiodaten gesendet werden.

- **Port:** UDP 30009 (TS1) oder 30010 (TS2)
- **Opcode:** `0x0841` (Little-Endian: `0x41 0x08`)
- **Payload-Länge:** 5 Bytes

| Byte-Offset | Feld | Format | Beschreibung |
|:---:|:---|:---:|:---|
| **0** | `call_type` | uint8 | `0x00` = Privatruf<br>`0x01` = Gruppenruf<br>`0x02` = All-Call / Sammelruf |
| **1–4** | `dest_id` | uint32 LE | Ziel-DMR-ID (z. B. `16777215` für All-Call) |

### 4.3 Opcode 0x0041: Button Request (PTT Taste Drücken/Loslassen)

Simuliert das physische Betätigen der Sprechtaste (PTT) auf dem Funkgerät bzw. Repeater.

- **Opcode:** `0x0041` (Little-Endian: `0x41 0x00`)
- **Payload-Länge:** 2 Bytes

| Byte-Offset | Feld | Wert | Beschreibung |
|:---:|:---|:---:|:---|
| **0** | `target` | `0x03` | Front-PTT (`BUTTON_TARGET_FRONT_PTT`)<br>(Alternativ `0x1E` für Rückseiten-PTT) |
| **1** | `operation` | `0x01`<br>`0x00` | PTT drücken (`BUTTON_OP_PRESS`)<br>PTT loslassen (`BUTTON_OP_RELEASE`) |

### 4.4 Opcode 0x0847: OTA Stun & Revive (Funkgeräte-Sperrung)

Ermöglicht der Leitstelle das ferngesteuerte Deaktivieren eines kompromittierten oder gestohlenen Funkgeräts über die Luftschnittstelle (Over-The-Air Stun):

- **Opcode:** `0x0847` (Little-Endian: `0x47 0x08`)
- **Payload-Länge:** 5 Bytes

| Byte-Offset | Feld | Format | Beschreibung |
|:---:|:---|:---:|:---|
| **0** | `mode` | uint8 | `0x01` = Radio Stun (Sperren)<br>`0x00` = Radio Revive (Wiederbeleben) |
| **1–4** | `dest_id` | uint32 LE | DMR-ID des Zielgeräts |

---

## 5. Call Control (Rufüberwachung & Status)

Call-Control-Pakete informieren die Leitstelle in Echtzeit über Anrufe, PTT-Aktivierungen und Empfangsqualität.

### 5.1 Opcode 0x0004: HR1065 NAI Call-Status

Primäres Ereignispaket des HR1065 auf Port 30009 / 30010 (Mindestlänge: 31 Bytes).

```
Byte 0                        Byte 8  Byte 9        Byte 16             Byte 26     Byte 30
+-----------------------------+-------+-------+-----+-------+-----+-----+-----------+-------+
| ... HSTRP / RCP Header ...  | Slot  | CallT | ... | State | ... | ... | Sender-ID | Qual  |
+-----------------------------+-------+-------+-----+-------+-----+-----+-----------+-------+
```

| Offset | Feld | Typ | Werte & Bedeutung |
|:---:|:---|:---:|:---|
| **8** | `Timeslot` | uint8 | `0x01` = Timeslot 1 (TS1)<br>`0x02` = Timeslot 2 (TS2) |
| **9** | `Call-Type` | uint8 | `0x01` = Privatruf<br>`0x02` = Gruppenruf<br>`0x03` = All-Call<br>`0x04` = **Notruf (Emergency)**<br>`0x05` = Broadcast |
| **16** | `Call-State` | uint8 | `0x01`–`0x04` = Ruf aktiv / PTT gedrückt<br>`0x05` oder `0x00` = Ruf beendet / PTT losgelassen |
| **26–29** | `Sender-ID` | uint32 LE | Echte 24-Bit DMR-ID des Funkgeräts |
| **30** | `Quality` | uint8 | Verbindungsqualität `0` bis `10`. Dient als RSSI-Schätzung: $\text{RSSI} \approx -115 + (\text{Quality} \times 6)\,\text{dBm}$ |

> **Wichtiger Implementierungshinweis (Bug-Fix B-01):**  
> `is_emergency` ist **ausschließlich** dann `True`, wenn `Call-Type == 0x04`. Das Status-Byte `0x16` meldet bei `0x04` lediglich einen aktiven Ruf, **keinen** Notruf!

### 5.2 Opcode 0xB845: Repeater Broadcast TX-Status & Echter RSSI

Enthält die exakt kalibrierte Empfangsfeldstärke des Repeaters.

| Offset | Feld | Format | Beschreibung |
|:---:|:---|:---:|:---|
| **0–3** | `Radio-IP` | uint32 BE | Radio-ID = `Radio-IP & 0x00FFFFFF` |
| **4–5** | `TX-Status` | uint16 BE | `1` = Sender aktiv, `0` = Sender inaktiv |
| **6–7** | `RSSI Raw` | **signed int16 BE** | **Echter RSSI-Wert in dBm:** $\text{RSSI} = \frac{\text{RSSI Raw}}{-2.0}$ |
| **8–11**| `Call-ID` | uint32 BE | Eindeutige Anruf-Kennung |
| **12** | `Slot` | uint8 | `0x01` = TS1, `0x02` = TS2 |
| **13** | `Call-Type` | uint8 | Anruftyp (Standard: `0x02` Gruppe) |

### 5.3 Opcode 0xB843: Legacy Broadcast Status
Ältere RD985 Firmware-Variante des Broadcast TX-Status mit identischer RSSI-Skalierung, jedoch abweichendem Header-Versatz.

### 5.4 Watchdog- & Debounce-Zeitglieder
- **PTT-Watchdog (5.0s):** Wenn das Funkgerät das Ende-Paket verschluckt (z. B. Funkabriss), erzwingt der Watchdog automatisch ein `ptt_end` Event.
- **PTT-Debounce (0.6s):** Filtert Tastenprellen und Doppelaussendungen bei schlechter Verbindung.

---

## 6. DMR Audio-Streaming (RTP & Codecs)

Die Sprachübertragung erfolgt unkomprimiert über Real-Time Protocol (RTP) Pakete auf Port 30012 (TS1) und Port 30014 (TS2).

### 6.1 RTP-Header & Hytera Extension Profile

Jedes Audiopaket besteht aus:
1. **12-Byte Standard RTP Header** (RFC 3550) mit gesetztem Extension-Bit (`X=1`).
2. **16-Byte Hytera Header Extension**.
3. **160-Byte G.711 µ-law Nutzlast**.

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|V=2|P|X=1|  CC |M|     PT=0    |       Sequence Number         |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                           Timestamp                           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|           Synchronization Source (SSRC) identifier            |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|      Profile ID (0x0015)      |        Length = 3 (12 Bytes)  |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|        Hytera Extension Data (12 Bytes: DMR Radio-ID)         |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|          G.711 µ-law Audio Payload (exakt 160 Bytes)          |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

### 6.2 ITU-T G.711 µ-law Nutzlast & Framing
- **Abtastrate:** 8.000 Hz Mono
- **Frame-Dauer:** 20 Millisekunden
- **Samples pro Frame:** 160 Samples
- **Auflösung:** 8-Bit logarithmisches Companding (µ-law / PCMU)
- **Bitrate:** 64 kbit/s

### 6.3 Resampling-Pipeline (48 kHz / 44.1 kHz → 8.000 Hz)
Für das Senden von Sprache aus modernen Web-Browsern (Web Audio API arbeitet typischerweise mit 44.100 Hz oder 48.000 Hz) ist eine Resampling-Pipeline integriert:
1. Browser sendet Float32 PCM Audiodaten via Secure WebSocket (`/ws/audio_tx`).
2. Konvertierung nach 16-Bit Signed Integer PCM.
3. Resampling auf 8.000 Hz via `audioop.ratecv` (mit linearem Fallback).
4. Companding nach ITU-T G.711 µ-law (Bias 0x84, 8 Segmente).
5. Kapselung in 160-Byte RTP-Frames im 20ms-Taktgeber.

### 6.4 JitterBuffer v2: Sequenz-Reordering & Duplikat-Filterung
Bei UDP-Übertragung im WLAN/LTE-Netz können Pakete vertauscht eintreffen:
- **16-Bit Sequence Reordering:** Rechnet Wrap-Arounds (`65535 → 0`) korrekt ein.
- **Duplikat-Erkennung:** Verwirft identische Pakete automatisch.
- **Streaming-WAV-Writer:** Schreibt Audiodaten direkt als atomare `.part` Datei mit kontinuierlichem Header-Update – garantiert absturzsichere Aufnahmen.

---

## 7. RRS – Radio Registration Service

Über den Radio Registration Service melden sich Handfunkgeräte beim Ein- und Ausschalten automatisch am Repeater an bzw. ab.

### 7.1 Registrierungs-Pakete (Online / Offline / Query)
- **Ports:** UDP 30001 (TS1) und 30002 (TS2)
- **Opcodes:**
  - `0x0003` = **RRS_REGISTER** (Funkgerät schaltet ein / betritt Zelle)
  - `0x0001` = **RRS_DEREGISTER** (Funkgerät schaltet aus / verlässt Zelle)
  - `0x0002` = **RRS_QUERY** (Statusabfrage)

### 7.2 Heuristische Radio-ID-Extraktion
Aufgrund herstellerspezifischer Firmware-Unterschiede codiert Hytera die DMR-ID in variierenden Offsets. Der Parser analysiert die Offsets 0, 2, 4, 6 und 8 sowohl in Big-Endian als auch Little-Endian auf gültige DMR-IDs ($1000 \le \text{ID} \le 16776415$).

---

## 8. GNSS / LIP – Location Information Protocol

Übermittlung von GPS-Positionsdaten durch Funkgeräte mit integriertem GNSS-Empfänger.

- **Ports:** UDP 30003 (TS1) und 30004 (TS2)
- **Opcode:** `0x0001` (oder portbasiert)

### 8.1 GPS-Paketaufbau & Skalierungsfaktoren

| Offset | Feld | Format | Skalierung / Formel |
|:---:|:---|:---:|:---|
| **0–3** | `Radio-IP` | uint32 BE | DMR-ID = `Radio-IP & 0x00FFFFFF` |
| **4–7** | `Latitude Raw` | signed int32 BE | $\text{Breite (Dezimalgrad)} = \frac{\text{Latitude Raw}}{10.000.000}$ |
| **8–11**| `Longitude Raw`| signed int32 BE | $\text{Länge (Dezimalgrad)} = \frac{\text{Longitude Raw}}{10.000.000}$ |
| **12–13**| `Speed` | uint16 BE | $\text{Geschwindigkeit} = \frac{\text{Speed}}{10.0}\,\text{km/h}$ |
| **14–15**| `Heading` | uint16 BE | Kurs $0^\circ$ bis $360^\circ$ |
| **16** | `GPS-Fix` | uint8 | Bit 0: `1` = Gültiger Fix, `0` = Ungültig |
| **17** | `HDOP` | uint8 | $\text{HDOP} = \frac{\text{Wert}}{10.0}$ |
| **18** | `Satelliten` | uint8 | Anzahl getrackter Satelliten |

### 8.2 Koordinatenberechnung & Telemetrie-Validierung
Ungültige Fixes (`lat == 0.0` und `lon == 0.0`, "Null Island") sowie Koordinaten außerhalb $-90^\circ \le \text{Lat} \le 90^\circ$ bzw. $-180^\circ \le \text{Lon} \le 180^\circ$ werden automatisch verworfen.

---

## 9. TMP – Text Message Protocol (DMR-SMS)

Hytera DMR-Kurznachrichten unterstützen Textübertragung an Einzelteilnehmer und Gesprächsgruppen.

- **Ports:** UDP 30007 (TS1) und 30008 (TS2)
- **Opcodes:**
  - `0x00A1` = TMP Einzelnachricht mit Bestätigung (ACK)
  - `0x80A1` = TMP Einzelnachricht ohne Bestätigung
  - `0x00B1` = TMP Gruppennachricht

### 9.1 Einzel- & Gruppen-SMS Nachrichtenaufbau

| Offset | Feld | Format | Beschreibung |
|:---:|:---|:---:|:---|
| **0–3** | `Sender-ID` | uint32 BE | DMR-ID des Absenders |
| **4–7** | `Target-ID` | uint32 BE | DMR-ID des Empfängers (`0` bei Gruppe) |
| **8–9** | `Group-ID` | uint16 BE | Zielgruppen-ID |
| **10** | `Encoding` | uint8 | `0x00` = ASCII, `0x01` / `0x04` = Unicode UTF-16LE |
| **11** | `Msg-Length`| uint8 | Länge des Texts in **Zeichen** (nicht Bytes!) |
| **12+** | `Text` | UTF-16LE | Nutztext (2 Bytes pro Zeichen) |

### 9.2 Text-Encoding & BOM-Bereinigung (UTF-16LE)
Der Parser erkennt Byte-Order-Marks (`0xFF 0xFE` oder `0xFE 0xFF`), entfernt diese vor dem Dekodieren und fällt bei ungültigem UTF-16 tolerant auf UTF-8 bzw. Latin-1 zurück.

---

## 10. SNMP Telemetrie & MIB-Katalog

Umfassendes Hardware-Monitoring des Repeaters über SNMPv1/v2c (Port 161) und asynchrone SNMP-Traps (Port 10162).

### 10.1 Hytera Private Enterprise MIB (`1.3.6.1.4.1.40297`)

| OID | Name | SNMP-Typ | Einheit / Bedeutung |
|:---|:---|:---:|:---|
| `.1.2.1.2.1.0` | `rptVoltage` | OCTET STRING | Betriebsspannung in Volt (z. B. `"13.8"`) |
| `.1.2.1.2.2.0` | `rptPaTemprature` | OCTET STRING | PA-Endstufentemperatur in °C |
| `.1.2.1.2.3.0` | `rptFanSpeed` | INTEGER | Lüfterdrehzahl in RPM |
| `.1.2.1.2.4.0` | `rptVswr` | OCTET STRING | Stehwellenverhältnis (z. B. `"1.15"`) |
| `.1.2.1.2.5.0` | `rptTxFwdPower` | OCTET STRING | Vorwärts-Sendeleistung in Watt |
| `.1.2.1.2.6.0` | `rptTxRefPower` | OCTET STRING | Reflektierte Sendeleistung in Watt |
| `.1.2.1.2.9.0` | `rptSlot1Rssi` | INTEGER | Empfangs-Feldstärke TS1 in dBm |
| `.1.2.1.2.10.0`| `rptSlot2Rssi` | INTEGER | Empfangs-Feldstärke TS2 in dBm |
| `.1.2.1.2.11.0`| `rptSupplyPowerType`| INTEGER | `0` = DC Netzteil, `1` = Notstrom-Batterie |
| `.1.2.1.2.13.0`| `rptBatteryVolt` | OCTET STRING | Batteriespannung in Volt |
| `.1.2.4.1.0` | `rptModelName` | OCTET STRING | Modellbezeichnung (z. B. `"HR1065"`) |
| `.1.2.4.3.0` | `rptFirmwareVersion`| OCTET STRING | Installierte Firmware-Version |
| `.1.2.4.5.0` | `rptSerialNo` | OCTET STRING | Seriennummer der Haupteinheit |
| `.1.2.4.7.0` | `rptRadioID` | INTEGER | Eigene DMR-ID des Repeaters |
| `.1.2.4.10.0`| `rptCurTxFreq` | INTEGER | Aktuelle Sendefrequenz in Hz |
| `.1.2.4.11.0`| `rptCurRxFreq` | INTEGER | Aktuelle Empfangsfrequenz in Hz |

### 10.2 Repeater Remote Knockdown (Repeating State OID)
Ermöglicht die sofortige ferngesteuerte Außerbetriebnahme des Repeaters bei Funkstörungen oder unautorisierter Belegung:

- **OID:** `1.3.6.1.4.1.40297.1.2.2.6.0` (`rptRepeatingState`)
- **Werte:**
  - `0` = **Normalbetrieb (Repeating Enabled)**
  - `1` = **Knockdown (Repeating Disabled / Unterdrückt)**

### 10.3 SNMP Traps (Port 10162)
Der Repeater sendet bei Grenzwertüberschreitung sofortige UDP-Traps an Port 10162:
- `1.3.6.1.4.1.40297.1.2.1.1.1`: Spannungs-Alarm (Unter-/Überspannung)
- `1.3.6.1.4.1.40297.1.2.1.1.2`: PA-Temperatur-Alarm ($> 75^\circ\text{C}$)
- `1.3.6.1.4.1.40297.1.2.1.1.3`: Lüfterausfall-Alarm
- `1.3.6.1.4.1.40297.1.2.1.1.6`: VSWR-Alarm (Antennenschaden / Kabelbruch)
- `1.3.6.1.4.1.40297.1.2.1.1.7`: Sender PLL-Lock Verlust

---

## 11. Busy Channel Lockout (BCL) & Vorrangschaltung (Preemption)

Um Kollisionen auf dem Funkkanal zu verhindern, wenn ein Handfunkgerät spricht und ein Disponent zeitgleich im Web-Interface die PTT betätigt, implementiert das System eine hardware-synchronisierte **Busy Channel Lockout (BCL)** Zustandsmaschine.

```
       [Kanal Frei (IDLE)]
               │
   ┌───────────┴───────────┐
   │                       │
Funkgerät drückt PTT    Leitstelle drückt PTT
   │                       │
   ▼                       ▼
[Kanal Belegt (BUSY)]   [Leitstellen-TX Aktiv]
   │
   ├─► Leitstellen-PTT gesperrt ("KANAL BELEGT")
   │
   └─► Vorrang-Befehl ("LEITSTELLEN-VORRANG"):
       1. Notruf / Priority Call Setup
       2. Unterbrechung des laufenden Rufs
       3. Sofortige Aussendung der Leitstelle
```

- **Sicherheits-Verriegelung:** Bei aktivem Empfangsraster verweigert der TX-Transmitter standardmäßig das Senden und visualisiert ein pulsierendes Warn-Badge im Interface.
- **Priority Preemption:** Die Führungskraft kann durch Bestätigung des Leitstellen-Vorrangs den bestehenden Funkverkehr gezielt übersteuern.

---

## 12. Browser Web-PTT & AudioWorklet-Verschlüsselung

Moderne Web-Browser gewähren Zugriff auf das Mikrofon (`navigator.mediaDevices.getUserMedia`) aus Sicherheitsgründen **ausschließlich in sicheren Kontexten (HTTPS)**.

1. **Automatisches SSL-Zertifikat:** Das System generiert beim ersten Start über `START_HTTPS.bat` automatisch ein 2048-Bit RSA X.509 Zertifikat mit SAN-Einträgen für `localhost` und die lokale LAN-IP (`192.168.x.x`).
2. **AudioWorklet Streaming:** Ein dedizierter Browser-AudioWorklet-Thread erfasst PCM-Audioblöcke latenzfrei und leitet sie über einen sicheren WebSocket (`wss://`) an das FastAPI-Backend weiter.
3. **Redundante Freigabe (PTT Release Guard):** Gegen Hänger bei Tastenverlust (Tab-Wechsel, Blur-Events, Fensterminimierung) sichert ein client- und serverseitiger Timeout-Timer (TOT) von 30 Sekunden das automatische Loslassen der PTT ab.

---

*Ende der Spezifikation. Alle Rechte vorbehalten.*
