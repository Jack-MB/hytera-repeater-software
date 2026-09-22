# -*- coding: utf-8 -*-
"""
mission_server.py – Leichtgewichtiger HTTP + SSE Server
Liefert die Timeline-HTML-Seite aus und pusht neue Ereignisse
per Server-Sent Events (SSE) in Echtzeit an den Browser.
"""
import json, threading, time, os, queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

PORT = 19080
_instance = None   # Singleton


class _Handler(BaseHTTPRequestHandler):
    """Einfacher Handler: / → HTML, /events → SSE, /state → aktueller JSON-State"""

    logger = None   # MissionLogger Referenz (gesetzt von MissionSSEServer)
    ids_fn = None   # Pfad zu ids.json

    def log_message(self, *a): pass   # Konsole sauber halten

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == "/":
            self._serve_html()
        elif path == "/events":
            self._serve_sse()
        elif path == "/state":
            self._serve_state()
        elif path == "/report":
            self._serve_report()
        elif path.startswith("/audio/"):
            self._serve_audio()
        elif path.startswith("/rec/"):
            self._serve_recording()
        elif path.startswith("/img/"):
            self._serve_image()
        elif path == "/status":
            self._serve_status()
        else:
            self.send_error(404)

    def do_POST(self):
        path = self.path.split('?')[0]
        if path == "/note":
            self._recv_note()
        elif path == "/image":
            self._recv_image()
        else:
            self.send_error(404)

    def _recv_note(self):
        """POST /note  body: {"text":"...", "rel":12.3}"""
        ml = _Handler.logger
        if not ml:
            self.send_error(503, "Kein Einsatz aktiv"); return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            text   = body.get("text", "").strip()
            if not text:
                self.send_error(400, "Kein Text"); return
            ml.add_note(text)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception as e:
            self.send_error(500, str(e))

    def _recv_image(self):
        """POST /image  multipart/form-data mit Feldern: file, caption"""
        ml = _Handler.logger
        if not ml:
            self.send_error(503, "Kein Einsatz aktiv"); return
        try:
            import cgi, tempfile
            ctype = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            # Einfaches multipart-Parsing ueber cgi.FieldStorage
            env = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": ctype,
                "CONTENT_LENGTH": str(length),
            }
            fs = cgi.FieldStorage(fp=self.rfile, headers=self.headers,
                                  environ=env, keep_blank_values=True)
            caption   = fs.getvalue("caption", "")
            file_item = fs["file"] if "file" in fs else None
            if not file_item or not file_item.filename:
                self.send_error(400, "Kein Bild"); return
            # Temp-Datei anlegen
            suffix = os.path.splitext(file_item.filename)[1] or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(file_item.file.read())
                tmp_path = tmp.name
            ml.add_image(tmp_path, caption=caption)
            os.unlink(tmp_path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception as e:
            self.send_error(500, str(e))

    def _serve_image(self):
        """GET /img/<filename>  – liefert Bilder aus dem Einsatz-Ordner."""
        ml = _Handler.logger
        if not ml:
            self.send_error(503); return
        fname = self.path[5:]  # strip "/img/"
        path  = os.path.join(ml.mission_dir, fname)
        if not os.path.exists(path):
            self.send_error(404); return
        ext = os.path.splitext(fname)[1].lower()
        ctype = {"jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png", ".gif": "image/gif",
                 ".webp": "image/webp"}.get(ext, "image/jpeg")
        data = open(path, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


    def _send_bytes_with_range(self, body, content_type):
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            try:
                range_spec = range_header.replace("bytes=", "").split("-")
                start = int(range_spec[0])
                end = int(range_spec[1]) if len(range_spec) > 1 and range_spec[1] else len(body) - 1
                end = min(end, len(body) - 1)
                
                chunk = body[start:end+1]
                self.send_response(206)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(chunk)
                return
            except Exception:
                pass
                
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    # ── G.711 µ-law Segment als WAV ───────────────────────────
    # URL-Format:  /audio/<slot>/<start_sec>/<dur_sec>
    # Beispiel:    /audio/TS1/143.5/3.39
    @staticmethod
    def _mulaw_wav_header(num_bytes, sample_rate=8000):
        """Erzeugt einen WAV-Header fuer G.711 µ-law (8-bit, mono)."""
        import struct
        fmt_chunk  = struct.pack('<HHIIHH H',
            7,            # AudioFormat: µ-law
            1,            # NumChannels: mono
            sample_rate,  # SampleRate
            sample_rate,  # ByteRate  (8-bit mono → Bytes/s = Hz)
            1,            # BlockAlign
            8,            # BitsPerSample
            0)            # cbSize (required for non-PCM)
        riff_size = 4 + (8 + len(fmt_chunk)) + (8 + num_bytes)
        header = (b'RIFF' + struct.pack('<I', riff_size) + b'WAVE'
                  + b'fmt ' + struct.pack('<I', len(fmt_chunk)) + fmt_chunk
                  + b'data' + struct.pack('<I', num_bytes))
        return header

    def _serve_audio(self):
        ml = _Handler.logger
        if not ml:
            self.send_error(503, "Kein Einsatz aktiv"); return
        # URL: /audio/<slot>/<byte_offset>/<byte_count>
        parts = self.path.strip('/').split('/')
        if len(parts) < 4:
            self.send_error(400, "Format: /audio/<slot>/<byte_offset>/<byte_count>"); return
        slot = parts[1]
        try:
            byte_off   = int(float(parts[2]))
            byte_count = int(float(parts[3]))
        except ValueError:
            self.send_error(400, "Ungueltige Byte-Angabe"); return
        if byte_count <= 0:
            self.send_error(400, "byte_count muss > 0 sein"); return

        master_path = os.path.join(ml.mission_dir, f"{slot}_master.raw")
        if not os.path.exists(master_path):
            self.send_error(404, f"Keine Aufnahme fuer {slot}"); return
        try:
            with open(master_path, 'rb') as f:
                f.seek(byte_off)
                audio_data = f.read(byte_count)
        except Exception as e:
            self.send_error(500, str(e)); return

        wav_header = self._mulaw_wav_header(len(audio_data))
        body = wav_header + audio_data
        self._send_bytes_with_range(body, "audio/wav")



    # ── HTML-Seite ────────────────────────────────────────────
    def _serve_html(self):
        html_src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "timeline_viewer.html")
        try:
            with open(html_src, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error(500, str(e))

    # ── Live Funkprotokoll ────────────────────────────────────
    def _serve_report(self):
        ml = _Handler.logger
        if not ml:
            self.send_error(503, "Kein Einsatz aktiv"); return
        ids_map = {}
        try:
            with open(_Handler.ids_fn, encoding="utf-8") as f:
                ids_map = json.load(f)
        except Exception:
            pass
        try:
            body = ml.generate_report(ids_map=ids_map, live=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error(500, str(e))

    # ── Aktueller Zustand (JSON) ──────────────────────────────
    def _serve_state(self):
        ml = _Handler.logger
        ids_map = {}
        try:
            with open(_Handler.ids_fn, encoding="utf-8") as f:
                ids_map = json.load(f)
        except Exception:
            pass
        data = {
            "name":   ml.name if ml else "",
            "start":  str(ml.start_ts) if ml else "0",
            "active": ml.active if ml else False,
            "ids":    ids_map,
            "events": [e.to_dict() for e in (ml.events if ml else [])],
        }
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── System-Status (USV + Repeater) ───────────────────────
    def _serve_status(self):
        import time as _time
        ml  = _Handler.logger
        usv = _Handler.usv_data or {}
        hb_ts  = _Handler.last_hb_ts
        hb_age = round(_time.time() - hb_ts, 1) if hb_ts else None

        # Aktive PTTs zaehlen
        active_ptts = []
        if ml:
            for key in list(ml._open_ptts.keys()):
                rid, slot = key
                active_ptts.append({"radio_id": rid, "slot": slot})

        data = {
            "usv":  usv,
            "router": getattr(_Handler, 'router_data', None),
            "repeater": {
                "heartbeat_age_s": hb_age,
                "online": hb_age is not None and hb_age < 15,
                "addr":   _Handler.last_hb_addr,
            },
            "mission": {
                "active":      ml.active if ml else False,
                "name":        ml.name   if ml else "",
                "event_count": len(ml.events) if ml else 0,
                "active_ptts": active_ptts,
            },
        }
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── SSE-Stream ────────────────────────────────────────────
    def _serve_sse(self):
        ml = _Handler.logger
        if not ml:
            self.send_error(503, "Kein Einsatz aktiv")
            return
        # Socket-Timeout: erkennt tote Verbindungen nach 20s
        self.connection.settimeout(20)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q = ml.subscribe_sse()
        try:
            # Sofortiger Init-State
            state = {
                "type": "__init__",
                "events": [e.to_dict() for e in ml.events],
                "name": ml.name,
                "start": ml.start_ts,
                "active": ml.active,
            }
            self._sse_write(state)
            # Live-Events streamen
            while True:
                try:
                    ev = q.get(timeout=15)
                    self._sse_write(ev)
                except queue.Empty:
                    # Heartbeat – haelt die Verbindung lebendig
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError, TimeoutError):
            pass
        finally:
            ml.unsubscribe_sse(q)

    def _sse_write(self, data):
        msg = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        self.wfile.write(msg.encode("utf-8"))
        self.wfile.flush()

    # ── Aufnahme-Datei ────────────────────────────────────────
    def _serve_recording(self):
        fname = self.path[5:]
        ml = _Handler.logger
        if not ml: self.send_error(404); return
        path = os.path.join(ml.mission_dir, fname)
        if not os.path.exists(path): self.send_error(404); return
        with open(path, "rb") as f:
            body = f.read()
        self._send_bytes_with_range(body, "audio/wav" if fname.endswith(".wav") else "audio/basic")


class MissionSSEServer:
    def __init__(self, mission_logger, ids_file=None):
        self._ml  = mission_logger
        self._srv = None
        self._thr = None
        # Klassen-Variablen fuer Status-Endpunkt
        _Handler.usv_data    = None
        _Handler.last_hb_ts  = None
        _Handler.last_hb_addr = None
        if ids_file is None:
            ids_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "ids.json")
        _Handler.ids_fn = ids_file

    def start(self):
        global _instance
        if _instance:
            try: _instance.stop()
            except Exception: pass
        _Handler.logger = self._ml

        class _ReusableServer(ThreadingMixIn, HTTPServer):
            allow_reuse_address = True
            daemon_threads = True   # Threads sterben mit dem Hauptprozess

        self._srv = _ReusableServer(("0.0.0.0", PORT), _Handler)
        self._thr = threading.Thread(target=self._srv.serve_forever,
                                     daemon=True, name="MissionSSE")
        self._thr.start()
        _instance = self
        # Lokale IP ermitteln fuer die Anzeige
        try:
            import socket as _sock
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "127.0.0.1"
        self._local_url = f"http://{local_ip}:{PORT}/"
        return self._local_url

    def broadcast_ids_update(self, ids_dict):
        """Pusht aktualisierte IDS_MAP sofort per SSE an alle Browser-Clients.
        Wird aufgerufen wenn der Nutzer IDs im Einstellungs-Tab speichert.
        Kein Neustart, kein Reconnect – der Browser rendert sofort neu."""
        ml = _Handler.logger
        if ml:
            ml._broadcast({
                "type": "__ids_update__",
                "ids":  ids_dict,
            })

    def set_usv(self, usv_dict):
        """Aktualisiert USV-Daten fuer den /status-Endpunkt."""
        _Handler.usv_data = usv_dict

    def set_router(self, router_dict):
        """Aktualisiert Router-Daten fuer den /status-Endpunkt."""
        _Handler.router_data = router_dict

    def set_heartbeat(self, ts, addr=None):
        """Setzt Zeitstempel des letzten Repeater-Heartbeats."""
        _Handler.last_hb_ts   = ts
        _Handler.last_hb_addr = addr

    def stop(self):
        global _instance
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None
        _Handler.logger = None
        _instance = None
