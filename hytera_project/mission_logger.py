# -*- coding: utf-8 -*-
"""
mission_logger.py  –  Einsatz-Zeitstrahl Logger
Erfasst alle Ereignisse (PTT, Radio Online/Offline, Notizen, Bilder)
und speichert sie als JSON. Exportiert einen interaktiven HTML-Zeitstrahl.
"""
import os, json, time, shutil, queue, threading
from datetime import datetime

MISSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "missions")
os.makedirs(MISSIONS_DIR, exist_ok=True)


class MissionEvent:
    def __init__(self, etype, rel_time, **kwargs):
        self.etype    = etype       # "ptt_start","ptt_end","online","offline","note","image"
        self.abs_time = datetime.now().isoformat()
        self.rel_time = rel_time    # Sekunden seit Einsatzbeginn
        self.data     = kwargs

    def to_dict(self):
        return {"type": self.etype, "abs": self.abs_time,
                "rel": self.rel_time, **self.data}


class MissionLogger:
    def __init__(self):
        self.active       = False
        self.name         = ""
        self.start_ts     = 0.0
        self.events       = []
        self._open_ptts   = {}   # (radio_id, slot) -> rel_time_start
        self.mission_dir  = ""
        self.json_path    = ""
        self._sse_subs    = []   # Liste von queue.Queue fuer SSE-Clients
        self._sse_lock    = threading.Lock()
        self._master_files = {}  # slot -> file handle (XXL-Aufnahme)
        self._master_bytes = {}  # slot -> byte count
        self._batch        = []  # gepufferte Telemetrie-Events (5s Flush)
        self._batch_lock   = threading.Lock()
        self._flush_timer  = None

    # ── Einsatz starten / stoppen ─────────────────────────────
    def start(self, name=""):
        self.name      = name or datetime.now().strftime("Einsatz_%Y-%m-%d_%H-%M-%S")
        self.mission_dir = os.path.join(MISSIONS_DIR, self.name)
        os.makedirs(self.mission_dir, exist_ok=True)
        self.json_path = os.path.join(self.mission_dir, "mission.json")
        
        if os.path.exists(self.json_path):
            try:
                import json
                with open(self.json_path, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                start_str = d.get("start")
                if start_str:
                    try:
                        self.start_ts = datetime.fromisoformat(start_str).timestamp()
                    except Exception:
                        self.start_ts = time.time()
                else:
                    self.start_ts = time.time()
                
                self.events = []
                for re in d.get("events", []):
                    etype = re.pop("type", "unknown")
                    abs_time = re.pop("abs", datetime.now().isoformat())
                    rel_time = re.pop("rel", 0.0)
                    ev = MissionEvent(etype, rel_time, **re)
                    ev.abs_time = abs_time
                    self.events.append(ev)
            except Exception:
                self.start_ts = time.time()
                self.events = []
        else:
            self.start_ts = time.time()
            self.events = []

        self._open_ptts = {}
        self._master_files = {}
        self._master_bytes = {}
        self._batch    = []
        self.active    = True

        # ── Unkollierte PTTs aus Absturz-Szenario schliessen ──
        # Beim sauberen Stop werden alle PTTs geschlossen. Wenn der Dispatcher
        # aber abgestuerzt ist, haben ptt_start-Events kein passendes ptt_end.
        # Diese werden jetzt mit einem synthetischen Ende markiert.
        if self.events:
            open_starts = {}  # (radio_id, slot) -> MissionEvent
            for ev in self.events:
                key = (ev.data.get("radio_id", 0), ev.data.get("slot", ""))
                if ev.etype == "ptt_start":
                    open_starts[key] = ev
                elif ev.etype == "ptt_end":
                    open_starts.pop(key, None)
            if open_starts:
                import logging as _lg
                _lg.getLogger(__name__).warning(
                    f"[Einsatz] Fortsetzen: {len(open_starts)} ungeschlossene PTT(s) "
                    f"aus vorherigem Absturz – automatisch geschlossen.")
                for key, s_ev in open_starts.items():
                    rid, slot = key
                    # Synthetisches Ende direkt nach dem Start (1s Dauer)
                    close_ev = MissionEvent("ptt_end", s_ev.rel_time + 1.0,
                                            radio_id=rid, slot=slot,
                                            duration=1.0, byte_count=0,
                                            auto_closed=True)
                    # abs_time = Start + 1s
                    try:
                        from datetime import datetime as _dt, timedelta as _td
                        close_ev.abs_time = (
                            _dt.fromisoformat(s_ev.abs_time) + _td(seconds=1)
                        ).isoformat()
                    except Exception:
                        pass  # Fallback: now() ist bereits gesetzt
                    self.events.append(close_ev)
                # Ereignisliste zeitlich sortieren (rel_time)
                self.events.sort(key=lambda e: e.rel_time)

        self._save_meta()
        self._start_flush_timer()
        return self.mission_dir

    def stop(self):
        if not self.active:
            return
        # Alle offenen PTTs schliessen
        for (rid, slot) in list(self._open_ptts.keys()):
            self.log_ptt_end(rid, slot)
        # Restliche Batch-Daten sofort flushen
        self._flush_batch()
        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None
        # Master-Aufnahmedateien schliessen
        for slot, fh in list(self._master_files.items()):
            try:
                fh.close()
                b = self._master_bytes.get(slot, 0)
                self._add(MissionEvent("rec_closed", self._rel(),
                                       slot=slot, bytes=b,
                                       filename=f"{slot}_master.raw"))
            except Exception:
                pass
        self._master_files.clear()
        self.active = False
        # Offene PTTs schliessen
        for (rid, slot), t0 in list(self._open_ptts.items()):
            self._add(MissionEvent("ptt_end", self._rel(),
                                   radio_id=rid, slot=slot, duration=self._rel()-t0))
        self.active = False
        self._save()

    def _rel(self):
        return round(time.time() - self.start_ts, 2)

    def _add(self, ev):
        self.events.append(ev)
        self._save()
        self._broadcast(ev.to_dict())

    def _broadcast(self, ev_dict):
        """Pushes a new event to all connected SSE clients."""
        dead = []
        with self._sse_lock:
            for q in self._sse_subs:
                try:
                    q.put_nowait(ev_dict)
                except Exception:
                    dead.append(q)
            for q in dead:
                self._sse_subs.remove(q)

    def subscribe_sse(self):
        """Returns a queue that receives new event dicts as they are logged."""
        q = queue.Queue(maxsize=200)
        with self._sse_lock:
            self._sse_subs.append(q)
        return q

    def unsubscribe_sse(self, q):
        with self._sse_lock:
            if q in self._sse_subs:
                self._sse_subs.remove(q)

    # ── Master-Aufnahme ─────────────────────────────────────
    def open_master_rec(self, slot):
        """Oeffnet die XXL-Masterdatei fuer einen Slot."""
        if not self.active or slot in self._master_files: return
        fn = os.path.join(self.mission_dir, f"{slot}_master.raw")
        try:
            if os.path.exists(fn):
                self._master_bytes[slot] = os.path.getsize(fn)
                self._master_files[slot] = open(fn, "ab")
            else:
                self._master_bytes[slot] = 0
                self._master_files[slot] = open(fn, "wb")
        except Exception as e:
            pass  # Fehler still ignorieren

    def write_audio(self, slot, payload):
        """Schreibt Audio-Payload in die XXL-Masterdatei."""
        fh = self._master_files.get(slot)
        if fh:
            fh.write(payload)
            self._master_bytes[slot] = self._master_bytes.get(slot, 0) + len(payload)

    # ── Audio-Segment-Export ─────────────────────────────────
    @staticmethod
    def _make_wav_bytes(raw_bytes):
        """Erstellt WAV-Datei aus G.711 µ-law Rohdaten (8-bit, 8kHz, mono)."""
        import struct
        fmt = struct.pack('<HHIIHH H', 7, 1, 8000, 8000, 1, 8, 0)
        riff_sz = 4 + (8 + len(fmt)) + (8 + len(raw_bytes))
        return (b'RIFF' + struct.pack('<I', riff_sz) + b'WAVE'
                + b'fmt ' + struct.pack('<I', len(fmt)) + fmt
                + b'data' + struct.pack('<I', len(raw_bytes)) + raw_bytes)

    def export_audio_segments(self):
        """Exportiert alle PTT-Segmente als WAV-Dateien in <mission>/audio/.
        Gibt dict zurueck: (slot, rel_time) -> 'audio/TS1_001.wav'"""
        if not self.mission_dir:
            return {}
        audio_dir = os.path.join(self.mission_dir, "audio")
        os.makedirs(audio_dir, exist_ok=True)
        # Master-Dateien einmalig einlesen
        raw_cache = {}
        def _raw(slot):
            if slot not in raw_cache:
                p = os.path.join(self.mission_dir, f"{slot}_master.raw")
                raw_cache[slot] = open(p, 'rb').read() if os.path.exists(p) else None
            return raw_cache[slot]
        result = {}
        open_tx = {}
        nr_by_slot = {}
        for ev in self.events:
            if ev.etype == "ptt_start":
                open_tx[(ev.data.get("radio_id",0), ev.data.get("slot",""))] = ev
            elif ev.etype == "ptt_end":
                key = (ev.data.get("radio_id",0), ev.data.get("slot",""))
                s_ev = open_tx.pop(key, None)
                if not s_ev: continue
                slot = ev.data.get("slot", "TS1")
                off  = s_ev.data.get("byte_offset", 0)
                cnt  = ev.data.get("byte_count", 0)
                if cnt <= 0: continue
                raw = _raw(slot)
                if raw is None: continue
                seg = raw[off: off + cnt]
                if not seg: continue
                nr = nr_by_slot.get(slot, 0) + 1
                nr_by_slot[slot] = nr
                fname = f"{slot}_{nr:03d}.wav"
                try:
                    with open(os.path.join(audio_dir, fname), 'wb') as f:
                        f.write(self._make_wav_bytes(seg))
                    result[(slot, s_ev.rel_time)] = f"audio/{fname}"
                except Exception:
                    pass
        return result

    # ── 5-Sekunden Batch-Flush ───────────────────────────────
    def _start_flush_timer(self):
        if self._flush_timer: self._flush_timer.cancel()
        self._flush_timer = threading.Timer(5.0, self._flush_and_reschedule)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _flush_and_reschedule(self):
        self._flush_batch()
        if self.active:
            self._start_flush_timer()

    def _flush_batch(self):
        with self._batch_lock:
            pending = self._batch[:]
            self._batch.clear()
        for ev in pending:
            self.events.append(ev)
            self._broadcast(ev.to_dict())
        if pending:
            self._save()

    def _add_batched(self, ev):
        """Puffert ein Event fuer den 5s-Flush (GPS, SMS, USV)."""
        if not self.active: return
        with self._batch_lock:
            self._batch.append(ev)

    def log_ptt_start(self, radio_id, slot, rec_file="", rssi=None):
        if not self.active: return
        key = (radio_id, slot)
        # Vorherige offene PTT automatisch schliessen
        if key in self._open_ptts:
            t0, b0  = self._open_ptts.pop(key)
            dur     = round(self._rel() - t0, 2)
            byte_n  = self._master_bytes.get(slot, 0) - b0
            self._add(MissionEvent("ptt_end", self._rel(),
                                   radio_id=radio_id, slot=slot,
                                   duration=dur, byte_count=byte_n))
        # Aktuellen Byte-Offset in Masterdatei merken
        byte_off = self._master_bytes.get(slot, 0)
        self._open_ptts[key] = (self._rel(), byte_off)
        self._add(MissionEvent("ptt_start", self._rel(),
                               radio_id=radio_id, slot=slot,
                               rec_file=rec_file, byte_offset=byte_off,
                               rssi=rssi))

    def attach_rssi(self, radio_id, slot, rssi_dbm):
        """Haengt RSSI-Wert an den letzten ptt_start dieses Radios/Slots."""
        for ev in reversed(self.events):
            if (ev.etype == "ptt_start"
                    and ev.data.get("slot") == slot
                    and ev.data.get("radio_id") == radio_id
                    and ev.data.get("rssi") is None):
                ev.data["rssi"] = rssi_dbm
                self._save()
                self._broadcast({"type": "__rssi_update",
                                 "radio_id": radio_id, "slot": slot,
                                 "rssi": rssi_dbm, "rel": ev.rel_time})
                return

    def log_rssi(self, radio_id, slot, rssi_dbm):
        """Loggt einen eigenstaendigen RSSI-Messpunkt (fuer Feldstaerken-Survey)."""
        if not self.active: return
        self._add(MissionEvent("rssi", self._rel(),
                               radio_id=radio_id, slot=slot, rssi=rssi_dbm))

    def log_ptt_start_if_not_open(self, radio_id, slot):
        """PTT-Start nur loggen wenn noch kein offener PTT fuer diese Kombination.
        Verhindert Duplikate wenn CC-Listener UND Audio-Fallback gleichzeitig aktiv sind."""
        if not self.active: return
        key = (radio_id, slot)
        if key not in self._open_ptts:
            self.log_ptt_start(radio_id, slot)

    def log_ptt_end(self, radio_id, slot):

        if not self.active: return
        key = (radio_id, slot)
        entry = self._open_ptts.pop(key, None)
        if entry is None: return
        t0, b0 = entry
        dur     = round(self._rel() - t0, 2)
        byte_n  = self._master_bytes.get(slot, 0) - b0
        self._add(MissionEvent("ptt_end", self._rel(),
                               radio_id=radio_id, slot=slot,
                               duration=dur, byte_count=byte_n))

    def attach_rec_file(self, slot, filepath):
        """Haengt eine Aufnahmedatei an den letzten ptt_start-Event des Slots."""
        import os
        fname = os.path.basename(filepath)
        for ev in reversed(self.events):
            if ev.etype == "ptt_start" and ev.data.get("slot") == slot:
                ev.data["rec_file"] = fname
                self._save()          # JSON sofort aktualisieren
                self._broadcast({"type": "__update_rec",
                                  "slot": slot, "rec_file": fname,
                                  "rel": ev.rel_time})
                return
        # Kein passender ptt_start → eigenes Ereignis anlegen
        self._add(MissionEvent("ptt_start", self._rel(),
                               radio_id=0, slot=slot, rec_file=fname))

    def log_radio_status(self, radio_id, online):
        if not self.active: return
        etype = "online" if online else "offline"
        self._add(MissionEvent(etype, self._rel(), radio_id=radio_id))

    def log_gps(self, radio_id, lat, lon, speed=0, heading=0):
        if not self.active: return
        self._add_batched(MissionEvent("gps", self._rel(),
                               radio_id=radio_id, lat=round(lat, 6),
                               lon=round(lon, 6), speed=speed, heading=heading))

    def log_sms(self, sender_id, target_id, text, is_group=False):
        if not self.active: return
        # SMS sofort (wichtige Nachricht), nicht gebatcht
        self._add(MissionEvent("sms", self._rel(),
                               sender_id=sender_id, target_id=target_id,
                               text=text, is_group=is_group))

    def log_call(self, sender_id, target_id, slot, call_type, status):
        if not self.active: return
        self._add(MissionEvent("call_" + status, self._rel(),
                               radio_id=sender_id, target_id=target_id,
                               slot=slot, call_type=call_type))

    def log_usv(self, batt_pct, batt_status, runtime_min):
        if not self.active: return
        self._add_batched(MissionEvent("usv", self._rel(),
                               batt_pct=batt_pct, batt_status=batt_status,
                               runtime_min=runtime_min))

    def add_note(self, text, radio_id=None):
        if not self.active: return
        self._add(MissionEvent("note", self._rel(),
                               text=text, radio_id=radio_id))

    def add_image(self, src_path, caption=""):
        if not self.active: return
        fname = f"img_{int(time.time())}_{os.path.basename(src_path)}"
        dst   = os.path.join(self.mission_dir, fname)
        shutil.copy2(src_path, dst)
        self._add(MissionEvent("image", self._rel(),
                               filename=fname, caption=caption))

    # ── Persistenz ────────────────────────────────────────────
    def _save_meta(self):
        self._save()

    def _save(self):
        if not self.json_path: return
        data = {"name": self.name,
                "start": datetime.fromtimestamp(self.start_ts).isoformat(),
                "events": [e.to_dict() for e in self.events]}
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── HTML Export ───────────────────────────────────────────
    def export_html(self, ids_map=None):
        """Speichert mission.json (mit IDs), exportiert Audio-Segmente und
        erstellt einen Standalone-Viewer mit eingebetteten Daten."""
        ids_map = ids_map or {}
        # Audio-Segmente exportieren
        audio_map = self.export_audio_segments()
        # Ereignisse mit Audio-Pfaden anreichern
        events_out = []
        open_tx = {}
        nr_by_slot = {}
        for e in self.events:
            d = e.to_dict()
            if e.etype == "ptt_start":
                open_tx[(e.data.get("radio_id",0), e.data.get("slot",""))] = e
            elif e.etype == "ptt_end":
                key = (e.data.get("radio_id",0), e.data.get("slot",""))
                s_ev = open_tx.pop(key, None)
                if s_ev:
                    slot = e.data.get("slot","TS1")
                    af = audio_map.get((slot, s_ev.rel_time))
                    if af:
                        # Pfad rueckwaerts zum ptt_start eintragen
                        for prev in events_out:
                            if (prev.get("type")=="ptt_start"
                                    and prev.get("radio_id")==s_ev.data.get("radio_id")
                                    and prev.get("slot")==slot
                                    and prev.get("rel")==s_ev.rel_time):
                                prev["_audio_file"] = af
                                break
            events_out.append(d)
        data = {"name": self.name,
                "start": datetime.fromtimestamp(self.start_ts).isoformat(),
                "ids": ids_map,
                "events": events_out}
        json_path = os.path.join(self.mission_dir, "mission.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Standalone-HTML generieren (eingebettete Daten + relative Audio-Pfade)
        viewer_dst = os.path.join(self.mission_dir, "timeline.html")
        html = _build_standalone_html(data)
        with open(viewer_dst, "w", encoding="utf-8") as f:
            f.write(html)
        return viewer_dst

    def generate_report(self, ids_map=None, live=False):
        """Erstellt ein vollstaendiges Funkprotokoll als HTML.
        live=True  -> gibt HTML-String zurueck (fuer SSE-Server, mit auto-refresh)
        live=False -> speichert in Funkprotokoll.html und gibt Pfad zurueck
        """
        ids_map = ids_map or {}

        def resolve(rid):
            e = ids_map.get(str(rid), {})
            name = e.get("name", f"ID {rid}")
            geraet = e.get("geraet", "")
            return name, geraet

        start_dt = datetime.fromtimestamp(self.start_ts)

        # ── Übertragungen aufbauen ──────────────────────────────
        transmissions = []   # {nr, time, abs, name, geraet, slot, call_type, target, dur, rec_offset}
        open_tx = {}
        nr = 0
        for ev in self.events:
            t = ev.etype
            d = ev.data
            if t == "ptt_start":
                key = (d.get("radio_id", 0), d.get("slot", ""))
                open_tx[key] = ev
            elif t == "ptt_end":
                key = (d.get("radio_id", 0), d.get("slot", ""))
                start_ev = open_tx.pop(key, None)
                if start_ev is None: continue
                nr += 1
                rid  = d.get("radio_id", 0)
                name, geraet = resolve(rid)
                rel  = start_ev.rel_time
                dur  = d.get("duration", 0)
                # Byte-Offset: aus Event oder zeitbasiert als Fallback
                byte_off   = start_ev.data.get("byte_offset", int(rel * 8000))
                byte_count = d.get("byte_count", 0)
                # Fallback: Wenn byte_count=0 aber Dauer bekannt → zeitbasiert schätzen
                if byte_count == 0 and dur and dur != '?':
                    try:
                        byte_count = int(float(dur) * 8000)
                    except (ValueError, TypeError):
                        byte_count = 0
                abs_t = (start_dt + __import__("datetime").timedelta(seconds=rel)).strftime("%H:%M:%S")
                transmissions.append({
                    "nr": nr, "rel": rel, "abs": abs_t,
                    "name": name, "geraet": geraet,
                    "radio_id": rid,
                    "slot": d.get("slot", ""), "call_type": start_ev.data.get("call_type", ""),
                    "dur": dur, "byte_off": byte_off, "byte_count": byte_count,
                    "approx": d.get("byte_count", 0) == 0,  # True = zeitbasierte Schätzung
                })
            elif t in ("call_start",):
                # Fallback falls nur call_start ohne ptt_start
                rid = d.get("radio_id", 0)
                target_id = d.get("target_id", 0)
                name, geraet = resolve(rid)
                tname, _ = resolve(target_id)
                key = (rid, d.get("slot", ""))
                if key not in open_tx:
                    open_tx[key] = ev

        # Unclosed PTTs schliessen
        for key, ev in open_tx.items():
            nr += 1
            rid = ev.data.get("radio_id", 0)
            name, geraet = resolve(rid)
            rel = ev.rel_time
            byte_off = ev.data.get("byte_offset", int(rel * 8000))
            abs_t = (start_dt + __import__("datetime").timedelta(seconds=rel)).strftime("%H:%M:%S")
            transmissions.append({
                "nr": nr, "rel": rel, "abs": abs_t,
                "name": name, "geraet": geraet,
                "radio_id": rid,
                "slot": ev.data.get("slot", ""), "call_type": "",
                "dur": "?", "byte_off": byte_off, "byte_count": 0,
            })

        # ── SMS aufbauen ─────────────────────────────────────────
        sms_list = []
        for ev in self.events:
            if ev.etype == "sms":
                d = ev.data
                sname, _ = resolve(d.get("sender_id", 0))
                tname, _ = resolve(d.get("target_id", 0))
                abs_t = (start_dt + __import__("datetime").timedelta(
                    seconds=ev.rel_time)).strftime("%H:%M:%S")
                sms_list.append({
                    "abs": abs_t, "sender": sname,
                    "target": tname, "text": d.get("text", ""),
                    "group": d.get("is_group", False)
                })

        # ── Radio-Statistik ──────────────────────────────────────
        stats = {}
        for tx in transmissions:
            k = tx["radio_id"]
            if k not in stats:
                stats[k] = {"name": tx["name"], "geraet": tx["geraet"],
                             "count": 0, "total_sec": 0.0}
            stats[k]["count"] += 1
            try: stats[k]["total_sec"] += float(tx["dur"])
            except: pass

        # ── HTML generieren ─────────────────────────────────────
        total_sec = int(time.time() - self.start_ts) if self.active else \
                    int((self.events[-1].rel_time if self.events else 0) + 1)
        dur_h, rem = divmod(total_sec, 3600)
        dur_m, dur_s = divmod(rem, 60)
        end_str = datetime.now().strftime("%H:%M:%S")

        # Audio-Segmente fuer gespeicherte Einsaetze exportieren
        audio_map = {}
        if not live:
            audio_map = self.export_audio_segments()

        tx_rows = ""
        for tx in transmissions:
            try: dur_s2 = f"{float(tx['dur']):.1f}s"
            except: dur_s2 = "?"
            bc       = tx.get('byte_count', 0)
            approx   = tx.get('approx', False)
            approx_note = ' <span title="Zeitbasierte Schätzung – ohne Master-Aufnahme" style="color:#f9e2af;font-size:9px">~</span>' if approx else ''
            if bc and bc > 0:
                if live:
                    audio_src = f"/audio/{tx['slot']}/{tx['byte_off']}/{bc}"
                else:
                    audio_src = audio_map.get((tx['slot'], tx['rel']), "")
                if audio_src:
                    audio_cell = (f'<audio controls src="{audio_src}" '
                                  f'style="height:28px;width:180px;accent-color:#fce300">'
                                  f'</audio>{approx_note}')
                else:
                    audio_cell = f'<span style="color:#3a3a5a;font-size:10px">– (Datei fehlt)</span>'
            else:
                audio_cell = '<span style="color:#3a3a5a;font-size:10px">–</span>'
            tx_rows += f"""
            <tr>
              <td class="nr">{tx['nr']}</td>
              <td class="time">{tx['abs']}</td>
              <td><strong>{tx['name']}</strong><br>
                  <span class="sub">{tx['geraet']} | ID {tx['radio_id']}</span></td>
              <td><span class="badge slot-{tx['slot'].lower()}">{tx['slot']}</span></td>
              <td>{tx['call_type'] or 'Gruppe'}</td>
              <td class="dur">{dur_s2}</td>
              <td>{audio_cell}</td>
            </tr>"""

        sms_rows = ""
        for s in sms_list:
            typ = "Gruppe" if s["group"] else "Direkt"
            sms_rows += f"""
            <tr>
              <td class="time">{s['abs']}</td>
              <td>{s['sender']}</td>
              <td>{s['target']}</td>
              <td><span class="badge">{typ}</span></td>
              <td class="sms-text">{s['text']}</td>
            </tr>"""

        stat_rows = ""
        for rid, st in sorted(stats.items(), key=lambda x: -x[1]["count"]):
            avg = st["total_sec"] / st["count"] if st["count"] else 0
            stat_rows += f"""
            <tr>
              <td><strong>{st['name']}</strong></td>
              <td class="sub">{st['geraet']}</td>
              <td class="nr">{st['count']}</td>
              <td>{st['total_sec']:.1f}s</td>
              <td>{avg:.1f}s</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
{"<meta http-equiv='refresh' content='2'>" if live else ""}
<title>// FUNKPROTOKOLL – {self.name}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
:root{{
  --acc:#fce300; --cyan:#00fff7; --red:#ff003c;
  --grn:#39ff14; --org:#ff6b00;
  --bg:#0a0a0f;  --panel:#0d0d1a; --border:rgba(252,227,0,0.2);
  --tx:#e8e8e8;  --sub:#3a3a5a;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{
  background:var(--bg);color:var(--tx);
  font-family:'Share Tech Mono','Consolas',monospace;
  padding:32px;line-height:1.7;
  background-image:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(252,227,0,0.01) 2px,rgba(252,227,0,0.01) 4px);
}}
h1{{
  color:var(--acc);font-size:20px;letter-spacing:3px;text-transform:uppercase;
  text-shadow:0 0 16px rgba(252,227,0,0.7);margin-bottom:6px;
}}
h2{{
  color:var(--acc);font-size:13px;letter-spacing:2px;text-transform:uppercase;
  margin:32px 0 12px;padding-bottom:6px;
  border-bottom:1px solid var(--border);
  text-shadow:0 0 8px rgba(252,227,0,0.4);
}}
h2::before{{content:"// ";color:var(--sub)}}
.meta{{color:var(--sub);font-size:12px;margin-bottom:28px;letter-spacing:1px}}
.meta strong{{color:var(--acc)}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:28px}}
th{{
  background:var(--panel);color:var(--acc);
  padding:8px 10px;text-align:left;font-weight:normal;
  letter-spacing:1px;text-transform:uppercase;white-space:nowrap;
  border-bottom:1px solid var(--acc);font-size:11px;
}}
td{{padding:7px 10px;border-bottom:1px solid rgba(252,227,0,0.06);vertical-align:top}}
tr:hover td{{background:rgba(252,227,0,0.04)}}
.nr{{color:var(--sub);font-size:10px;width:40px;text-align:right}}
.time{{color:var(--acc);white-space:nowrap;font-variant-numeric:tabular-nums;text-shadow:0 0 6px rgba(252,227,0,0.4)}}
.sub{{color:var(--sub);font-size:10px}}
.dur{{color:var(--cyan);font-variant-numeric:tabular-nums;text-align:right;text-shadow:0 0 4px rgba(0,255,247,0.4)}}
.off{{color:var(--sub);font-size:10px}}
.sms-text{{font-style:italic;color:var(--cyan)}}
.badge{{
  display:inline-block;padding:1px 8px;font-size:10px;
  background:transparent;border:1px solid var(--acc);color:var(--acc);
  letter-spacing:1px;text-transform:uppercase;
}}
.slot-ts1{{border-color:var(--acc);color:var(--acc)}}
.slot-ts2{{border-color:var(--cyan);color:var(--cyan)}}
.summary{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:28px}}
.card{{
  background:var(--panel);
  border:1px solid var(--border);
  border-left:3px solid var(--acc);
  padding:14px 20px;min-width:130px;
  clip-path:polygon(0 0,calc(100% - 8px) 0,100% 8px,100% 100%,0 100%);
}}
.card-val{{font-size:26px;color:var(--acc);text-shadow:0 0 12px rgba(252,227,0,0.6)}}
.card-lbl{{font-size:10px;color:var(--sub);letter-spacing:1px;text-transform:uppercase}}
.toc{{
  background:var(--panel);border:1px solid var(--border);
  border-left:3px solid var(--acc);
  padding:14px 20px;margin-bottom:32px;
}}
.toc h3{{color:var(--acc);margin-bottom:10px;font-size:12px;letter-spacing:2px;text-transform:uppercase}}
.toc ul{{list-style:none;display:flex;gap:20px;flex-wrap:wrap}}
.toc a{{color:var(--sub);text-decoration:none;font-size:12px;letter-spacing:1px;transition:.15s}}
.toc a:hover{{color:var(--acc);text-shadow:0 0 6px rgba(252,227,0,0.5)}}
::-webkit-scrollbar{{width:4px}}
::-webkit-scrollbar-track{{background:#050508}}
::-webkit-scrollbar-thumb{{background:rgba(252,227,0,0.3)}}
@media print{{
  body{{background:#fff;color:#000;padding:16px;font-family:monospace}}
  table{{font-size:11px}} .card-val{{color:#333}}
  h1,h2{{color:#000;text-shadow:none}} .badge{{border:1px solid #ccc;color:#333}}
  .time{{color:#333}} .dur{{color:#333}}
}}
</style>
</head>
<body>
<h1>// FUNKPROTOKOLL</h1>
<div class="meta">
  <strong>EINSATZ:</strong> {self.name} &nbsp;//&nbsp;
  <strong>BEGINN:</strong> {start_dt.strftime("%d.%m.%Y %H:%M:%S")} &nbsp;//&nbsp;
  <strong>ENDE:</strong> {end_str} &nbsp;//&nbsp;
  <strong>DAUER:</strong> {dur_h:02d}:{dur_m:02d}:{dur_s:02d} &nbsp;//&nbsp;
  GEN: {datetime.now().strftime("%d.%m.%Y %H:%M")}
</div>

<div class="toc">
  <h3>INDEX</h3>
  <ul>
    <li><a href="#zusammenfassung">ZUSAMMENFASSUNG</a></li>
    <li><a href="#statistik">STATISTIK</a></li>
    <li><a href="#funklog">FUNKPROTOKOLL</a></li>
    {"<li><a href='#sms'>NACHRICHTEN</a></li>" if sms_list else ""}
  </ul>
</div>

<h2 id="zusammenfassung">📊 Zusammenfassung</h2>
<div class="summary">
  <div class="card"><div class="card-val">{len(transmissions)}</div>
    <div class="card-lbl">Übertragungen gesamt</div></div>
  <div class="card"><div class="card-val">{len(stats)}</div>
    <div class="card-lbl">Aktive Teilnehmer</div></div>
  <div class="card"><div class="card-val">{sum(v['count'] for v in stats.values() if v.get('count'))}</div>
    <div class="card-lbl">PTT-Tastendrücke</div></div>
  <div class="card"><div class="card-val">{len(sms_list)}</div>
    <div class="card-lbl">SMS-Nachrichten</div></div>
  <div class="card"><div class="card-val">{dur_h:02d}:{dur_m:02d}</div>
    <div class="card-lbl">Einsatzdauer</div></div>
</div>

<h2 id="statistik">👤 Statistik pro Mitarbeiter / Gerät</h2>
<table>
  <thead><tr><th>Name</th><th>Gerät</th><th>Übertragungen</th>
    <th>Gesamt-Sendezeit</th><th>Ø Dauer</th></tr></thead>
  <tbody>{stat_rows}</tbody>
</table>

<h2 id="funklog">📻 Funkprotokoll – Chronologisch</h2>
<table>
  <thead><tr><th>#</th><th>Uhrzeit</th><th>Teilnehmer</th>
    <th>Slot</th><th>Ruftyp</th><th>Dauer</th><th>Audio</th></tr></thead>
  <tbody>{tx_rows}</tbody>
</table>

{"<h2 id='sms'>💬 Nachrichten-Protokoll</h2><table><thead><tr><th>Uhrzeit</th><th>Von</th><th>An</th><th>Typ</th><th>Text</th></tr></thead><tbody>" + sms_rows + "</tbody></table>" if sms_list else ""}

<div style="margin-top:32px;color:var(--sub);font-size:11px;border-top:1px solid var(--border);padding-top:12px">
  Automatisch erstellt von Hytera Repeater Dispatcher &nbsp;|&nbsp;
  Audio-Referenz: <code>{self.name}/TS1_master.raw</code> / <code>TS2_master.raw</code> &nbsp;|&nbsp;
  Byte-Offset basiert auf G.711 µ-law, 8000 Hz
</div>
</body></html>"""

        if live:
            return html
        report_path = os.path.join(self.mission_dir, "Funkprotokoll.html")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html)
        return report_path


    @staticmethod
    def list_missions():
        result = []
        for d in sorted(os.listdir(MISSIONS_DIR), reverse=True):
            p = os.path.join(MISSIONS_DIR, d, "mission.json")
            if os.path.exists(p):
                result.append((d, p))
        return result

    @staticmethod
    def load_from_json(json_path):
        with open(json_path, encoding="utf-8") as f:
            return json.load(f)


# ─────────────────────────────────────────────────────────────
#  Standalone-HTML (gespeicherte Einsätze, kein Server nötig)
# ─────────────────────────────────────────────────────────────
def _build_standalone_html(data):
    """Erzeugt eine komplett autonome Timeline-HTML mit eingebetteten Daten
    und relativen Audio-Pfaden (für Öffnen aus Datei ohne Server)."""
    import json
    import os
    ev_json  = json.dumps(data.get("events", []), ensure_ascii=False)
    ids_json = json.dumps(data.get("ids", {}),    ensure_ascii=False)
    name     = data.get("name", "Einsatz")
    start    = data.get("start", "")

    # Lese das aktuelle timeline_viewer.html
    viewer_path = os.path.join(os.path.dirname(__file__), "timeline_viewer.html")
    with open(viewer_path, "r", encoding="utf-8") as f:
        html = f.read()

    injection = f"""<script>
window.STANDALONE_DATA = {{
    events: {ev_json},
    ids: {ids_json},
    name: "{name}",
    start: "{start}"
}};
</script>
"""
    html = html.replace("<head>", "<head>\n" + injection, 1)
    return html
