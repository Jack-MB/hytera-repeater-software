# -*- coding: utf-8 -*-
"""
app.py  –  Hytera HR1065 Dashboard
btop-inspiriertes Tab-Design
Integriert: hytera_protocol.py + usv_monitor.py
"""
import os, json, time, queue, struct, threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

# ── optionale Abhängigkeiten ──────────────────────────────
try:
    import pyaudio, audioop
    AUDIO_OK = True
except ImportError:
    AUDIO_OK = False

try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False

def _apply_gain(pcm_bytes: bytes, factor: float) -> bytes:
    """Verstärkt 16-bit PCM mit Soft-Clipping (tanh-Limiter).
    Verhindert hartes digitales Clipping bei hohen Gain-Werten.
    Fällt auf audioop.mul zurück falls numpy fehlt."""
    if factor == 1.0:
        return pcm_bytes
    if _NUMPY_OK:
        arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        arr *= factor
        # Tanh-Soft-Limiter: verhindert hartes Clipping
        limit = 32767.0
        arr = np.tanh(arr / limit) * limit
        return arr.astype(np.int16).tobytes()
    else:
        # Fallback: audioop (hartes Clipping bei >1x)
        import audioop
        return audioop.mul(pcm_bytes, 2, min(factor, 4.0))

from hytera_protocol import (
    AudioListener, CallControlListener, GNSSListener,
    SMSListener, RRSListener, TelemetryListener,
    CallEvent, GPSFix, SMSMessage, RadioStatus, TelemetryEvent,
)
from usv_monitor import USVMonitor, UPSData
from mission_logger import MissionLogger, MISSIONS_DIR
from mission_server import MissionSSEServer
from sdr_monitor import SDRMonitor
try:
    from repeater_trap_monitor import RepeaterTrapMonitor, RepeaterAlarmState
    _TRAP_MON_OK = True
except ImportError:
    _TRAP_MON_OK = False

try:
    from zte_monitor import ZTEMonitor, ZTEData
    _ZTE_OK = True
except ImportError:
    _ZTE_OK = False

# ── Pfade ─────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
IDS_FILE    = os.path.join(BASE_DIR, "ids.json")
RECORD_DIR  = os.path.join(BASE_DIR, "recordings")
os.makedirs(RECORD_DIR, exist_ok=True)

DEFAULT_CFG = {
    "repeater_ip": "192.168.1.100",
    "listen_ip":   "0.0.0.0",
    "ts1_port":    30012,
    "ts2_port":    30014,
    "cc1_port":    30009,
    "cc2_port":    30010,
    "gps_port":    30003,
    "gps2_port":   30004,
    "sms_port":    30007,
    "sms2_port":   30008,
    "rrs1_port":   30001,
    "rrs2_port":   30002,
    "tele_port":   30005,
    "tele2_port":  30006,
    "timeout":     2.0,
    "audio_device":   -1,
    "audio_enabled":  True,
    "usv_ip":      "192.168.1.200",
    "usv_community":  "public",
    "usv_interval":   30,
    "usv_enabled":    True,
    # SDR (RTL-SDR v4) Settings
    "sdr_enabled":    False,
    "sdr_freq":       "446.00625",
    "sdr_gain":       "auto",
    "sdr_ppm":        0,
    # TP-Link Router SNMP Settings
    "router_enabled": False,
    "router_ip":      "192.168.0.1",
    "router_community": "public",
    # Tab-Sichtbarkeit (Dashboard + USV immer sichtbar)
    "tab_audio":   True,
    "tab_gps_sms": True,
    # ZTE MF281 (Telekom Speedbox 2) Monitor
    "zte_enabled": False,
    "zte_ip":      "192.168.0.1",
    "zte_password": "admin",
    "zte_interval": 15,
}

def load_config():
    cfg = DEFAULT_CFG.copy()
    try:
        if os.path.exists(CONFIG_FILE):
            cfg.update(json.load(open(CONFIG_FILE, encoding="utf-8")))
    except Exception:
        pass
    return cfg

def save_config(cfg):
    try:
        json.dump(cfg, open(CONFIG_FILE, "w", encoding="utf-8"), indent=2)
    except Exception:
        pass

# ── DMR-ID → Mitarbeiter ──────────────────────────────────
_ids: dict = {}   # { "1001": {"name": "Max M.", "geraet": "RT81 #1"} }

def load_ids():
    global _ids
    try:
        raw = json.load(open(IDS_FILE, encoding="utf-8"))
        _ids = {k: v for k, v in raw.items() if not k.startswith("_")}
    except Exception:
        _ids = {}

def save_ids():
    try:
        json.dump(_ids, open(IDS_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    except Exception:
        pass

def resolve_id(radio_id) -> str:
    """Gibt 'Name (ID)' zurück, oder nur 'ID' wenn unbekannt."""
    entry = _ids.get(str(radio_id))
    if entry:
        name   = entry.get("name", "?")
        geraet = entry.get("geraet", "")
        suffix = f"  [{geraet}]" if geraet else ""
        return f"{name}{suffix}  (ID {radio_id})"
    return f"ID {radio_id}"

load_ids()

# ── Cyberpunk // Night City Palette ───────────────────────
C = {
    "bg":    "#0a0a0f",
    "panel": "#0d0d1a",
    "panel2":"#080810",
    "border":"#1a1a2e",
    "acc":   "#fce300",   # Night City Yellow
    "acc2":  "#00fff7",   # Cyan
    "red":   "#ff003c",
    "red2":  "#3d0010",
    "grn":   "#39ff14",
    "grn2":  "#003d00",
    "org":   "#ff6b00",
    "tx":    "#e8e8e8",
    "tx2":   "#a0a0c0",
    "sub":   "#3a3a5a",
    "hdr":   "#050508",
    "sel":   "#1a1a00",
    "dim":   "#12121f",
    "ts1":   "#fce300",
    "ts2":   "#00fff7",
}

# ── Fonts ──────────────────────────────────────────
FONT_MONO   = ("Consolas",  9)
FONT_UI     = ("Consolas",  9)
FONT_BOLD   = ("Consolas",  9, "bold")
FONT_TITLE  = ("Consolas", 11, "bold")
FONT_LARGE  = ("Consolas", 11, "bold")
FONT_HUD    = ("Consolas", 15, "bold")
FONT_HUD_SM = ("Consolas", 10, "bold")
FONT_TINY   = ("Consolas",  7)

# ─────────────────────────────────────────────────────────
#  AudioPlayer
# ─────────────────────────────────────────────────────────
class AudioPlayer:
    """G.711 µ-law Live-Wiedergabe via pyaudio."""

    def __init__(self):
        self._pa = self._stream = self._thread = None
        self._q  = queue.Queue(maxsize=80)
        self._active = False
        if AUDIO_OK:
            try:
                self._pa = pyaudio.PyAudio()
            except Exception:
                pass

    def devices(self):
        out = [(-1, "🔊 System-Standard")]
        if not self._pa:
            return out
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if info.get("maxOutputChannels", 0) > 0:
                out.append((i, info.get("name", f"Gerät {i}")))
        return out

    def start(self, dev_idx=-1):
        if not AUDIO_OK or not self._pa:
            return
        self.stop()
        try:
            self._stream = self._pa.open(
                format=pyaudio.paInt16, channels=1, rate=8000,
                output=True,
                output_device_index=dev_idx if dev_idx >= 0 else None,
                frames_per_buffer=1024,
            )
            self._active = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        except Exception as e:
            print(f"Audio Fehler: {e}")

    def stop(self):
        self._active = False
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except Exception:
                break

    def feed(self, payload):
        """Nimmt rohen G.711-Payload (nach RTP-Strip)."""
        if not self._active or not payload:
            return
        try:
            pcm = audioop.ulaw2lin(payload, 2)
            vol = getattr(self, '_volume', 2.0)
            if vol != 1.0:
                pcm = _apply_gain(pcm, vol)
            self._q.put_nowait(pcm)
        except Exception:
            pass

    def set_volume(self, vol):
        self._volume = max(0.1, float(vol))

    def _loop(self):
        while self._active:
            try:
                pcm = self._q.get(timeout=0.1)
                if self._stream:
                    self._stream.write(pcm)
            except queue.Empty:
                pass
            except Exception:
                pass

    def terminate(self):
        self.stop()
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────
#  Hilfs-Widgets
# ─────────────────────────────────────────────────────────
def _lbl(parent, text, **kw):
    return tk.Label(parent, text=text,
                    bg=kw.pop("bg", C["panel"]),
                    fg=kw.pop("fg", C["sub"]),
                    font=kw.pop("font", FONT_UI),
                    **kw)

def _btn(parent, text, cmd, **kw):
    bg  = kw.pop("bg",  C["acc"])
    fg  = kw.pop("fg",  "#000000")
    abg = kw.pop("abg", C["acc2"])
    # Standard-Padding nur wenn Caller nichts uebergibt
    kw.setdefault("padx", 10)
    kw.setdefault("pady", 3)
    return tk.Button(parent, text=text, command=cmd,
                     bg=bg, fg=fg,
                     activebackground=abg, activeforeground="#000000",
                     relief="flat", cursor="hand2",
                     font=kw.pop("font", FONT_BOLD),
                     **kw)

def _sep(parent):
    tk.Frame(parent, bg=C["acc"], height=1).pack(fill="x", pady=4)


def _make_scrollable_tab(tab_frame):
    """Bettet den Inhalt eines Tab-Frames in einen scrollbaren Canvas ein.
    Gibt den inneren Frame zurück, in den Widgets gepackt werden sollen.
    Unterstützt Mausrad-Scrollen (Windows + Linux)."""
    # Äußerer Container: Canvas + Scrollbar nebeneinander
    outer = tk.Frame(tab_frame, bg=C["bg"])
    outer.pack(fill="both", expand=True)

    vbar = ttk.Scrollbar(outer, orient="vertical")
    vbar.pack(side="right", fill="y")

    canvas = tk.Canvas(outer, bg=C["bg"], bd=0, highlightthickness=0,
                       yscrollcommand=vbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    vbar.config(command=canvas.yview)

    # Innerer Frame der eigentlichen Inhalte
    inner = tk.Frame(canvas, bg=C["bg"])
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_frame_configure(event):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _on_canvas_configure(event):
        # Inneren Frame immer auf Canvas-Breite strecken
        canvas.itemconfig(win_id, width=event.width)

    inner.bind("<Configure>", _on_frame_configure)
    canvas.bind("<Configure>", _on_canvas_configure)

    # Mausrad-Scrollen (Windows: <MouseWheel>, Linux: <Button-4/5>)
    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_linux_up(event):
        canvas.yview_scroll(-1, "units")

    def _on_mousewheel_linux_down(event):
        canvas.yview_scroll(1, "units")

    def _bind_mousewheel(widget):
        """Bindet Mausrad rekursiv an alle Kindwidgets."""
        widget.bind("<MouseWheel>", _on_mousewheel, "+")
        widget.bind("<Button-4>",   _on_mousewheel_linux_up, "+")
        widget.bind("<Button-5>",   _on_mousewheel_linux_down, "+")
        for child in widget.winfo_children():
            _bind_mousewheel(child)

    # Initiales Binden; wird nach dem Befüllen des inneren Frames erneut aufgerufen
    inner.bind("<Map>", lambda e: _bind_mousewheel(inner))
    canvas.bind("<MouseWheel>", _on_mousewheel)

    return inner

def _panel(parent, title="", accent=None, **kw):
    """HUD-Panel mit 1px Akzentrand, Titelbalken und Eck-Notch."""
    acc = accent or C["acc"]
    outer = tk.Frame(parent, bg=acc, **kw)
    inner = tk.Frame(outer, bg=C["panel"])
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    if title:
        hdr = tk.Frame(inner, bg=C["hdr"])
        hdr.pack(fill="x")
        tk.Frame(hdr, bg=acc, width=3).pack(side="left", fill="y")
        tk.Label(hdr, text=f" {title.upper()} ",
                 font=FONT_BOLD, bg=C["hdr"], fg=acc).pack(side="left", padx=4, pady=3)
        tk.Label(hdr, text="◤", font=FONT_TINY,
                 bg=C["hdr"], fg=acc).pack(side="right", padx=3)
    inner._outer = outer
    return inner

def _hud_box(parent, label, value="—", accent=None, w=120, h=60):
    """Arasaka-style Status-Box mit Label und grossem Wert."""
    acc = accent or C["acc"]
    box = tk.Frame(parent, bg=C["dim"], width=w, height=h,
                   highlightbackground=acc, highlightthickness=1)
    box.pack_propagate(False)
    tk.Label(box, text=label.upper(), font=FONT_TINY,
             bg=C["dim"], fg=C["sub"]).pack(pady=(6, 0))
    val_lbl = tk.Label(box, text=value, font=FONT_HUD_SM,
                       bg=C["dim"], fg=acc)
    val_lbl.pack(pady=(2, 6))
    return box, val_lbl


# ─────────────────────────────────────────────────────────
#  Haupt-App
# ─────────────────────────────────────────────────────────
class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("HYTERA HR1065  //  MISSION CONTROL")
        self.configure(bg=C["bg"])
        self.minsize(900, 640)

        # ── DPI-Skalierung (Windows High-DPI aware) ──────────
        try:
            # Hole aktuellen DPI-Wert des Bildschirms
            dpi = self.winfo_fpixels('1i')   # Pixel pro Inch
            scale = dpi / 96.0               # 96 DPI = 100% = Basis
            # Nur skalieren wenn sinnvoll (>110% oder <90%)
            if scale > 1.1 or scale < 0.9:
                self.tk.call('tk', 'scaling', scale)
        except Exception:
            pass  # Tkinter-Version unterstützt das nicht → ignorieren

        self.cfg      = load_config()
        self._running = False
        self._listeners = []
        self._usv_mon   = None
        self._sdr_mon   = None
        self._router_mon = None
        self._trap_mon   = None
        self._zte_mon    = None   # ZTE MF281 Monitor
        self._repeater_alarm = None   # letzter RepeaterAlarmState
        self._audio     = AudioPlayer()
        self._last_hb   = 0.0
        self._hb_addr   = ""
        self._calls     = []   # CallEvent-Liste
        self._gps_fixes = {}   # radio_id -> GPSFix
        self._sms_list  = []   # SMSMessage-Liste
        self._online    = {}   # radio_id -> bool
        self._usv_data  = UPSData()
        self._mission   = MissionLogger()   # Einsatz-Logger

        self._build_style()
        self._build_header()
        self._build_notebook()
        self._apply_cfg()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick_header()

    # ── ttk-Style ─────────────────────────────────────────
    def _build_style(self):
        s = ttk.Style(self)
        s.theme_use("default")
        s.configure("TNotebook",
                     background=C["bg"], borderwidth=0)
        s.configure("TNotebook.Tab",
                     background=C["panel"], foreground=C["sub"],
                     font=FONT_BOLD, padding=[14, 6],
                     borderwidth=0)
        s.map("TNotebook.Tab",
              background=[("selected", C["hdr"])],
              foreground=[("selected", C["acc"])])
        s.configure("Vertical.TScrollbar",
                     background=C["border"], troughcolor=C["hdr"],
                     arrowcolor=C["acc"], borderwidth=0, width=6)
        s.configure("TCombobox",
                     fieldbackground=C["bg"], background=C["panel"],
                     foreground=C["acc"], selectbackground=C["hdr"],
                     selectforeground=C["acc"])

    # ── Header ────────────────────────────────────────────
    def _build_header(self):
        hdr = tk.Frame(self, bg=C["hdr"], height=56)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        # Logo-Placeholder (spaeter Firmenlogo)
        logo_box = tk.Frame(hdr, bg=C["dim"],
                            highlightbackground=C["acc"], highlightthickness=1,
                            width=100, height=40)
        logo_box.pack(side="left", padx=(10, 0), pady=8)
        logo_box.pack_propagate(False)
        tk.Label(logo_box, text="[ LOGO ]", font=("Consolas", 8),
                 bg=C["dim"], fg=C["sub"]).pack(expand=True)
        # Trenner
        tk.Frame(hdr, bg=C["sub"], width=1).pack(side="left", fill="y", pady=10, padx=8)
        # Titel
        tk.Label(hdr, text="//  HYTERA HR1065  //  MISSION CONTROL",
                 font=FONT_TITLE, bg=C["hdr"], fg=C["acc"]).pack(side="left", pady=8)
        # Verbindungs-Dot (rechts)
        self._dot = tk.Label(hdr, text="\u25cf", font=("Consolas", 16),
                              bg=C["hdr"], fg=C["sub"])
        self._dot.pack(side="right", padx=(0, 14))
        self._lbl_conn = tk.Label(hdr, text="NO SIGNAL",
                                   font=FONT_UI, bg=C["hdr"], fg=C["sub"])
        self._lbl_conn.pack(side="right", padx=(8, 2))
        # Uhr
        self._lbl_clock = tk.Label(hdr, text="", font=FONT_MONO,
                                    bg=C["hdr"], fg=C["acc"])
        self._lbl_clock.pack(side="right", padx=16)
        # Akzentbalken unten + Scan-Linie
        tk.Frame(self, bg=C["acc"], height=2).pack(fill="x")
        tk.Frame(self, bg=C["dim"], height=1).pack(fill="x")

    # ── Notebook ──────────────────────────────────────────
    def _build_notebook(self):
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill="both", expand=True, padx=6, pady=6)
        self._build_tab_dashboard()   # immer sichtbar
        self._build_tab_einsatz()     # Einsatz-Zeitstrahl
        self._build_tab_audio()       # versteckbar
        self._build_tab_gps_sms()     # versteckbar
        self._build_tab_usv()         # immer sichtbar
        self._build_tab_settings()    # immer sichtbar
        self._apply_tab_visibility()

    # ══════════════════════════════════════════════════════
    #  TAB 1 – DASHBOARD
    # ══════════════════════════════════════════════════════
    def _build_tab_dashboard(self):
        tab = tk.Frame(self._nb, bg=C["bg"])
        self._nb.add(tab, text="  📊 Dashboard  ")
        tab = _make_scrollable_tab(tab)   # Scrollbar für Dashboard

        # Obere Reihe: Slot-Status + Anruf-Log
        top = tk.Frame(tab, bg=C["bg"])
        top.pack(fill="x", padx=8, pady=(8, 0))

        # --- Slot-Status-Panels (HUD-Stil) ---
        self._slot_panels = {}
        for slot, acc in (("TS1", C["ts1"]), ("TS2", C["ts2"])):
            pnl = _panel(top, title=f"Zeitschlitz {slot[-1]}", accent=acc)
            pnl._outer.pack(side="left", padx=(0, 6), pady=0, ipadx=6, ipady=4)
            st = tk.Label(pnl, text="GESTOPPT", font=FONT_HUD,
                          bg=C["panel"], fg=C["sub"])
            st.pack(padx=12, pady=(6, 2))
            ind = tk.Frame(pnl, bg=C["sub"], height=3)
            ind.pack(fill="x", padx=12, pady=(0, 4))
            bc = tk.Label(pnl, text="0 Pakete  |  0 KB",
                          font=FONT_MONO, bg=C["panel"], fg=C["sub"])
            bc.pack(padx=12, pady=(0, 2))
            rssi_lbl = tk.Label(pnl, text="Signal: –",
                                font=FONT_TINY, bg=C["panel"], fg=C["sub"])
            rssi_lbl.pack(padx=12, pady=(0, 6))
            self._slot_panels[slot] = {"status": st, "bytes": bc,
                                       "ind": ind, "acc": acc, "rssi": rssi_lbl}
        # --- Aktiver Anruf ---
        call_pnl = _panel(top, title="Aktueller Anruf")
        call_pnl._outer.pack(side="left", padx=(0, 6), fill="both", expand=True)
        self._lbl_call = tk.Label(call_pnl, text="– kein Anruf –",
                                   font=FONT_HUD, bg=C["panel"], fg=C["sub"])
        self._lbl_call.pack(padx=12, pady=(8, 2))
        self._lbl_call_detail = tk.Label(call_pnl, text="",
                                          font=FONT_MONO, bg=C["panel"],
                                          fg=C["tx2"])
        self._lbl_call_detail.pack(padx=12, pady=(0, 8))

        # --- Radio-Online-Liste ---
        radio_pnl = _panel(top, title="Radios Online", accent=C["grn"])
        radio_pnl._outer.pack(side="left", padx=0, fill="both", expand=True)
        self._lbx_radios = tk.Listbox(radio_pnl, bg=C["panel"], fg=C["grn"],
                                       font=FONT_MONO, relief="flat",
                                       height=4, width=18,
                                       selectbackground=C["sel"],
                                       borderwidth=0)
        self._lbx_radios.pack(fill="both", expand=True, padx=4, pady=4)

        # Start/Stop-Buttons
        bf = tk.Frame(tab, bg=C["bg"])
        bf.pack(fill="x", padx=8, pady=6)
        self._btn_start = _btn(bf, "▶  Server starten", self._start,
                                bg=C["grn"], fg="#000", font=("Consolas", 10, "bold"),
                                padx=20, pady=8)
        self._btn_start.pack(side="left", padx=(0, 6))
        self._btn_stop = _btn(bf, "■  Stoppen", self._stop,
                               bg=C["red"], fg="#fff", font=("Consolas", 10, "bold"),
                               padx=20, pady=8, state="disabled")
        self._btn_stop.pack(side="left", padx=(0, 6))
        _btn(bf, "💾 Konfig", self._save_cfg,
             bg=C["sub"], fg=C["tx"], padx=12, pady=8).pack(side="left")
        _btn(bf, "📡 Aufnahmen",
             lambda: os.startfile(RECORD_DIR),
             bg=C["sub"], fg=C["tx"], padx=12, pady=8).pack(side="right")

        # ── PTT + USV-Panel ──────────────────────────────
        mid = tk.Frame(tab, bg=C["bg"])
        mid.pack(fill="x", padx=8, pady=(0, 4))


        # USV-Schnellstatus (HUD-Box-Raster)
        usv_quick = _panel(mid, title="\u26a1 USV-Status", accent=C["acc2"])
        usv_quick._outer.pack(side="left", fill="both", expand=True)
        ug = tk.Frame(usv_quick, bg=C["panel"])
        ug.pack(fill="x", padx=8, pady=8)
        self._usv_quick_labels = {}
        for i, (key, lbl, acc) in enumerate([
            ("batt",  "Batterie",     C["grn"]),
            ("netz",  "Netz",         C["acc"]),
            ("volt",  "Spannung",     C["acc2"]),
            ("laufz", "Restlaufzeit", C["acc2"]),
            ("last",  "Last",         C["org"]),
        ]):
            box, val = _hud_box(ug, lbl, accent=acc, w=110, h=58)
            box.grid(row=0, column=i, padx=4, pady=4, sticky="nsew")
            self._usv_quick_labels[key] = val

        # Router-Schnellstatus
        router_quick = _panel(mid, title="🌐 TP-Link Router", accent=C["org"])
        router_quick._outer.pack(side="left", fill="both", expand=True, padx=(6, 0))
        rg = tk.Frame(router_quick, bg=C["panel"])
        rg.pack(fill="x", padx=8, pady=8)
        self._router_quick_labels = {}
        for i, (key, lbl, acc) in enumerate([
            ("sys",  "Name",    C["org"]),
            ("up",   "Uptime",  C["tx"]),
            ("stat", "Status",  C["grn"]),
            ("cpu",  "CPU",     C["acc2"]),
            ("rx",   "Download", C["sub"]),
            ("tx",   "Upload",   C["sub"]),
        ]):
            box, val = _hud_box(rg, lbl, accent=acc, w=90, h=58)
            box.grid(row=0, column=i, padx=4, pady=4, sticky="nsew")
            self._router_quick_labels[key] = val

        # ── ZTE MF281 (Telekom Speedbox 2) Schnellstatus ─────────
        zte_mid = tk.Frame(tab, bg=C["bg"])
        zte_mid.pack(fill="x", padx=8, pady=(0, 4))
        zte_quick = _panel(zte_mid, title="📡 ZTE MF281  (Telekom Speedbox 2)", accent=C["acc2"])
        zte_quick._outer.pack(fill="x")
        zg = tk.Frame(zte_quick, bg=C["panel"])
        zg.pack(fill="x", padx=8, pady=8)
        self._zte_labels = {}
        for i, (key, lbl, acc) in enumerate([
            ("akku",     "Akku",       C["grn"]),
            ("charge",   "Ladung",     C["acc"]),
            ("signal",   "Signal",     C["acc2"]),
            ("provider", "Anbieter",   C["org"]),
            ("wan",      "WAN",        C["acc2"]),
            ("netz",     "Netztyp",    C["org"]),
            ("dl",       "Download",   C["acc2"]),
            ("ul",       "Upload",     C["sub"]),
        ]):
            box, val = _hud_box(zg, lbl, value="–", accent=acc, w=100, h=58)
            box.grid(row=0, column=i, padx=4, pady=4, sticky="nsew")
            zg.columnconfigure(i, weight=1)
            self._zte_labels[key] = val
        # Status-Zeile
        self._zte_status_lbl = tk.Label(
            zte_quick, text="  ZTE Monitor nicht aktiviert (Einstellungen → ZTE)",
            font=FONT_TINY, bg=C["panel"], fg=C["sub"])
        self._zte_status_lbl.pack(anchor="w", padx=8, pady=(0, 4))

        # ── Repeater Alarm-Panel (SNMP Traps HR1065) ──────
        rep_row = tk.Frame(tab, bg=C["bg"])
        rep_row.pack(fill="x", padx=8, pady=(0, 4))
        rep_pnl = _panel(rep_row, title="📡 HR1065 Repeater Alarme  [SNMP Trap]",
                         accent=C["red"])
        rep_pnl._outer.pack(fill="x")
        rp = tk.Frame(rep_pnl, bg=C["panel"])
        rp.pack(fill="x", padx=8, pady=6)
        self._rep_alarm_labels = {}
        _alarm_defs = [
            ("vswr",        "VSWR",     C["red"]),
            ("temperatur",  "Temp",     C["org"]),
            ("luefter",     "Luefter",  C["org"]),
            ("tx_pll",      "TX-PLL",   C["red"]),
            ("rx_pll",      "RX-PLL",   C["red"]),
            ("spannung",    "Spannung", C["org"]),
            ("fw_leistung", "Fwd-Pwr",  C["acc2"]),
            ("rw_leistung", "Rfl-Pwr",  C["acc2"]),
            ("batterie",    "Batterie", C["acc"]),
        ]
        for i, (key, lbl, acc) in enumerate(_alarm_defs):
            box, val = _hud_box(rp, lbl, value="?", accent=C["sub"], w=88, h=52)
            box.grid(row=0, column=i, padx=3, pady=4, sticky="nsew")
            self._rep_alarm_labels[key] = (box, val)
            rp.columnconfigure(i, weight=1)
        # Status-Zeile
        self._rep_status_lbl = tk.Label(
            rep_pnl, text="  Warte auf Traps vom Repeater (UDP :162)...",
            font=FONT_TINY, bg=C["panel"], fg=C["sub"])
        self._rep_status_lbl.pack(anchor="w", padx=8, pady=(0, 4))

        # Protokoll-Log
        log_pnl = _panel(tab, title="Protokoll")
        log_pnl._outer.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._log = tk.Text(log_pnl, bg="#050508", fg=C["tx"],
                             font=FONT_MONO, relief="flat",
                             state="disabled", wrap="word",
                             insertbackground=C["acc"])
        sb = ttk.Scrollbar(log_pnl, orient="vertical",
                            command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._log.pack(fill="both", expand=True, padx=2, pady=2)
        self._log.tag_config("ok",   foreground=C["grn"])
        self._log.tag_config("err",  foreground=C["red"])
        self._log.tag_config("warn", foreground=C["org"])
        self._log.tag_config("acc",  foreground=C["acc"])
        self._log.tag_config("sub",  foreground=C["sub"])
        self._log.tag_config("ptt",  foreground=C["acc2"])

    # ══════════════════════════════════════════════════════
    #  TAB 2 – EINSATZ-ZEITSTRAHL
    # ══════════════════════════════════════════════════════
    def _build_tab_einsatz(self):
        tab = tk.Frame(self._nb, bg=C["bg"])
        self._nb.add(tab, text="  \U0001f4cb Einsatz  ")

        # ── Obere Steuerleiste ────────────────────────────
        ctrl = _panel(tab, title="⚡ Einsatz-Steuerung", accent=C["acc"])
        ctrl._outer.pack(fill="x", padx=8, pady=(8, 4))

        top = tk.Frame(ctrl, bg=C["panel"]); top.pack(fill="x", padx=10, pady=8)

        tk.Label(top, text="Einsatz-Name:", font=FONT_BOLD,
                 bg=C["panel"], fg=C["sub"]).pack(side="left", padx=(0, 6))
        import tkinter.ttk as ttk
        self._var_mission_name = tk.StringVar(
            value=datetime.now().strftime("Einsatz_%Y-%m-%d"))
            
        # Lade existierende Einsätze für die Dropdown-Liste
        existing_missions = []
        if os.path.isdir(MISSIONS_DIR):
            try:
                existing_missions = sorted(
                    [d for d in os.listdir(MISSIONS_DIR) if os.path.isdir(os.path.join(MISSIONS_DIR, d))],
                    reverse=True
                )
            except Exception: pass

        self._cb_mission = ttk.Combobox(top, textvariable=self._var_mission_name, 
                                        values=existing_missions, width=28, font=FONT_MONO)
        self._cb_mission.pack(side="left", padx=(0, 12))

        self._btn_mission_start = _btn(top, "\u25b6 Starten / Fortsetzen",
                                       self._mission_start,
                                       bg=C["grn"], fg="#000", font=("Consolas", 10, "bold"),
                                       padx=14, pady=6)
        self._btn_mission_start.pack(side="left", padx=(0, 6))

        self._btn_mission_stop = _btn(top, "\u25a0  Beenden",
                                      self._mission_stop,
                                      bg=C["red"], fg="#fff", font=("Consolas", 10, "bold"),
                                      padx=14, pady=6, state="disabled")
        self._btn_mission_stop.pack(side="left", padx=(0, 12))

        self._lbl_mission_status = tk.Label(top, text="Kein aktiver Einsatz",
                                             font=FONT_MONO, bg=C["panel"], fg=C["sub"])
        self._lbl_mission_status.pack(side="left")

        # Zeitanzeige laufend
        self._lbl_mission_time = tk.Label(top, text="",
                                           font=("Consolas", 12, "bold"),
                                           bg=C["panel"], fg=C["acc"])
        self._lbl_mission_time.pack(side="right", padx=10)

        # ── Live-Links (erscheinen wenn Einsatz aktiv) ────────
        link_row = tk.Frame(ctrl, bg=C["panel"])
        link_row.pack(fill="x", padx=10, pady=(0, 4))

        tk.Label(link_row, text="Live:", font=FONT_BOLD,
                 bg=C["panel"], fg=C["sub"]).pack(side="left", padx=(0, 8))

        self._lnk_timeline = tk.Label(link_row,
            text="–", font=FONT_MONO,
            bg=C["panel"], fg=C["sub"], cursor="hand2")
        self._lnk_timeline.pack(side="left", padx=(0, 16))

        self._lnk_report = tk.Label(link_row,
            text="–", font=FONT_MONO,
            bg=C["panel"], fg=C["sub"], cursor="hand2")
        self._lnk_report.pack(side="left")

        # Tailscale-Remote-Link
        tk.Label(link_row, text="|",
                 bg=C["panel"], fg=C["sub"]).pack(side="left", padx=8)
        tk.Label(link_row, text="VPN:", font=FONT_BOLD,
                 bg=C["panel"], fg=C["sub"]).pack(side="left", padx=(0, 6))
        self._lnk_vpn = tk.Label(link_row,
            text="–", font=FONT_MONO,
            bg=C["panel"], fg=C["sub"], cursor="hand2")
        self._lnk_vpn.pack(side="left", padx=(0, 10))
        # Tailscale Test-Button
        self._btn_ts_test = _btn(link_row, "\U0001f6f0 Tailscale testen",
            self._tailscale_test,
            bg=C["sub"], fg=C["tx"], padx=8, pady=2,
            font=("Consolas", 8, "bold"))
        self._btn_ts_test.pack(side="left")
        self._lbl_ts_status = tk.Label(link_row, text="",
            font=FONT_MONO, bg=C["panel"], fg=C["sub"])
        self._lbl_ts_status.pack(side="left", padx=(6, 0))

        # ── Notiz & Bild hinzufügen ───────────────────────────
        act = tk.Frame(ctrl, bg=C["panel"]); act.pack(fill="x", padx=10, pady=(0, 8))
        self._var_note = tk.StringVar()
        tk.Entry(act, textvariable=self._var_note, width=40,
                 bg=C["bg"], fg=C["tx"], insertbackground=C["tx"],
                 relief="flat", font=FONT_MONO,
                 ).pack(side="left", padx=(0, 6))
        _btn(act, "\U0001f4dd Notiz", self._mission_add_note,
             bg=C["sub"], fg=C["tx"], padx=10, pady=4).pack(side="left", padx=(0, 6))
        _btn(act, "\U0001f5bc Bild", self._mission_add_image,
             bg=C["sub"], fg=C["tx"], padx=10, pady=4).pack(side="left", padx=(0, 12))
        _btn(act, "\U0001f310 Timeline", self._mission_open_html,
             bg=C["acc"], fg=C["bg"], font=("Consolas", 9, "bold"),
             padx=14, pady=4).pack(side="left", padx=(0, 6))
        _btn(act, "\U0001f4cb Protokoll (Live)", self._mission_open_report,
             bg=C["acc2"], fg=C["bg"], font=("Consolas", 9, "bold"),
             padx=14, pady=4).pack(side="left")

        # ── Ereignis-Log (Live) ───────────────────────────
        log_pnl = _panel(tab, title="Einsatz-Ereignisse (Live)")
        log_pnl._outer.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self._mission_log = tk.Text(log_pnl, bg="#0d0d0d", fg=C["tx"],
                                    font=FONT_MONO, relief="flat",
                                    state="disabled", wrap="word", height=8)
        sb_m = ttk.Scrollbar(log_pnl, orient="vertical",
                              command=self._mission_log.yview)
        self._mission_log.configure(yscrollcommand=sb_m.set)
        sb_m.pack(side="right", fill="y")
        self._mission_log.pack(fill="both", expand=True, padx=2, pady=2)
        self._mission_log.tag_config("ptt",  foreground=C["acc"])
        self._mission_log.tag_config("rrs",  foreground=C["grn"])
        self._mission_log.tag_config("note", foreground=C["org"])
        self._mission_log.tag_config("img",  foreground=C["acc2"])

        # ── Gespeicherte Einsätze ─────────────────────────
        hist_pnl = _panel(tab, title="Gespeicherte Einsätze", accent=C["acc2"])
        hist_pnl._outer.pack(fill="x", padx=8, pady=(0, 8))
        hf = tk.Frame(hist_pnl, bg=C["panel"]); hf.pack(fill="x", padx=8, pady=6)
        self._lbx_missions = tk.Listbox(hf, bg="#0d0d0d", fg=C["acc2"],
                                         font=FONT_MONO, relief="flat",
                                         height=4, selectbackground=C["sel"],
                                         borderwidth=0)
        self._lbx_missions.pack(side="left", fill="both", expand=True)
        bf2 = tk.Frame(hf, bg=C["panel"]); bf2.pack(side="left", padx=8, fill="y")
        _btn(bf2, "\U0001f504 Aktualisieren", self._mission_refresh_list,
             bg=C["sub"], fg=C["tx"], padx=10, pady=5).pack(fill="x", pady=2)
        _btn(bf2, "\U0001f310 Öffnen", self._mission_open_selected,
             bg=C["acc"], fg=C["bg"], padx=10, pady=5).pack(fill="x", pady=2)
        _btn(bf2, "\U0001f4c2 Ordner", self._mission_open_folder,
             bg=C["sub"], fg=C["tx"], padx=10, pady=5).pack(fill="x", pady=2)
        self._mission_refresh_list()

    # ── Einsatz-Methoden ──────────────────────────────────
    def _mission_start(self):
        name = self._var_mission_name.get().strip() or \
               datetime.now().strftime("Einsatz_%Y-%m-%d_%H-%M-%S")

        try:
            self._mission.start(name)
        except Exception as e:
            messagebox.showerror("Einsatz-Fehler",
                f"Fehler beim Laden des Einsatzes:\n{e}")
            return

        self._btn_mission_start.config(state="disabled")
        self._btn_mission_stop.config(state="normal")
        self._lbl_mission_status.config(
            text=f"Aktiv: {self._mission.name}", fg=C["grn"])

        # ── Ereignis-Log leeren und Historik einfügen ─────────
        self._mission_log.config(state="normal")
        self._mission_log.delete("1.0", "end")
        self._mission_log.config(state="disabled")

        ev_count = 0
        for ev in self._mission.events:
            try:
                try:
                    ts = datetime.fromisoformat(ev.abs_time).strftime("%H:%M:%S")
                except Exception:
                    ts = None

                if ev.etype == "ptt_start":
                    rid      = ev.data.get("radio_id", "?")
                    slot     = ev.data.get("slot", "")
                    self._mission_log_entry(
                        f"\U0001f4fb {resolve_id(rid)} funkt... ({slot})",
                        tag="ptt", custom_time=ts)
                    ev_count += 1
                elif ev.etype == "ptt_end":
                    rid  = ev.data.get("radio_id", "?")
                    dur  = float(ev.data.get("duration",
                                 ev.data.get("duration_sec",
                                 ev.data.get("dur", 0))))
                    auto = ev.data.get("auto_closed", False)
                    sfx  = "  \u26a0 auto" if auto else ""
                    self._mission_log_entry(
                        f"\U0001f4fb {resolve_id(rid)} Ende ({dur:.1f}s){sfx}",
                        tag="ptt", custom_time=ts)
                    ev_count += 1
                elif ev.etype == "note":
                    self._mission_log_entry(
                        f"\U0001f4dd {ev.data.get('text','')}",
                        tag="note", custom_time=ts)
                    ev_count += 1
                elif ev.etype == "image":
                    self._mission_log_entry(
                        f"\U0001f5bc Bild: {ev.data.get('caption','')}",
                        tag="img", custom_time=ts)
                    ev_count += 1
                elif ev.etype == "online":
                    self._mission_log_entry(
                        f"\u2714 {resolve_id(ev.data.get('radio_id','?'))} angemeldet",
                        tag="rrs", custom_time=ts)
                    ev_count += 1
                elif ev.etype == "offline":
                    self._mission_log_entry(
                        f"\u2716 {resolve_id(ev.data.get('radio_id','?'))} abgemeldet",
                        tag="rrs", custom_time=ts)
                    ev_count += 1
                elif ev.etype == "sms":
                    self._mission_log_entry(
                        f"\U0001f4ac SMS von {resolve_id(ev.data.get('sender_id','?'))}: "
                        f"{ev.data.get('text','')}",
                        tag="note", custom_time=ts)
                    ev_count += 1
            except Exception as _ev_err:
                # Einzelnes Event fehlerhaft – ignorieren, Rest weiter laden
                self._log_msg(f"[Einsatz] Replay-Fehler {ev.etype}: {_ev_err}", "warn")

        is_resume = ev_count > 0
        status_txt = f"Fortgesetzt ({ev_count} Ereignisse)" if is_resume else "Gestartet"
        self._mission_log_entry(f"=== Einsatz {status_txt}: {self._mission.name} ===", "note")
        self._tick_mission_time()
        self._log_msg(f"[Einsatz] {'Fortgesetzt' if is_resume else 'Gestartet'}: "
                      f"{self._mission.name}  ({ev_count} Ereignisse geladen)", "ok")

        # Master-Aufnahmedateien für alle Audio-Listener öffnen
        from hytera_protocol import AudioListener as _AL
        for lst in self._listeners:
            if isinstance(lst, _AL):
                self._mission.open_master_rec(lst.slot_name)

        # ── SSE-Server starten ────────────────────────────────
        self._sse_url = ""
        for _port_attempt in range(3):   # Versuche mit ggf. freiem Port
            try:
                self._sse_server = MissionSSEServer(self._mission)
                url = self._sse_server.start()
                self._sse_url = url
                break
            except Exception as e:
                self._log_msg(f"[Live] SSE Versuch {_port_attempt+1} gescheitert: {e}", "warn")
                import time as _t; _t.sleep(0.5)
        else:
            self._log_msg("[Live] SSE-Server konnte nicht gestartet werden!", "err")

        if self._sse_url:
            self._log_msg(f"[Live] Timeline:   {self._sse_url}", "ok")
            self._log_msg(f"[Live] Protokoll:  {self._sse_url}report", "ok")

            def _open_br(u):
                import webbrowser, time as _t
                webbrowser.open(u + "?t=" + str(int(_t.time())))
            _open_br(self._sse_url)

            tl_url  = self._sse_url
            rep_url = self._sse_url + "report"
            self._lnk_timeline.config(
                text="\U0001f310 Timeline öffnen", fg=C["acc"], cursor="hand2")
            self._lnk_timeline.bind(
                "<Button-1>", lambda e, u=tl_url: _open_br(u))
            self._lnk_report.config(
                text="\U0001f4cb Protokoll (Live)", fg="#9fa8da", cursor="hand2")
            self._lnk_report.bind(
                "<Button-1>", lambda e, u=rep_url:
                    __import__('webbrowser').open(u))

        # ── Tailscale-VPN-URL anzeigen (asynchron) ────────────
        _local_url = self._sse_url
        def _check_ts(local_url=_local_url):
            try:
                from tailscale_helper import get_remote_url
                import webbrowser as _wb
                ts_url = get_remote_url(local_url)
                if ts_url:
                    self._log_msg(f"[VPN]  Extern (Tailscale): {ts_url}", "ok")
                    self.after(0, self._lnk_vpn.config,
                        {"text": "\U0001f6f0 VPN-Link", "fg": "#80cbc4", "cursor": "hand2"})
                    self.after(0, self._lnk_vpn.bind,
                        "<Button-1>", lambda e, u=ts_url: _wb.open(u))
                else:
                    self._log_msg("[VPN]  Tailscale nicht aktiv", "sub")
            except Exception as ex:
                self._log_msg(f"[VPN]  Fehler: {ex}", "warn")
        import threading as _thr
        _thr.Thread(target=_check_ts, daemon=True).start()

    def _mission_stop(self):
        if not self._mission.active: return
        self._mission.stop()
        if hasattr(self, '_sse_server') and self._sse_server:
            self._sse_server.stop()
        self._lnk_timeline.config(text="\u2013", fg=C["sub"])
        self._lnk_timeline.unbind("<Button-1>")
        self._lnk_report.config(text="\u2013", fg=C["sub"])
        self._lnk_report.unbind("<Button-1>")
        self._lnk_vpn.config(text="\u2013", fg=C["sub"])
        self._lnk_vpn.unbind("<Button-1>")
        self._btn_mission_start.config(state="normal")
        self._btn_mission_stop.config(state="disabled")
        self._lbl_mission_status.config(text="Kein aktiver Einsatz", fg=C["sub"])
        self._lbl_mission_time.config(text="")
        self._mission_log_entry("=== Einsatz beendet ===", "note")
        self._log_msg("[Einsatz] Beendet.", "warn")
        self._mission_refresh_list()
        # HTML automatisch generieren
        try:
            path = self._mission.export_html(ids_map=_ids)
            self._log_msg(f"[Einsatz] Timeline gespeichert: {os.path.basename(path)}", "ok")
        except Exception as e:
            self._log_msg(f"[Einsatz] HTML-Fehler: {e}", "err")

    def _mission_add_note(self):
        txt = self._var_note.get().strip()
        if not txt:
            messagebox.showwarning("Notiz", "Bitte Text eingeben.")
            return
        if not self._mission.active:
            messagebox.showwarning("Einsatz", "Kein Einsatz aktiv.")
            return
        self._mission.add_note(txt)
        self._var_note.set("")
        self._mission_log_entry(f"\U0001f4dd Notiz: {txt}", "note")
        self._log_msg(f"[Einsatz] Notiz: {txt}", "acc")

    def _mission_add_image(self):
        if not self._mission.active:
            messagebox.showwarning("Einsatz", "Kein Einsatz aktiv."); return
        path = filedialog.askopenfilename(
            title="Bild auswählen",
            filetypes=[("Bilder", "*.jpg *.jpeg *.png *.bmp *.gif"), ("Alle", "*.*")])
        if not path: return
        caption = self._var_note.get().strip() or os.path.basename(path)
        self._mission.add_image(path, caption)
        self._mission_log_entry(f"\U0001f5bc Bild: {caption}", "img")
        self._log_msg(f"[Einsatz] Bild: {caption}", "acc")

    def _mission_open_html(self):
        if not self._mission.active and not self._mission.events:
            self._mission_open_selected(); return
        try:
            path = self._mission.export_html(ids_map=_ids)
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def _mission_open_report(self):
        """Oeffnet das Live-Funkprotokoll im Browser."""
        import webbrowser
        url = getattr(self, '_sse_url', None)
        if url and getattr(self, '_sse_server', None):
            webbrowser.open(url + "report")
        elif self._mission.mission_dir:
            # Kein Live-Server → statisch generieren
            try:
                ids_map = {}
                try:
                    import json as _json
                    ids_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ids.json")
                    with open(ids_path, encoding="utf-8") as f:
                        ids_map = _json.load(f)
                except Exception:
                    pass
                path = self._mission.generate_report(ids_map=ids_map, live=False)
                os.startfile(path)
            except Exception as e:
                messagebox.showerror("Fehler", str(e))
        else:
            messagebox.showinfo("Protokoll", "Bitte zuerst einen Einsatz starten.")

    def _mission_refresh_list(self):
        self._lbx_missions.delete(0, "end")
        for name, _ in MissionLogger.list_missions():
            self._lbx_missions.insert("end", f"  {name}")

    def _mission_open_selected(self):
        sel = self._lbx_missions.curselection()
        if not sel:
            messagebox.showinfo("Hinweis", "Einsatz in der Liste auswählen."); return
        name = self._lbx_missions.get(sel[0]).strip()
        # Zuerst neue standalone timeline.html suchen, dann Fallback
        html = os.path.join(MISSIONS_DIR, name, "timeline.html")
        if not os.path.exists(html):
            html = os.path.join(MISSIONS_DIR, name, "timeline_viewer.html")
        if not os.path.exists(html):
            # Neu generieren
            try:
                json_path = os.path.join(MISSIONS_DIR, name, "mission.json")
                data = MissionLogger.load_from_json(json_path)
                from mission_logger import _build_standalone_html
                # Audio-Segmente aus mission.json ableiten (WAV-Dateien müssen vorhanden sein)
                content = _build_standalone_html(data)
                html = os.path.join(MISSIONS_DIR, name, "timeline.html")
                with open(html, "w", encoding="utf-8") as f: f.write(content)
            except Exception as e:
                messagebox.showerror("Fehler", str(e)); return
        os.startfile(html)

    def _mission_open_folder(self):
        os.startfile(MISSIONS_DIR)

    def _tailscale_test(self):
        """Prueft Tailscale-Verbindung unabhaengig von USV/Repeater."""
        self._lbl_ts_status.config(text="pruefe...", fg=C["sub"])
        self._btn_ts_test.config(state="disabled")
        def _check():
            try:
                from tailscale_helper import get_tailscale_ip, get_remote_url
                import webbrowser
                ts_ip = get_tailscale_ip()
                if ts_ip:
                    local_url = getattr(self, "_sse_url", "") or f"http://127.0.0.1:19080/"
                    ts_url = get_remote_url(local_url) or f"http://{ts_ip}:19080/"
                    def _ok():
                        self._lbl_ts_status.config(
                            text=f"✓  {ts_ip}  →  {ts_url}", fg=C["grn"])
                        self._btn_ts_test.config(state="normal")
                        self._log_msg(f"[VPN]  Tailscale aktiv: {ts_ip}", "ok")
                        self._log_msg(f"[VPN]  Dashboard-URL:   {ts_url}", "ok")
                        self._lnk_vpn.config(
                            text="\U0001f6f0 VPN-Link", fg="#80cbc4", cursor="hand2")
                        self._lnk_vpn.bind("<Button-1>",
                            lambda e, u=ts_url: webbrowser.open(u))
                    self.after(0, _ok)
                else:
                    def _fail():
                        self._lbl_ts_status.config(
                            text="✗  Tailscale nicht aktiv", fg=C["red"])
                        self._btn_ts_test.config(state="normal")
                        self._log_msg(
                            "[VPN]  Tailscale nicht gefunden – tailscale.com", "warn")
                    self.after(0, _fail)
            except Exception as ex:
                def _err():
                    self._lbl_ts_status.config(text=f"✗  Fehler: {ex}", fg=C["red"])
                    self._btn_ts_test.config(state="normal")
                self.after(0, _err)
        import threading
        threading.Thread(target=_check, daemon=True).start()

    def _mission_log_entry(self, msg, tag="", custom_time=None):
        self._mission_log.config(state="normal")
        ts = custom_time if custom_time else datetime.now().strftime("%H:%M:%S")
        self._mission_log.insert("end", f"[{ts}] {msg}\n", tag)
        self._mission_log.see("end")
        self._mission_log.config(state="disabled")

    def _tick_mission_time(self):
        if not self._mission.active: return
        elapsed = time.time() - self._mission.start_ts
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        self._lbl_mission_time.config(text=f"{h:02d}:{m:02d}:{s:02d}")
        self.after(1000, self._tick_mission_time)

    # ══════════════════════════════════════════════════════
    #  TAB 3 – AUDIO
    # ══════════════════════════════════════════════════════
    def _build_tab_audio(self):

        tab = tk.Frame(self._nb, bg=C["bg"])
        self._nb.add(tab, text="  🔊 Audio  ")
        self._tab_audio = tab   # Referenz für hide/show

        pnl = _panel(tab, title="Live-Audio  (G.711 µ-law → PCM)", accent=C["acc2"])
        pnl._outer.pack(fill="x", padx=8, pady=8)

        row1 = tk.Frame(pnl, bg=C["panel"])
        row1.pack(fill="x", padx=10, pady=6)
        tk.Label(row1, text="Ausgabe-Gerät:", font=FONT_BOLD,
                 bg=C["panel"], fg=C["sub"]).pack(side="left", padx=(0, 8))
        self._var_audio = tk.BooleanVar(value=self.cfg.get("audio_enabled", True))
        tk.Checkbutton(row1, text="Live hören", variable=self._var_audio,
                       bg=C["panel"], fg=C["tx"], selectcolor=C["bg"],
                       activebackground=C["panel"], font=FONT_UI,
                       command=self._toggle_audio).pack(side="left", padx=(0, 16))
        self._cmb_dev = ttk.Combobox(row1, width=42, state="readonly",
                                      font=FONT_UI)
        self._cmb_dev.pack(side="left")
        _btn(row1, "🔄", self._refresh_devices,
             bg=C["panel"], fg=C["tx"],
             font=("Segoe UI", 11), padx=6).pack(side="left", padx=6)
        if not AUDIO_OK:
            tk.Label(row1, text="⚠️ pyaudio fehlt – pip install pyaudio",
                     font=FONT_UI, bg=C["panel"], fg=C["red"]).pack(side="left")
        self._refresh_devices()

        row2 = tk.Frame(pnl, bg=C["panel"])
        row2.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(row2, text="Lautstärke:", font=FONT_BOLD,
                 bg=C["panel"], fg=C["sub"]).pack(side="left", padx=(0, 8))
        self._var_volume = tk.DoubleVar(value=self.cfg.get("volume", 2.0))
        self._lbl_vol = tk.Label(row2, text="200%", font=FONT_MONO,
                                  bg=C["panel"], fg=C["acc"], width=5)
        self._lbl_vol.pack(side="right", padx=(0, 10))
        def _on_vol(v):
            pct = int(float(v) * 100)
            self._lbl_vol.config(text=f"{pct}%")
            self._audio.set_volume(float(v))
        tk.Scale(row2, from_=0.5, to=10.0, resolution=0.1,
                 orient="horizontal", variable=self._var_volume,
                 command=_on_vol, length=260,
                 bg=C["panel"], fg=C["tx"], troughcolor=C["border"],
                 highlightthickness=0, showvalue=False).pack(side="left")
        # Initialen Wert setzen
        _on_vol(self._var_volume.get())


        # ── USB-Aufnahme (direct_audio) ──────────────────────
        usb_pnl = _panel(tab, title="Lokale Aufnahme (USB / Line-In)", accent=C["org"])
        usb_pnl._outer.pack(fill="x", padx=8, pady=(0, 8))
        u_row = tk.Frame(usb_pnl, bg=C["panel"])
        u_row.pack(fill="x", padx=10, pady=6)
        
        tk.Label(u_row, text="Eingabe-Gerät:", font=FONT_BOLD,
                 bg=C["panel"], fg=C["sub"]).pack(side="left", padx=(0, 8))
                 
        self._cmb_usb_in = ttk.Combobox(u_row, width=42, state="readonly", font=FONT_UI)
        self._cmb_usb_in.pack(side="left")
        
        _btn(u_row, "🔄", self._refresh_usb_devices,
             bg=C["panel"], fg=C["tx"], font=("Segoe UI", 11), padx=6).pack(side="left", padx=6)
             
        self._btn_usb_rec = _btn(u_row, "⏺ Aufnahme starten", self._toggle_usb_recording,
                                 bg=C["panel"], fg=C["red"], font=FONT_BOLD, padx=10)
        self._btn_usb_rec.pack(side="left", padx=10)
        
        self._lbl_usb_stat = tk.Label(u_row, text="Bereit", font=FONT_UI, bg=C["panel"], fg=C["sub"])
        self._lbl_usb_stat.pack(side="left", padx=10)

        # ── Row 2 ──
        u_row2 = tk.Frame(usb_pnl, bg=C["panel"])
        u_row2.pack(fill="x", padx=10, pady=(0, 6))

        tk.Label(u_row2, text="Kanal-Name:", font=FONT_BOLD,
                 bg=C["panel"], fg=C["sub"]).pack(side="left", padx=(0, 8))
                 
        self._var_usb_name = tk.StringVar(value="USB-Live")
        self._ent_usb_name = tk.Entry(u_row2, textvariable=self._var_usb_name, font=FONT_UI, width=15,
                                      bg="#050508", fg=C["tx"], insertbackground=C["tx"], relief="flat")
        self._ent_usb_name.pack(side="left")
        
        tk.Label(u_row2, text="VOX Threshold:", font=FONT_BOLD,
                 bg=C["panel"], fg=C["sub"]).pack(side="left", padx=(16, 8))
                 
        self._var_vox = tk.IntVar(value=150)
        self._ent_vox = tk.Entry(u_row2, textvariable=self._var_vox, font=FONT_UI, width=6,
                                 bg="#050508", fg=C["tx"], insertbackground=C["tx"], relief="flat")
        self._ent_vox.pack(side="left")
        
        self._usb_capture = None
        self._refresh_usb_devices()
        
        # Aufnahmen-Liste
        rec_pnl = _panel(tab, title="Aufnahmen")
        rec_pnl._outer.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._lbx_rec = tk.Listbox(rec_pnl, bg="#0d0d0d", fg=C["acc2"],
                                    font=FONT_MONO, relief="flat",
                                    selectbackground=C["sel"], borderwidth=0)
        sb_r = ttk.Scrollbar(rec_pnl, orient="vertical",
                              command=self._lbx_rec.yview)
        self._lbx_rec.configure(yscrollcommand=sb_r.set)
        sb_r.pack(side="right", fill="y")
        self._lbx_rec.pack(fill="both", expand=True, padx=2, pady=2)
        bf = tk.Frame(tab, bg=C["bg"])
        bf.pack(fill="x", padx=8, pady=(0, 8))
        _btn(bf, "🔄 Aktualisieren", self._refresh_recordings,
             bg=C["sub"], fg=C["tx"], padx=12, pady=6).pack(side="left")
        _btn(bf, "▶ Abspielen", self._play_recording,
             bg=C["grn"], fg="#000", padx=12, pady=6).pack(side="left", padx=6)
        _btn(bf, "💾 Als WAV", self._export_wav,
             bg=C["acc"], fg="#000", padx=12, pady=6).pack(side="left")
        _btn(bf, "📡 Ordner öffnen",
             lambda: os.startfile(RECORD_DIR),
             bg=C["sub"], fg=C["tx"], padx=12, pady=6).pack(side="left", padx=6)
        self._refresh_recordings()
        self._playback_thread = None

    # ══════════════════════════════════════════════════════
    #  TAB 3 – GPS / SMS
    # ══════════════════════════════════════════════════════
    def _build_tab_gps_sms(self):
        tab = tk.Frame(self._nb, bg=C["bg"])
        self._nb.add(tab, text="  📍 GPS / SMS  ")
        self._tab_gps_sms = tab   # Referenz für hide/show


        # Links: GPS
        left = tk.Frame(tab, bg=C["bg"])
        left.pack(side="left", fill="both", expand=True, padx=(8, 3), pady=8)
        gps_pnl = _panel(left, title="GPS-Fixes", accent=C["acc"])
        gps_pnl._outer.pack(fill="both", expand=True)
        self._lbx_gps = tk.Listbox(gps_pnl, bg="#0d0d0d", fg=C["acc"],
                                    font=FONT_MONO, relief="flat",
                                    selectbackground=C["sel"], borderwidth=0)
        sb_g = ttk.Scrollbar(gps_pnl, orient="vertical",
                              command=self._lbx_gps.yview)
        self._lbx_gps.configure(yscrollcommand=sb_g.set)
        sb_g.pack(side="right", fill="y")
        self._lbx_gps.pack(fill="both", expand=True, padx=2, pady=2)

        # Rechts: SMS
        right = tk.Frame(tab, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(3, 8), pady=8)
        sms_pnl = _panel(right, title="SMS / TMP-Nachrichten", accent=C["acc2"])
        sms_pnl._outer.pack(fill="both", expand=True)
        self._lbx_sms = tk.Text(sms_pnl, bg="#0d0d0d", fg=C["tx"],
                                 font=FONT_MONO, relief="flat",
                                 state="disabled", wrap="word")
        sb_s = ttk.Scrollbar(sms_pnl, orient="vertical",
                              command=self._lbx_sms.yview)
        self._lbx_sms.configure(yscrollcommand=sb_s.set)
        sb_s.pack(side="right", fill="y")
        self._lbx_sms.pack(fill="both", expand=True, padx=2, pady=2)
        self._lbx_sms.tag_config("hdr", foreground=C["acc2"])
        self._lbx_sms.tag_config("grp", foreground=C["org"])

    # ══════════════════════════════════════════════════════
    #  TAB 4 – USV
    # ══════════════════════════════════════════════════════
    def _build_tab_usv(self):
        tab = tk.Frame(self._nb, bg=C["bg"])
        self._nb.add(tab, text="  🔋 USV  ")

        top = tk.Frame(tab, bg=C["bg"])
        top.pack(fill="x", padx=8, pady=8)

        # ── Batterie (grosses HUD-Panel links) ──────────────
        batt_pnl = _panel(top, title="⚡ Batterie", accent=C["grn"])
        batt_pnl._outer.pack(side="left", padx=(0, 6), ipadx=10, ipady=6)
        self._lbl_batt_pct = tk.Label(batt_pnl, text="–%",
                                       font=("Consolas", 32, "bold"),
                                       bg=C["panel"], fg=C["grn"])
        self._lbl_batt_pct.pack(padx=16, pady=(8, 0))
        # Canvas-Ladebalken
        self._cv_batt = tk.Canvas(batt_pnl, width=160, height=12,
                                   bg=C["dim"], highlightthickness=1,
                                   highlightbackground=C["grn"])
        self._cv_batt.pack(padx=16, pady=(4, 2))
        self._bar_batt = self._cv_batt.create_rectangle(
            0, 0, 0, 12, fill=C["grn"], outline="")
        self._lbl_batt_min = tk.Label(batt_pnl, text="– min verbleibend",
                                       font=FONT_MONO, bg=C["panel"], fg=C["tx2"])
        self._lbl_batt_min.pack(padx=16, pady=(0, 2))
        self._lbl_batt_st = tk.Label(batt_pnl, text="–",
                                      font=FONT_HUD_SM, bg=C["panel"], fg=C["grn"])
        self._lbl_batt_st.pack(pady=(0, 8))

        # ── Netz-HUD-Boxen rechts davon ──────────────────────
        right_col = tk.Frame(top, bg=C["bg"])
        right_col.pack(side="left", fill="both", expand=True)

        # Obere Reihe: Netz-Status + Last + Temp
        row1 = tk.Frame(right_col, bg=C["bg"])
        row1.pack(fill="x", pady=(0, 4))

        netz_pnl = _panel(row1, title="Netzversorgung", accent=C["acc"])
        netz_pnl._outer.pack(side="left", padx=(0, 6), ipadx=8, ipady=4)
        self._lbl_netz_st = tk.Label(netz_pnl, text="–",
                                      font=FONT_HUD, bg=C["panel"], fg=C["acc"])
        self._lbl_netz_st.pack(padx=12, pady=(6, 2))
        self._lbl_ein_v = tk.Label(netz_pnl, text="Eingang:  – V  –Hz",
                                    font=FONT_MONO, bg=C["panel"], fg=C["tx2"])
        self._lbl_ein_v.pack(padx=12)
        self._lbl_aus_v = tk.Label(netz_pnl, text="Ausgang:  – V  –Hz",
                                    font=FONT_MONO, bg=C["panel"], fg=C["tx2"])
        self._lbl_aus_v.pack(padx=12, pady=(0, 6))
        self._lbl_hz = tk.Label(netz_pnl, text="", font=FONT_MONO,
                                 bg=C["panel"], fg=C["sub"])  # Compat-Label (unused)

        last_pnl = _panel(row1, title="Last / Temperatur", accent=C["org"])
        last_pnl._outer.pack(side="left", padx=(0, 6), ipadx=8, ipady=4)
        self._lbl_last = tk.Label(last_pnl, text="– %",
                                   font=FONT_HUD, bg=C["panel"], fg=C["org"])
        self._lbl_last.pack(padx=12, pady=(6, 2))
        self._lbl_temp = tk.Label(last_pnl, text="Temp:   – °C",
                                   font=FONT_MONO, bg=C["panel"], fg=C["tx2"])
        self._lbl_temp.pack(padx=12)
        self._lbl_alarme = tk.Label(last_pnl, text="Alarme: –",
                                     font=FONT_MONO, bg=C["panel"], fg=C["tx2"])
        self._lbl_alarme.pack(padx=12, pady=(0, 6))

        reach_pnl = _panel(row1, title="USV Status", accent=C["acc2"])
        reach_pnl._outer.pack(side="left", fill="both", expand=True, ipadx=8, ipady=4)
        self._dot_usv = tk.Label(reach_pnl, text="●",
                                  font=("Consolas", 28, "bold"),
                                  bg=C["panel"], fg=C["sub"])
        self._dot_usv.pack(pady=(6, 0))
        self._lbl_usv_st = tk.Label(reach_pnl, text="nicht verbunden",
                                     font=FONT_HUD_SM, bg=C["panel"], fg=C["sub"])
        self._lbl_usv_st.pack()
        self._lbl_usv_ts = tk.Label(reach_pnl, text="",
                                     font=FONT_MONO, bg=C["panel"], fg=C["sub"])
        self._lbl_usv_ts.pack(pady=(0, 6))

        # Manuelle Abfrage
        bf = tk.Frame(tab, bg=C["bg"])
        bf.pack(fill="x", padx=8, pady=(0, 8))
        _btn(bf, "🔍 Jetzt abfragen", self._usv_poll_now,
             bg=C["acc"], fg="#000", font=("Consolas", 10, "bold"),
             padx=14, pady=6).pack(side="left")

    # ══════════════════════════════════════════════════════
    #  TAB 5 – EINSTELLUNGEN
    # ══════════════════════════════════════════════════════
    def _build_tab_settings(self):
        tab = tk.Frame(self._nb, bg=C["bg"])
        self._nb.add(tab, text="  ⚙ Einstellungen  ")
        tab = _make_scrollable_tab(tab)   # Einstellungen scrollbar machen

        def row(parent, label, attr, width=18, row_n=0, col=0):
            tk.Label(parent, text=label, font=FONT_UI,
                     bg=C["panel"], fg=C["sub"]).grid(
                row=row_n, column=col*2, sticky="e", padx=(12, 4), pady=4)
            var = tk.StringVar()
            setattr(self, attr, var)
            tk.Entry(parent, textvariable=var, width=width,
                     bg=C["bg"], fg=C["tx"], insertbackground=C["tx"],
                     relief="flat", font=FONT_MONO).grid(
                row=row_n, column=col*2+1, sticky="w", padx=(0, 10), pady=4)

        net_pnl = _panel(tab, title="Netzwerk  –  Hytera Repeater", accent=C["acc"])
        net_pnl._outer.pack(fill="x", padx=8, pady=(8, 4))
        g = tk.Frame(net_pnl, bg=C["panel"]); g.pack(padx=10, pady=6)
        row(g, "Repeater IP:", "_s_rip", 18, 0, 0)
        row(g, "Lausch-IP:",   "_s_lip", 18, 0, 1)
        row(g, "TS1 Port:",    "_s_ts1", 10, 1, 0)
        row(g, "TS2 Port:",    "_s_ts2", 10, 1, 1)
        row(g, "CC1 Port:",    "_s_cc1", 10, 2, 0)
        row(g, "CC2 Port:",    "_s_cc2", 10, 2, 1)
        row(g, "GPS Port (TS1):",  "_s_gps",  10, 3, 0)
        row(g, "GPS Port (TS2):",  "_s_gps2", 10, 3, 1)
        row(g, "SMS Port (TS1):",  "_s_sms",  10, 4, 0)
        row(g, "SMS Port (TS2):",  "_s_sms2", 10, 4, 1)
        row(g, "RRS Port (TS1):",  "_s_rrs",  10, 5, 0)
        row(g, "RRS Port (TS2):",  "_s_rrs2", 10, 5, 1)
        row(g, "Tele Port (TS1):", "_s_tel",  10, 6, 0)
        row(g, "Tele Port (TS2):", "_s_tel2", 10, 6, 1)
        row(g, "Timeout (s):",     "_s_tmo",   8, 7, 0)

        usv_pnl = _panel(tab, title="BlueWalker USV  –  Modbus", accent=C["acc2"])
        usv_pnl._outer.pack(fill="x", padx=8, pady=4)
        ug = tk.Frame(usv_pnl, bg=C["panel"]); ug.pack(padx=10, pady=6)
        row(ug, "USV IP:",        "_s_usv_ip",   18, 0, 0)
        row(ug, "Community:",     "_s_usv_comm",  14, 0, 1)
        row(ug, "Intervall (s):", "_s_usv_int",    8, 1, 0)
        self._var_usv_en = tk.BooleanVar()
        tk.Checkbutton(ug, text="USV-Monitor aktivieren",
                       variable=self._var_usv_en,
                       bg=C["panel"], fg=C["tx"], selectcolor=C["bg"],
                       activebackground=C["panel"], font=FONT_UI).grid(
            row=1, column=3, padx=10)

        sdr_pnl = _panel(tab, title="RTL-SDR v4  –  Direktfunk", accent=C["ts2"])
        sdr_pnl._outer.pack(fill="x", padx=8, pady=4)
        sg = tk.Frame(sdr_pnl, bg=C["panel"]); sg.pack(padx=10, pady=6)
        row(sg, "Frequenz (MHz):", "_s_sdr_freq", 14, 0, 0)
        row(sg, "Gain:",           "_s_sdr_gain", 14, 0, 1)
        row(sg, "PPM:",            "_s_sdr_ppm",   8, 1, 0)
        self._var_sdr_en = tk.BooleanVar()
        tk.Checkbutton(sg, text="RTL-SDR Monitor aktivieren",
                       variable=self._var_sdr_en,
                       bg=C["panel"], fg=C["tx"], selectcolor=C["bg"],
                       activebackground=C["panel"], font=FONT_UI).grid(
            row=1, column=3, padx=10)

        router_pnl = _panel(tab, title="TP-Link ER605  –  SNMP Monitor", accent=C["org"])
        router_pnl._outer.pack(fill="x", padx=8, pady=4)
        rg = tk.Frame(router_pnl, bg=C["panel"]); rg.pack(padx=10, pady=6)
        row(rg, "Router IP:",      "_s_router_ip", 14, 0, 0)
        row(rg, "Community:",      "_s_router_comm", 14, 0, 1)
        self._var_router_en = tk.BooleanVar()
        tk.Checkbutton(rg, text="Router-Monitor aktivieren",
                       variable=self._var_router_en,
                       bg=C["panel"], fg=C["tx"], selectcolor=C["bg"],
                       activebackground=C["panel"], font=FONT_UI).grid(
            row=0, column=3, padx=10)



        zte_pnl = _panel(tab, title="📡 ZTE MF281  –  Telekom Speedbox 2 Monitor", accent=C["acc2"])
        zte_pnl._outer.pack(fill="x", padx=8, pady=4)
        zpf = tk.Frame(zte_pnl, bg=C["panel"]); zpf.pack(padx=10, pady=6)
        row(zpf, "Router IP:",     "_s_zte_ip",  14, 0, 0)
        row(zpf, "Passwort:",      "_s_zte_pw",  14, 0, 1)
        row(zpf, "Intervall (s):", "_s_zte_int",  6, 1, 0)
        self._var_zte_en = tk.BooleanVar()
        tk.Checkbutton(zpf, text="ZTE Monitor aktivieren",
                       variable=self._var_zte_en,
                       bg=C["panel"], fg=C["tx"], selectcolor=C["bg"],
                       activebackground=C["panel"], font=FONT_UI).grid(
            row=0, column=5, padx=10)

        # ── Tab-Sichtbarkeit ───────────────────────────────
        vis_pnl = _panel(tab, title="Tabs ein-/ausblenden  (Dashboard & USV immer sichtbar)")
        vis_pnl._outer.pack(fill="x", padx=8, pady=4)
        vf = tk.Frame(vis_pnl, bg=C["panel"]); vf.pack(fill="x", padx=10, pady=8)

        self._var_tab_audio   = tk.BooleanVar()
        self._var_tab_gps_sms = tk.BooleanVar()

        for col, text, var in [
            (0, "🔊 Audio-Tab anzeigen",    self._var_tab_audio),
            (1, "📍 GPS/SMS-Tab anzeigen",  self._var_tab_gps_sms),
        ]:
            tk.Checkbutton(vf, text=text, variable=var,
                           bg=C["panel"], fg=C["tx"], selectcolor=C["bg"],
                           activebackground=C["panel"], font=FONT_UI,
                           command=self._apply_tab_visibility).pack(
                side="left", padx=20)

        bf = tk.Frame(tab, bg=C["bg"])
        bf.pack(fill="x", padx=8, pady=8)
        _btn(bf, "💾 Speichern & Übernehmen", self._save_cfg,
             bg=C["acc"], fg="#000", font=("Consolas", 10, "bold"),
             padx=16, pady=8).pack(side="left")
        _btn(bf, "↩ Zurücksetzen", self._apply_cfg,
             bg=C["sub"], fg=C["tx"], padx=12, pady=8).pack(side="left", padx=8)

        # ── DMR-ID Mitarbeiter-Tabelle ─────────────────────
        id_pnl = _panel(tab, title="👥  Funkgerät → Mitarbeiter Zuordnung  (DMR-IDs)")
        id_pnl._outer.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Treeview-Tabelle
        cols = ("id", "name", "geraet")
        self._tv_ids = ttk.Treeview(id_pnl, columns=cols, show="headings",
                                     height=8, selectmode="browse")
        self._tv_ids.heading("id",     text="Radio-ID")
        self._tv_ids.heading("name",   text="Mitarbeiter / Name")
        self._tv_ids.heading("geraet", text="Gerät")
        self._tv_ids.column("id",     width=90,  anchor="center")
        self._tv_ids.column("name",   width=200, anchor="w")
        self._tv_ids.column("geraet", width=140, anchor="w")

        # Style für Treeview
        style = ttk.Style()
        style.configure("Treeview",
                         background=C["bg"], foreground=C["tx"],
                         fieldbackground=C["bg"], rowheight=22,
                         font=FONT_MONO)
        style.configure("Treeview.Heading",
                         background=C["hdr"], foreground=C["acc"],
                         font=FONT_BOLD)
        style.map("Treeview", background=[("selected", C["sel"])])

        sb_tv = ttk.Scrollbar(id_pnl, orient="vertical",
                               command=self._tv_ids.yview)
        self._tv_ids.configure(yscrollcommand=sb_tv.set)
        sb_tv.pack(side="right", fill="y")
        self._tv_ids.pack(fill="both", expand=True, padx=2, pady=2)
        self._refresh_id_table()

        # Eingabefelder
        ef = tk.Frame(id_pnl, bg=C["panel"]); ef.pack(fill="x", padx=4, pady=4)
        for col, label, width, attr in [
            (0, "Radio-ID:", 10, "_id_f_id"),
            (2, "Name:",     22, "_id_f_name"),
            (4, "Gerät:",    16, "_id_f_geraet"),
        ]:
            tk.Label(ef, text=label, font=FONT_UI,
                     bg=C["panel"], fg=C["sub"]).grid(row=0, column=col, padx=(8,2), pady=4)
            var = tk.StringVar()
            setattr(self, attr, var)
            tk.Entry(ef, textvariable=var, width=width,
                     bg=C["bg"], fg=C["tx"], insertbackground=C["tx"],
                     relief="flat", font=FONT_MONO).grid(
                row=0, column=col+1, padx=(0,8), pady=4)

        # Buttons
        ibf = tk.Frame(id_pnl, bg=C["panel"]); ibf.pack(fill="x", padx=4, pady=(0,6))
        _btn(ibf, "➕ Hinzufügen",  self._id_add,    bg=C["grn"], fg=C["bg"], padx=10, pady=5).pack(side="left", padx=(4,2))
        _btn(ibf, "✏️ Bearbeiten",  self._id_edit,   bg=C["acc2"], fg=C["bg"], padx=10, pady=5).pack(side="left", padx=2)
        _btn(ibf, "🗑️ Löschen",    self._id_delete, bg=C["red"], fg="white", padx=10, pady=5).pack(side="left", padx=2)
        _btn(ibf, "💾 IDs speichern", self._id_save, bg=C["acc"], fg=C["bg"], padx=10, pady=5).pack(side="right", padx=4)
        self._tv_ids.bind("<<TreeviewSelect>>", self._id_on_select)

    # ══════════════════════════════════════════════════════
    #  Konfig
    # ══════════════════════════════════════════════════════
    def _apply_cfg(self):
        c = self.cfg
        self._s_rip.set(c.get("repeater_ip", "192.168.1.100"))
        self._s_lip.set(c.get("listen_ip",   "0.0.0.0"))
        self._s_ts1.set(str(c.get("ts1_port",  30012)))
        self._s_ts2.set(str(c.get("ts2_port",  30014)))
        self._s_cc1.set(str(c.get("cc1_port",  30009)))
        self._s_cc2.set(str(c.get("cc2_port",  30010)))
        self._s_gps.set(str(c.get("gps_port",   30003)))
        self._s_gps2.set(str(c.get("gps2_port",  30004)))
        self._s_sms.set(str(c.get("sms_port",   30007)))
        self._s_sms2.set(str(c.get("sms2_port",  30008)))
        self._s_rrs.set(str(c.get("rrs1_port",  30001)))
        self._s_rrs2.set(str(c.get("rrs2_port",  30002)))
        self._s_tel.set(str(c.get("tele_port",  30005)))
        self._s_tel2.set(str(c.get("tele2_port", 30006)))
        self._s_tmo.set(str(c.get("timeout", 0.3)))
        self._s_usv_ip.set(c.get("usv_ip",       "192.168.1.200"))
        self._s_usv_comm.set(c.get("usv_community", "public"))
        self._s_usv_int.set(str(c.get("usv_interval", 30)))
        self._var_usv_en.set(c.get("usv_enabled", True))
        
        self._s_sdr_freq.set(c.get("sdr_freq", "446.00625"))
        self._s_sdr_gain.set(str(c.get("sdr_gain", "auto")))
        self._s_sdr_ppm.set(str(c.get("sdr_ppm", 0)))
        self._var_sdr_en.set(c.get("sdr_enabled", False))

        self._s_router_ip.set(c.get("router_ip", "192.168.0.1"))
        self._s_router_comm.set(c.get("router_community", "public"))
        self._var_router_en.set(c.get("router_enabled", False))

        # ZTE MF281
        self._s_zte_ip.set(c.get("zte_ip", "192.168.0.1"))
        self._s_zte_pw.set(c.get("zte_password", "admin"))
        self._s_zte_int.set(str(c.get("zte_interval", 15)))
        self._var_zte_en.set(c.get("zte_enabled", False))

        # Tab-Sichtbarkeit
        self._var_tab_audio.set(c.get("tab_audio", True))
        self._var_tab_gps_sms.set(c.get("tab_gps_sms", True))
        self._apply_tab_visibility()

    def _read_cfg(self):
        try:
            self.cfg.update({
                "repeater_ip":   self._s_rip.get().strip(),
                "listen_ip":     self._s_lip.get().strip() or "0.0.0.0",
                "ts1_port":      int(self._s_ts1.get()),
                "ts2_port":      int(self._s_ts2.get()),
                "cc1_port":      int(self._s_cc1.get()),
                "cc2_port":      int(self._s_cc2.get()),
                "gps_port":      int(self._s_gps.get()),
                "gps2_port":     int(self._s_gps2.get()),
                "sms_port":      int(self._s_sms.get()),
                "sms2_port":     int(self._s_sms2.get()),
                "rrs1_port":     int(self._s_rrs.get()),
                "rrs2_port":     int(self._s_rrs2.get()),
                "tele_port":     int(self._s_tel.get()),
                "tele2_port":    int(self._s_tel2.get()),
                "timeout":       float(self._s_tmo.get()),
                "audio_device":  self._get_dev_idx(),
                "audio_enabled": self._var_audio.get(),
                "usv_ip":        self._s_usv_ip.get().strip(),
                "usv_community": self._s_usv_comm.get().strip(),
                "usv_interval":  int(self._s_usv_int.get()),
                "usv_enabled":   self._var_usv_en.get(),
                "sdr_freq":      self._s_sdr_freq.get().strip(),
                "sdr_gain":      self._s_sdr_gain.get().strip(),
                "sdr_ppm":       int(self._s_sdr_ppm.get()),
                "sdr_enabled":   self._var_sdr_en.get(),
                "router_ip":     self._s_router_ip.get().strip(),
                "router_community": self._s_router_comm.get().strip(),
                "router_enabled": self._var_router_en.get(),
                "zte_ip":        self._s_zte_ip.get().strip(),
                "zte_password":  self._s_zte_pw.get().strip(),
                "zte_interval":  int(self._s_zte_int.get() or 15),
                "zte_enabled":   self._var_zte_en.get(),
                "tab_audio":     self._var_tab_audio.get(),
                "tab_gps_sms":   self._var_tab_gps_sms.get(),
            })
            return True
        except Exception as e:
            messagebox.showerror("Konfigurationsfehler", str(e))
            return False

    def _apply_tab_visibility(self):
        """Tabs sofort ein- oder ausblenden basierend auf Checkbox-Zustand."""
        tab_map = [
            (self._var_tab_audio,   self._tab_audio),
            (self._var_tab_gps_sms, self._tab_gps_sms),
        ]
        for var, tab in tab_map:
            try:
                if var.get():
                    # Tab wieder einblenden (falls er hidden war)
                    state = self._nb.tab(tab, option="state")
                    if state == "hidden":
                        self._nb.add(tab)
                else:
                    self._nb.hide(tab)
            except Exception:
                pass

    def _save_cfg(self):
        if self._read_cfg():
            save_config(self.cfg)
            self._log_msg("Konfiguration gespeichert.", "ok")

    # ── Audio-Geräte ──────────────────────────────────────

    # ── USB Audio Methoden ────────────────────────────────

    # ── USB Audio Methoden ────────────────────────────────
    def _refresh_usb_devices(self):
        try:
            from direct_audio import list_input_devices
            devs = list_input_devices()
            self._usb_dev_map = {name: idx for idx, name in devs}
            self._cmb_usb_in["values"] = list(self._usb_dev_map.keys())
            if self._cmb_usb_in["values"]:
                self._cmb_usb_in.current(0)
        except Exception as e:
            self._lbl_usb_stat.config(text="Fehler: pyaudio fehlt?", fg=C["red"])

    def _toggle_usb_recording(self):
        if getattr(self, "_usb_capture", None) is not None:
            self._usb_capture.stop()
            self._usb_capture = None
            self._btn_usb_rec.config(text="⏺ Aufnahme starten", fg=C["red"])
            self._lbl_usb_stat.config(text="Gestoppt", fg=C["sub"])
            return

        name = self._cmb_usb_in.get()
        if not name or name not in getattr(self, "_usb_dev_map", {}):
            return
            
        idx = self._usb_dev_map[name]
        try:
            from direct_audio import DirectAudioCapture
            import os, time
            
            # If mission is active, save directly to mission_dir
            is_active = getattr(self, "_mission", None) and self._mission.active
            out_dir = self._mission.mission_dir if is_active else os.path.join(RECORD_DIR, "USB_Direkt")
            
            c_name = getattr(self, "_var_usb_name", None)
            usb_name = c_name.get().strip() if c_name else "USB-Live"
            def _status_cb(st, fn):
                msg = f"{st}: {os.path.basename(fn)}" if fn else st
                color = C["grn"] if st=="recording" else C["sub"]
                self.after(0, lambda: self._lbl_usb_stat.config(text=msg, fg=color))
                
                if getattr(self, "_mission", None) and self._mission.active:
                    if st == "recording":
                        self._usb_start_time = time.time()
                        self._usb_fn = fn
                        from mission_logger import MissionEvent
                        self._mission._add(MissionEvent("ptt_start", self._mission._rel(),
                            radio_id=999999,
                            slot="USB",
                            name=usb_name,
                            rssi=-40,
                            _byteCount=0
                        ))
                    elif st in ("standby", "gestoppt"):
                        if hasattr(self, "_usb_start_time"):
                            dur = time.time() - self._usb_start_time
                            fn_base = os.path.basename(getattr(self, "_usb_fn", ""))
                            from mission_logger import MissionEvent
                            self._mission._add(MissionEvent("ptt_end", self._mission._rel(),
                                radio_id=999999,
                                slot="USB",
                                name=usb_name,
                                duration=round(dur, 1),
                                byte_count=0,
                                _audio_file="/rec/" + fn_base if fn_base else None
                            ))
                            delattr(self, "_usb_start_time")
                            
            self._usb_capture = DirectAudioCapture(
                device_index=idx,
                out_dir=out_dir,
                vox_threshold=int(getattr(self, "_var_vox").get()) if hasattr(self, "_var_vox") else 150, # VOX mode
                vox_hold_sec=1.5,
                status_cb=_status_cb
            )
            self._usb_capture.start()
            self._btn_usb_rec.config(text="⏹ Aufnahme stoppen", fg=C["tx"])
        except Exception as e:
            self._lbl_usb_stat.config(text=f"Fehler: {e}", fg=C["red"])

    def _refresh_devices(self):

        devs = self._audio.devices()
        self._dev_map = {name: idx for idx, name in devs}
        self._cmb_dev["values"] = [n for _, n in devs]
        saved = self.cfg.get("audio_device", -1)
        for idx, name in devs:
            if idx == saved:
                self._cmb_dev.set(name); return
        if devs:
            self._cmb_dev.current(0)

    def _get_dev_idx(self):
        return self._dev_map.get(self._cmb_dev.get(), -1)

    def _toggle_audio(self):
        if self._var_audio.get() and self._running:
            self._audio.start(self._get_dev_idx())
        elif not self._var_audio.get():
            self._audio.stop()

    def _refresh_recordings(self):
        self._lbx_rec.delete(0, "end")
        try:
            files = sorted(
                [f for f in os.listdir(RECORD_DIR) if f.endswith(".raw")],
                reverse=True)
            for f in files:
                sz = os.path.getsize(os.path.join(RECORD_DIR, f)) // 1024
                self._lbx_rec.insert("end", f"  {f}   [{sz} KB]")
            if not files:
                self._lbx_rec.insert("end", "  – keine Aufnahmen –")
        except Exception:
            pass

    def _get_selected_rec(self):
        """Gibt den vollen Pfad der ausgewählten Aufnahme zurück oder None."""
        sel = self._lbx_rec.curselection()
        if not sel:
            messagebox.showinfo("Hinweis", "Bitte erst eine Aufnahme in der Liste auswählen.")
            return None
        # Dateiname steht am Anfang des Eintrags (ohne führende Leerzeichen)
        entry = self._lbx_rec.get(sel[0]).strip()
        fname = entry.split("   ")[0].strip()
        return os.path.join(RECORD_DIR, fname)

    def _play_recording(self):
        """Spielt eine .raw G.711 µ-law Datei über pyaudio ab."""
        path = self._get_selected_rec()
        if not path or not os.path.exists(path):
            return
        if self._playback_thread and self._playback_thread.is_alive():
            self._log_msg("Wiedergabe läuft bereits …", "warn")
            return
        if not AUDIO_OK:
            messagebox.showerror("Fehler", "pyaudio ist nicht installiert.")
            return

        def _do_play():
            import audioop
            try:
                pa = pyaudio.PyAudio()
                dev_idx = self._get_dev_idx()
                stream = pa.open(format=pyaudio.paInt16,
                                 channels=1, rate=8000,
                                 output=True,
                                 output_device_index=dev_idx if dev_idx >= 0 else None)
                self._log_msg(f"▶ Wiedergabe: {os.path.basename(path)}", "ok")
                vol = self._var_volume.get()
                with open(path, "rb") as f:
                    while chunk := f.read(160):
                        pcm = audioop.ulaw2lin(chunk, 2)
                        if vol != 1.0:
                            pcm = _apply_gain(pcm, vol)
                        stream.write(pcm)
                stream.stop_stream()
                stream.close()
                pa.terminate()
                self._log_msg("▶ Wiedergabe beendet.", "acc")
            except Exception as e:
                self._log_msg(f"Wiedergabe-Fehler: {e}", "err")

        self._playback_thread = threading.Thread(target=_do_play, daemon=True)
        self._playback_thread.start()

    def _export_wav(self):
        """Konvertiert .raw G.711 µ-law → .wav und öffnet den Ordner."""
        path = self._get_selected_rec()
        if not path or not os.path.exists(path):
            return
        import wave, audioop
        wav_path = path.replace(".raw", ".wav")
        try:
            with open(path, "rb") as raw_f:
                raw_data = raw_f.read()
            pcm_data = audioop.ulaw2lin(raw_data, 2)
            vol = self._var_volume.get()
            if vol != 1.0:
                pcm_data = _apply_gain(pcm_data, vol)
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)   # 16-bit
                wf.setframerate(8000)
                wf.writeframes(pcm_data)
            self._log_msg(f"💾 WAV gespeichert: {os.path.basename(wav_path)}", "ok")
            self._refresh_recordings()
            os.startfile(RECORD_DIR)
        except Exception as e:
            self._log_msg(f"WAV-Export Fehler: {e}", "err")

    # ══════════════════════════════════════════════════════
    #  Start / Stop
    # ══════════════════════════════════════════════════════
    def _start(self):
        if self._running:
            return
        if not self._read_cfg():
            return
        cfg = self.cfg
        self._running = True
        self._btn_start.config(state="disabled")
        self._btn_stop.config(state="normal")
        lip = cfg["listen_ip"]
        tmo = max(0.1, float(cfg.get("timeout", 0.3)))  # min 100ms (DMR: 20ms/Paket)

        def hb(addr):
            t = time.time()
            self._last_hb = t; self._hb_addr = addr
            srv = getattr(self, '_sse_server', None)
            if srv:
                srv.set_heartbeat(t, addr)

        def on_call(ev):
            self.after(0, self._handle_call, ev)

        def on_gps(fix):
            self.after(0, self._handle_gps, fix)

        def on_sms(msg):
            self.after(0, self._handle_sms, msg)

        def on_radio(rs):
            self.after(0, self._handle_radio, rs)

        def slot_status(slot, text, color):
            self.after(0, self._update_slot, slot, text, color)

        if cfg.get("audio_enabled"):
            self._audio.start(self._get_dev_idx())

        def on_bytes(slot, pkts, total_bytes):
            self.after(0, self._update_slot_bytes, slot, pkts, total_bytes)

        def on_radio_from_audio(radio_id):
            """Radio-ID aus RTP-Extension: Radio ist aktiv/online."""
            if not self._online.get(radio_id):
                from hytera_protocol import RadioStatus
                self.after(0, self._handle_radio, RadioStatus(radio_id, True))

        for slot, port in (("TS1", cfg["ts1_port"]), ("TS2", cfg["ts2_port"])):
            def _make_rssi_cb_audio(s=slot):
                def _on_rssi(radio_id, rssi_dbm, sl):
                    from hytera_protocol import CallEvent as _CE
                    quality = _CE.rssi_quality(rssi_dbm)
                    color   = _CE.rssi_color(rssi_dbm)
                    self._log_msg(
                        f"[RSSI] {resolve_id(radio_id)} @ {sl}: {rssi_dbm} dBm ({quality})",
                        "acc")
                    self._mission.attach_rssi(radio_id, sl, rssi_dbm)
                    self.after(0, self._update_rssi_display, radio_id, rssi_dbm, color, s)
                return _on_rssi

            lst = AudioListener(
                port=port, slot_name=slot, timeout=tmo,
                log_cb=self._log_msg, status_cb=slot_status,
                hb_cb=hb, audio_feed=self._audio.feed, rec_dir=RECORD_DIR,
                bytes_cb=on_bytes, radio_id_cb=on_radio_from_audio,
                cc_cb=on_call, rssi_cb=_make_rssi_cb_audio())
            def _make_write_cb(s=slot):
                def _cb(sn, payload):
                    self._mission.write_audio(s, payload)
                return _cb
            def _make_rec_cb(s=slot):
                def _cb(sn):
                    self.after(0, self._mission_log_entry,
                               f"\U0001f3a4 {s} sendet", "ptt")
                return _cb
            def _make_ptt_start_cb(s=slot):
                def _cb(slot_name, radio_id):
                    self._mission.log_ptt_start_if_not_open(radio_id, s)
                return _cb
            def _make_ptt_end_cb(s=slot):
                def _cb(slot_name, radio_id):
                    self._mission.log_ptt_end(radio_id, s)
                return _cb
            lst.write_cb     = _make_write_cb()
            lst.rec_cb       = _make_rec_cb()
            lst.ptt_start_cb = _make_ptt_start_cb()
            lst.ptt_end_cb   = _make_ptt_end_cb()
            lst.start(); self._listeners.append(lst)

        for slot, port in (("TS1", cfg["cc1_port"]), ("TS2", cfg["cc2_port"])):
            def _make_rssi_cb(s=slot):
                def _on_rssi(radio_id, rssi_dbm, sl):
                    from hytera_protocol import CallEvent as _CE
                    quality = _CE.rssi_quality(rssi_dbm)
                    color   = _CE.rssi_color(rssi_dbm)
                    self._log_msg(
                        f"[RSSI] {resolve_id(radio_id)} @ {sl}: {rssi_dbm} dBm ({quality})",
                        "acc")
                    self._mission.attach_rssi(radio_id, sl, rssi_dbm)
                    self.after(0, self._update_rssi_display, radio_id, rssi_dbm, color, s)
                return _on_rssi
            lst = CallControlListener(port, slot, on_call, hb_cb=hb,
                                      rrs_cb=on_radio, rssi_cb=_make_rssi_cb())
            lst.start(); self._listeners.append(lst)

        for cls, port, port2, cb, label in (
            (GNSSListener, cfg["gps_port"],  cfg["gps2_port"],  on_gps,   "GPS"),
            (SMSListener,  cfg["sms_port"],  cfg["sms2_port"],  on_sms,   "SMS"),
            (RRSListener,  cfg["rrs1_port"], cfg["rrs2_port"],  on_radio, "RRS"),
        ):
            l = cls(port, label, cb, hb_cb=hb, extra_ports=[port2])
            l.start(); self._listeners.append(l)

        def on_tele(ev):
            self._log_msg(
                f"[Tele] Radio {ev.radio_id}  CH{ev.channel}={ev.value}", "acc")
        tele = TelemetryListener(cfg["tele_port"], "Tele", on_tele,
                                  hb_cb=hb, extra_ports=[cfg["tele2_port"]])
        tele.start(); self._listeners.append(tele)

        if cfg.get("usv_enabled"):
            self._usv_mon = USVMonitor(
                ip=cfg["usv_ip"],
                community=cfg.get("usv_community", "public"),
                interval=cfg.get("usv_interval", 30),
                callback=lambda d: [
                    self.after(0, self._update_usv, d),
                    self.after(0, self._update_usv_quick, d),
                    getattr(self, '_sse_server', None) and
                    getattr(self, '_sse_server').set_usv(d.as_dict() if hasattr(d, 'as_dict') else {}),
                ])

            self._usv_mon.start()

        if cfg.get("sdr_enabled"):
            self._sdr_mon = SDRMonitor(
                freq=cfg.get("sdr_freq"),
                gain=cfg.get("sdr_gain"),
                ppm=cfg.get("sdr_ppm"),
                log_cb=self._log_msg
            )
            self._sdr_mon.start()

        if cfg.get("router_enabled"):
            from router_monitor import RouterMonitor
            self._router_mon = RouterMonitor(
                ip=cfg.get("router_ip", "192.168.0.1"),
                community=cfg.get("router_community", "public"),
                callback=lambda d: [
                    self.after(0, self._update_router_quick, d),
                    getattr(self, '_sse_server', None) and
                    getattr(self, '_sse_server').set_router(d.as_dict() if hasattr(d, 'as_dict') else {})
                ]
            )
            self._router_mon.start()

        # ── ZTE MF281 Monitor ──────────────────────────────
        if cfg.get("zte_enabled") and _ZTE_OK:
            self._zte_mon = ZTEMonitor(
                host=cfg.get("zte_ip", "192.168.0.1"),
                password=cfg.get("zte_password", "admin"),
                interval=cfg.get("zte_interval", 15),
                callback=lambda d: self.after(0, self._update_zte, d),
            )
            self._zte_mon.start()
            self._log_msg(
                f"[ZTE] Monitor gestartet: {cfg.get('zte_ip')}  "
                f"Interval={cfg.get('zte_interval', 15)}s", "ok")
        elif cfg.get("zte_enabled") and not _ZTE_OK:
            self._log_msg("[ZTE] zte_monitor.py nicht gefunden – übersprungen.", "warn")

        # ── Repeater SNMP-Trap Empfaenger ─────────────────────
        if _TRAP_MON_OK:
            self._trap_mon = RepeaterTrapMonitor(
                callback=lambda s: self.after(0, self._update_repeater_alarms, s),
                log_cb=self._log_msg
            )
            self._trap_mon.start()
        else:
            self._log_msg("[Repeater] repeater_trap_monitor.py nicht gefunden.", "warn")

        self._log_msg("─" * 54)
        self._log_msg(
            f"Server gestartet  –  {len(self._listeners)} Listener aktiv.", "ok")
        self._log_msg(
            f"TS1:{cfg['ts1_port']}  TS2:{cfg['ts2_port']}  "
            f"GPS:{cfg['gps_port']}  SMS:{cfg['sms_port']}", "acc")
        self._log_msg(
            f"CC1:{cfg['cc1_port']}  CC2:{cfg['cc2_port']}  "
            f"RRS1:{cfg['rrs1_port']}  RRS2:{cfg['rrs2_port']}  "
            f"-- warte auf HSTRP-Pakete ...", "sub")

    def _stop(self):
        if not self._running:
            return
        for l in self._listeners:
            l.stop()
        self._listeners.clear()
        self._audio.stop()
        if self._usv_mon:
            self._usv_mon.stop()
            self._usv_mon = None
        if self._sdr_mon:
            self._sdr_mon.stop()
            self._sdr_mon = None
        if self._router_mon:
            self._router_mon.stop()
            self._router_mon = None
        if self._trap_mon:
            self._trap_mon.stop()
            self._trap_mon = None
        if self._zte_mon:
            self._zte_mon.stop()
            self._zte_mon = None

        if hasattr(self, '_sse'):
            self._sse.stop()
        self._running = False
        self._btn_start.config(state="normal")
        self._btn_stop.config(state="disabled")
        self._log_msg("Server gestoppt.", "warn")

    # ══════════════════════════════════════════════════════
    #  Callbacks
    # ══════════════════════════════════════════════════════
    def _log_msg(self, msg, tag=""):
        def _do():
            self._log.config(state="normal")
            ts = datetime.now().strftime("%H:%M:%S")
            self._log.insert("end", f"[{ts}]  {msg}\n", tag)
            self._log.see("end")
            self._log.config(state="disabled")
        self.after(0, _do)

    def _update_slot(self, slot, text, color):
        if slot in self._slot_panels:
            self._slot_panels[slot]["status"].config(text=text, fg=color)

    def _update_slot_bytes(self, slot, pkts, total_bytes):
        """Aktualisiert den Byte-/Paket-Zähler im Slot-Panel."""
        if slot in self._slot_panels:
            kb = total_bytes / 1024
            self._slot_panels[slot]["bytes"].config(
                text=f"{pkts} Pakete  |  {kb:.1f} KB",
                fg=C["acc2"])

    def _handle_call(self, ev):
        sender = resolve_id(ev.sender_id)
        target = resolve_id(ev.target_id)
        if ev.status == "start":
            rssi_str = f"  |  {ev.rssi} dBm" if ev.rssi is not None else ""
            self._lbl_call.config(
                text=f"\U0001f534  {ev.call_type}  {sender}",
                fg=C["red"])
            self._lbl_call_detail.config(
                text=f"\u2192 {target}   |   {ev.slot}  {ev.ts.strftime('%H:%M:%S')}{rssi_str}",
                fg=C["org"])
            self._log_msg(
                f"[{ev.slot}] {ev.call_type}: {sender} \u2192 {target}{rssi_str}", "ok")
            # Einsatz-Logger – nur starten wenn noch kein PTT offen fuer dieses Radio/Slot
            self._mission.log_ptt_start_if_not_open(ev.sender_id, ev.slot)
            if ev.rssi is not None:
                self._mission.attach_rssi(ev.sender_id, ev.slot, ev.rssi)
            self._mission.log_call(ev.sender_id, ev.target_id, ev.slot, ev.call_type, "start")
            self._mission_log_entry(
                f"\U0001f4fb {ev.slot} {ev.call_type}: {sender} \u2192 {target}{rssi_str}", "ptt")
            # Sicherheitsnetz: Falls ptt_end ausbleibt, Label nach 35s selbst zuruecksetzen.
            # (Normaler Weg: Watchdog in CC-Listener sendet ptt_end nach 5s Stille.)
            call_key = (ev.sender_id, ev.slot)
            if not hasattr(self, '_call_clear_timers'):
                self._call_clear_timers = {}
            old = self._call_clear_timers.pop(call_key, None)
            if old:
                try: old.cancel()
                except Exception: pass
            import threading as _thr
            def _auto_clear(key=call_key):
                self._call_clear_timers.pop(key, None)
                self.after(0, self._lbl_call.config,
                           {"text": "\u2013 kein Anruf \u2013", "fg": C["sub"]})
                self.after(0, self._lbl_call_detail.config, {"text": ""})
            tmr = _thr.Timer(35.0, _auto_clear)
            tmr.daemon = True
            self._call_clear_timers[call_key] = tmr
            tmr.start()
        else:
            # ptt_end: Timer sofort abbrechen
            call_key = (ev.sender_id, ev.slot)
            old = getattr(self, '_call_clear_timers', {}).pop(call_key, None)
            if old:
                try: old.cancel()
                except Exception: pass
            self._lbl_call.config(text="\u2013 kein Anruf \u2013", fg=C["sub"])
            self._lbl_call_detail.config(text="")
            self._mission.log_ptt_end(ev.sender_id, ev.slot)
            self._mission.log_call(ev.sender_id, ev.target_id, ev.slot, ev.call_type, "end")

    def _update_rssi_display(self, radio_id, rssi_dbm, color, slot=None):
        """Aktualisiert RSSI-Anzeige – slot-spezifisch, Wert bleibt nach PTT sichtbar."""
        from hytera_protocol import CallEvent as _CE
        quality = _CE.rssi_quality(rssi_dbm)
        name    = resolve_id(radio_id)
        text    = f"{name}: {rssi_dbm} dBm ({quality})"
        # Nur den zugehoerigen Slot aktualisieren (oder alle falls slot=None)
        for sl, widgets in self._slot_panels.items():
            if "rssi" not in widgets:
                continue
            if slot is None or sl == slot:
                widgets["rssi"].config(text=f"Signal: {text}", fg=color)

    def _handle_gps(self, fix):
        self._gps_fixes[fix.radio_id] = fix
        self._lbx_gps.delete(0, "end")
        for rid, f in sorted(self._gps_fixes.items()):
            ts   = f.ts.strftime("%H:%M:%S")
            name = resolve_id(rid)
            self._lbx_gps.insert(
                "end", f"  {name:<28}  {f.lat:+.5f}  {f.lon:+.5f}   {ts}")
        # Einsatz-Logger
        self._mission.log_gps(fix.radio_id, fix.lat, fix.lon,
                              getattr(fix, 'speed', 0), getattr(fix, 'heading', 0))

    def _handle_sms(self, msg):
        self._lbx_sms.config(state="normal")
        ts     = msg.ts.strftime("%H:%M:%S")
        sender = resolve_id(msg.sender_id)
        target = resolve_id(msg.target_id)
        tag    = "grp" if msg.is_group else "hdr"
        self._lbx_sms.insert("end",
            f"[{ts}] {sender} → {target}\n", tag)
        self._lbx_sms.insert("end", f"  {msg.text}\n")
        self._lbx_sms.see("end")
        self._lbx_sms.config(state="disabled")

    def _handle_radio(self, rs):
        self._online[rs.radio_id] = rs.online
        self._lbx_radios.delete(0, "end")
        for i, (rid, online) in enumerate(sorted(self._online.items())):
            sym   = "\u25cf" if online else "\u25cb"
            name  = resolve_id(rid)
            color = C["grn"] if online else C["sub"]
            self._lbx_radios.insert("end", f"  {sym}  {name}")
            self._lbx_radios.itemconfig(i, fg=color)
        status = 'online' if rs.online else 'offline'
        self._log_msg(f"[RRS] {resolve_id(rs.radio_id)} \u2013 {status}")
        # Einsatz-Logger
        self._mission.log_radio_status(rs.radio_id, rs.online)
        sym = "\U0001f7e2" if rs.online else "\U0001f534"
        self._mission_log_entry(
            f"{sym} {resolve_id(rs.radio_id)} {status}", "rrs")

    def _update_usv(self, d):
        self._usv_data = d
        if d.erreichbar:
            pct = d.batterie_pct or 0
            col = C["red"] if pct < 20 else C["org"] if pct < 50 else C["grn"]
            self._lbl_batt_pct.config(text=f"{pct}%", fg=col)
            self._lbl_batt_min.config(
                text=f"{d.batterie_min or '–'} min verbleibend")
            self._lbl_batt_st.config(
                text=d.batterie_status or "–",
                fg=C["grn"] if d.batterie_status == "Normal" else C["red"])
            self._cv_batt.coords(self._bar_batt, 0, 0, int(160 * pct / 100), 18)
            self._cv_batt.itemconfig(self._bar_batt, fill=col)
            self._lbl_netz_st.config(
                text=d.netz_status or "–",
                fg=C["grn"] if d.netz_status == "Normal" else C["red"])
            self._lbl_ein_v.config(text=f"Eingang:  {d.eingang_volt or '–'} V")
            self._lbl_aus_v.config(text=f"Ausgang:  {d.ausgang_volt or '–'} V")
            self._lbl_hz.config(text=f"Freq:  {d.ausgang_hz or '–'} Hz")
            self._lbl_last.config(
                text=f"Last:  {d.last_pct or '–'} %",
                fg=C["red"] if (d.last_pct or 0) > 80 else C["tx"])
            self._lbl_temp.config(text=f"Temp:  {d.temperatur or '–'} °C")
            self._lbl_alarme.config(
                text=f"Alarme:  {d.alarme or 0}",
                fg=C["red"] if (d.alarme or 0) > 0 else C["grn"])
            self._dot_usv.config(fg=C["grn"])
            self._lbl_usv_st.config(text="Verbunden", fg=C["grn"])
            ts = datetime.fromtimestamp(d.letzte_abfrage).strftime("%H:%M:%S")
            self._lbl_usv_ts.config(text=f"Stand: {ts}")
        else:
            self._dot_usv.config(fg=C["red"])
            self._lbl_usv_st.config(text="Nicht erreichbar", fg=C["red"])
            self._lbl_usv_ts.config(text=d.fehler or "")

    def _update_usv_quick(self, d):
        if not hasattr(self, '_usv_quick_labels') or not self._usv_quick_labels:
            return
        if d.erreichbar:
            pct = d.batterie_pct or 0
            self._usv_quick_labels["batt"].config(text=f"{pct}%")
            self._usv_quick_labels["netz"].config(text=d.netz_status or "–")
            self._usv_quick_labels["volt"].config(text=f"{d.ausgang_volt or '–'} V")
            self._usv_quick_labels["laufz"].config(text=f"{d.batterie_min or '–'} min")
            self._usv_quick_labels["last"].config(text=f"{d.last_pct or '–'} %")
        else:
            for k in self._usv_quick_labels:
                self._usv_quick_labels[k].config(text="–")

    def _update_router_quick(self, d):
        if not hasattr(self, '_router_quick_labels') or not self._router_quick_labels:
            return
        if d.erreichbar:
            self._router_quick_labels["sys"].config(text=d.sys_name[:12])
            
            # Format uptime nicely
            sec = int(d.uptime_s)
            d_d, rem = divmod(sec, 86400)
            d_h, rem = divmod(rem, 3600)
            d_m, d_s = divmod(rem, 60)
            up_str = f"{d_d}d {d_h:02}:{d_m:02}" if d_d > 0 else f"{d_h:02}:{d_m:02}:{d_s:02}"
            self._router_quick_labels["up"].config(text=up_str)
            self._router_quick_labels["stat"].config(text="Verbunden", fg=C["grn"])
            
            # CPU & Traffic
            cpu = f"{d.cpu_load}%" if d.cpu_load is not None else "–"
            self._router_quick_labels["cpu"].config(text=cpu)
            self._router_quick_labels["rx"].config(text=f"{d.traffic_in_mbps:.1f} M/s")
            self._router_quick_labels["tx"].config(text=f"{d.traffic_out_mbps:.1f} M/s")
        else:
            self._router_quick_labels["sys"].config(text="–")
            self._router_quick_labels["up"].config(text="–")
            self._router_quick_labels["stat"].config(text="Offline", fg=C["red"])
            self._router_quick_labels["cpu"].config(text="–")
            self._router_quick_labels["rx"].config(text="–")
            self._router_quick_labels["tx"].config(text="–")

    def _update_zte(self, d):
        """Aktualisiert das ZTE MF281 Dashboard-HUD mit aktuellen Telemetriedaten."""
        if not hasattr(self, '_zte_labels') or not self._zte_labels:
            return
        lbl = self._zte_labels
        if d.reachable:
            # ── Akku ────────────────────────────────────────
            pct = d.battery_pct or 0
            batt_col = C["grn"] if pct > 30 else C["org"] if pct > 10 else C["red"]
            lbl["akku"].config(
                text=f"{pct}%" if d.battery_pct is not None else "–",
                fg=batt_col)
            lbl["charge"].config(text=d.battery_charge[:8])

            # ── Signal (Balken, da RSRP vom MF281 nicht geliefert wird) ───
            bars     = d.signal_bars          # 0–5
            filled   = "█" * bars + "░" * (5 - bars)
            # Qualitätstext aus Balken ableiten
            bar_qual = ["❌", "⚠ Sehr schwach", "⚠ Schwach",
                        "Mäßig", "Gut", "★ Ausgezeichnet"]
            qual     = bar_qual[min(bars, 5)]
            sig_col  = (C["grn"] if bars >= 4
                        else C["org"] if bars >= 2
                        else C["red"])
            lbl["signal"].config(text=f"{filled} {bars}/5", fg=sig_col)

            # ── Anbieter ────────────────────────────────────
            prov = d.network_provider.replace("?", "") or "–"
            lbl["provider"].config(text=prov[:10])

            # ── WAN-Status ────────────────────────────────
            wan_st = d.wan_status or "–"
            # ipv6_connected → kompaktere Darstellung
            wan_disp = (wan_st
                        .replace("ipv6_connected", "IPv6 ✔")
                        .replace("ipv4_connected", "IPv4 ✔")
                        .replace("connecting",     "..."))
            wan_col = (C["grn"] if "connected" in wan_st.lower()
                       else C["org"] if "connecting" in wan_st.lower()
                       else C["red"])
            lbl["wan"].config(text=wan_disp[:10], fg=wan_col)

            # ── Netztyp ─────────────────────────────────────
            lbl["netz"].config(text=d.network_type[:8])

            # ── Durchsatz ──────────────────────────────────
            lbl["dl"].config(text=f"{d.dl_mbps():.1f}M")
            lbl["ul"].config(text=f"{d.ul_mbps():.2f}M")

            # ── Status-Zeile (vollständigere Info) ──────────────
            ip_info = f"  IP: {d.wan_ip}" if d.wan_ip else ""
            self._zte_status_lbl.config(
                text=f"  {filled} {qual}  |  {prov}  |  {d.network_type}{ip_info}",
                fg=C["tx2"])
        else:
            for k in lbl:
                lbl[k].config(text="–", fg=C["sub"])
            self._zte_status_lbl.config(text="  ⚠ ZTE nicht erreichbar", fg=C["red"])

    def _update_repeater_alarms(self, state):
        """Aktualisiert das Repeater-Alarm-Panel – zeigt Messwerte statt nur OK/ALARM."""
        self._repeater_alarm = state
        if not hasattr(self, '_rep_alarm_labels'):
            return

        traps_ok = (state.trap_anzahl > 0)

        # Messwert-Mapping: alarm_key -> (state_attr, format_string, unit)
        # Wenn Messwert vorhanden: wird statt OK angezeigt
        WERT_MAP = {
            "vswr":        ("vswr_wert",   "{:.2f}",  ""),
            "temperatur":  ("temp_wert",   "{:.1f}",  "°C"),
            "luefter":     ("fan_wert",    "{}",      " RPM"),
            "spannung":    ("volt_wert",   "{:.1f}",  " V"),
            "fw_leistung": ("fw_pwr_watt", "{:.1f}",  " W"),
            "rw_leistung": ("rw_pwr_watt", "{:.1f}",  " W"),
            "tx_pll":      (None, None, None),
            "rx_pll":      (None, None, None),
            "batterie":    ("batt_volt",   "{:.1f}",  " V"),
        }

        for key, (box, lbl) in self._rep_alarm_labels.items():
            alarm_key = key if key != "vswr" else "vswr_alarm"
            alarm_val = getattr(state, alarm_key, None)

            # Alarm hat hoechste Prioritaet
            if alarm_val == 1:
                lbl.config(text="ALARM!", fg=C["red"])
                box.config(highlightbackground=C["red"], bg="#1a0005")
                lbl.config(bg="#1a0005")
                continue

            # Messwert abrufen
            wert_attr, fmt, unit = WERT_MAP.get(key, (None, None, None))
            messwert = getattr(state, wert_attr, None) if wert_attr else None

            if messwert is not None:
                # Messwert vorhanden -> direkt anzeigen
                val_str = fmt.format(messwert) + unit
                # Farbe je nach Wert
                if key == "vswr":
                    col = C["grn"] if messwert < 1.5 else C["org"] if messwert < 2.0 else C["red"]
                elif key == "temperatur":
                    col = C["grn"] if messwert < 50 else C["org"] if messwert < 65 else C["red"]
                elif key in ("fw_leistung", "rw_leistung"):
                    col = C["acc2"]
                else:
                    col = C["grn"]
                lbl.config(text=val_str, fg=col)
                box.config(highlightbackground=col, bg=C["dim"])
                lbl.config(bg=C["dim"])
            elif alarm_val == 0 or (alarm_val is None and traps_ok):
                lbl.config(text="OK", fg=C["grn"])
                box.config(highlightbackground=C["grn"], bg=C["dim"])
                lbl.config(bg=C["dim"])
            else:
                lbl.config(text="?", fg=C["sub"])
                box.config(highlightbackground=C["sub"], bg=C["dim"])
                lbl.config(bg=C["dim"])

        # Status-Zeile
        parts = []
        if state.vswr_wert   is not None: parts.append(f"VSWR {state.vswr_wert:.2f}")
        if state.temp_wert   is not None: parts.append(f"Temp {state.temp_wert:.1f}°C")
        if state.fw_pwr_watt is not None: parts.append(f"Fwd {state.fw_pwr_watt:.1f}W")
        if state.rw_pwr_watt is not None: parts.append(f"Rfl {state.rw_pwr_watt:.1f}W")
        if state.volt_wert   is not None: parts.append(f"U {state.volt_wert:.1f}V")

        messwert_str = "  |  " + "  ".join(parts) if parts else ""

        if state.irgendein_alarm:
            alarms = ", ".join(state.alarm_liste)
            self._rep_status_lbl.config(
                text=f"  [!] ALARM: {alarms}{messwert_str}  |  #{state.trap_anzahl} Traps",
                fg=C["red"])
            self._mission_log_entry(f"\U0001f534 Repeater Alarm: {alarms}", "ptt")
            self._mission.log_event("repeater_alarm", {"alarme": state.alarm_liste})
        else:
            self._rep_status_lbl.config(
                text=f"  [OK] Normal{messwert_str}  |  #{state.trap_anzahl} Traps  |  {state.quelle_ip}",
                fg=C["grn"])

    def _usv_poll_now(self):
        if self._usv_mon:
            threading.Thread(
                target=lambda: self.after(0, self._update_usv,
                                          self._usv_mon.poll()),
                daemon=True).start()
        else:
            from usv_monitor import snmp_discover
            ip   = self._s_usv_ip.get().strip()
            comm = self._s_usv_comm.get().strip()
            self._log_msg(f"USV-Test: {ip} …", "acc")
            def run():
                ok, result = snmp_discover(ip, comm)
                if ok:
                    self.after(0, self._update_usv, result)
                    self.after(0, self._log_msg,
                               f"USV erreichbar – Batterie {result.batterie_pct}%", "ok")
                else:
                    self.after(0, self._log_msg, f"USV Fehler: {result}", "err")
            threading.Thread(target=run, daemon=True).start()

    # ── Header-Tick (Uhr + Verbindungsstatus) ─────────────
    def _tick_header(self):
        now = time.time()
        self._lbl_clock.config(text=datetime.now().strftime("%H:%M:%S"))
        age = now - self._last_hb if self._last_hb else None
        if age is None:
            self._dot.config(fg=C["sub"])
            self._lbl_conn.config(text="kein Signal", fg=C["sub"])
        elif age < 15:
            self._dot.config(fg=C["grn"])
            self._lbl_conn.config(
                text=f"Verbunden  {self._hb_addr}  ({int(age)}s)", fg=C["grn"])
        elif age < 45:
            self._dot.config(fg=C["org"])
            self._lbl_conn.config(
                text=f"Schwach  {self._hb_addr}  ({int(age)}s)", fg=C["org"])
        else:
            self._dot.config(fg=C["red"])
            self._lbl_conn.config(text=f"Verloren  ({int(age)}s)", fg=C["red"])
        self.after(1000, self._tick_header)

    # ══════════════════════════════════════════════════════
    #  ID-Editor Methoden
    # ══════════════════════════════════════════════════════
    def _refresh_id_table(self):
        """Tabelle aus _ids dict neu aufbauen."""
        for row in self._tv_ids.get_children():
            self._tv_ids.delete(row)
        for rid, entry in sorted(_ids.items(), key=lambda x: x[0]):
            self._tv_ids.insert("", "end", iid=rid,
                                values=(rid,
                                        entry.get("name", ""),
                                        entry.get("geraet", "")))

    def _id_on_select(self, _event=None):
        """Selektierte Zeile in die Eingabefelder laden."""
        sel = self._tv_ids.selection()
        if not sel:
            return
        rid = sel[0]
        entry = _ids.get(rid, {})
        self._id_f_id.set(rid)
        self._id_f_name.set(entry.get("name", ""))
        self._id_f_geraet.set(entry.get("geraet", ""))

    def _id_add(self):
        rid   = self._id_f_id.get().strip()
        name  = self._id_f_name.get().strip()
        geraet= self._id_f_geraet.get().strip()
        if not rid or not name:
            messagebox.showwarning("Eingabe fehlt",
                                   "Radio-ID und Name sind Pflichtfelder.")
            return
        if rid in _ids:
            if not messagebox.askyesno("Überschreiben?",
                                       f"ID {rid} existiert bereits.\nÜberschreiben?"):
                return
        _ids[rid] = {"name": name, "geraet": geraet}
        self._refresh_id_table()
        self._id_f_id.set(""); self._id_f_name.set(""); self._id_f_geraet.set("")
        self._log_msg(f"[IDs] Eingetragen: {rid} → {name}", "ok")

    def _id_edit(self):
        sel = self._tv_ids.selection()
        if not sel:
            messagebox.showinfo("Hinweis", "Bitte erst eine Zeile auswählen.")
            return
        old_rid = sel[0]
        new_rid = self._id_f_id.get().strip()
        name    = self._id_f_name.get().strip()
        geraet  = self._id_f_geraet.get().strip()
        if not new_rid or not name:
            messagebox.showwarning("Eingabe fehlt",
                                   "Radio-ID und Name sind Pflichtfelder.")
            return
        if old_rid != new_rid:
            del _ids[old_rid]
        _ids[new_rid] = {"name": name, "geraet": geraet}
        self._refresh_id_table()
        self._log_msg(f"[IDs] Aktualisiert: {new_rid} → {name}", "ok")

    def _id_delete(self):
        sel = self._tv_ids.selection()
        if not sel:
            messagebox.showinfo("Hinweis", "Bitte erst eine Zeile auswählen.")
            return
        rid = sel[0]
        name = _ids.get(rid, {}).get("name", rid)
        if messagebox.askyesno("Löschen?", f"Eintrag '{name}' (ID {rid}) löschen?"):
            del _ids[rid]
            self._refresh_id_table()
            self._id_f_id.set(""); self._id_f_name.set(""); self._id_f_geraet.set("")
            self._log_msg(f"[IDs] Gelöscht: {rid}", "warn")

    def _id_save(self):
        save_ids()
        load_ids()   # globales Dict neu laden
        self._log_msg(f"[IDs] {len(_ids)} Einträge in ids.json gespeichert.", "ok")
        # ── Echtzeit-Update ans Browser-Dashboard senden ──────────────
        # Pusht __ids_update__ per SSE – kein Neustart, kein Reconnect nötig
        srv = getattr(self, '_sse_server', None)
        if srv:
            srv.broadcast_ids_update(_ids)
            self._log_msg("[IDs] → Live-Dashboard aktualisiert.", "acc")

    # == USV Dashboard-Schnellansicht ==================
    def _update_usv_quick(self, d):
        if not hasattr(self, '_usv_quick_labels'): return
        ql = self._usv_quick_labels
        if d.erreichbar:
            pct = d.batterie_pct or 0
            col = '#ef5350' if pct < 20 else '#ffb74d' if pct < 50 else '#a6e3a1'
            ql['batt'].config(text=f'{pct}% ({d.batterie_status or chr(45)})', fg=col)
            nc = '#a6e3a1' if d.netz_status == 'Normal' else '#ef5350'
            ql['netz'].config(text=d.netz_status or '-', fg=nc)
            ql['volt'].config(text=f'{d.ausgang_volt or chr(45)} V  /  {d.ausgang_hz or chr(45)} Hz', fg='#e0e0e0')
            laufz = f'{d.batterie_min} min' if d.batterie_min else '-'
            lc = '#ffb74d' if (d.batterie_min or 999) < 30 else '#e0e0e0'
            ql['laufz'].config(text=laufz, fg=lc)
            lpc = (d.last_pct or 0)
            ql['last'].config(text=f'{lpc} %', fg='#ef5350' if lpc > 80 else '#e0e0e0')
        else:
            for lv in ql.values(): lv.config(text='n/a', fg='#546e7a')


    def _on_close(self):

        self._stop()
        self._audio.terminate()
        save_config(self.cfg)
        self.destroy()



# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-8s  %(levelname)s  %(message)s")
    App().mainloop()
