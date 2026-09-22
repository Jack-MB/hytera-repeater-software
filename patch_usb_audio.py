import re
import os

patch_code = '''
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
        
        self._usb_capture = None
        self._refresh_usb_devices()
        
        # Aufnahmen-Liste
'''

methods_code = '''
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
            import os
            out_dir = os.path.join(RECORD_DIR, "USB_Direkt")
            
            def _status_cb(st, fn):
                msg = f"{st}: {os.path.basename(fn)}" if fn else st
                color = C["grn"] if st=="recording" else C["sub"]
                self.after(0, lambda: self._lbl_usb_stat.config(text=msg, fg=color))
                
            self._usb_capture = DirectAudioCapture(
                device_index=idx,
                out_dir=out_dir,
                vox_threshold=0, 
                status_cb=_status_cb
            )
            self._usb_capture.start()
            self._btn_usb_rec.config(text="⏹ Aufnahme stoppen", fg=C["tx"])
        except Exception as e:
            self._lbl_usb_stat.config(text=f"Fehler: {e}", fg=C["red"])

    def _refresh_devices(self):
'''

def apply_patch(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        
    if "Lokale Aufnahme (USB" in content:
        print(f"Already patched {filepath}")
        return
        
    # Replace the UI part
    content = content.replace("        # Aufnahmen-Liste\n", patch_code)
    
    # Replace the method part
    content = content.replace("    def _refresh_devices(self):\n", methods_code)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Successfully patched {filepath}")

apply_patch('c:/Users/jc/Hytera Repetaer Software/hytera_project/app.py')
apply_patch('c:/Users/jc/Hytera Repetaer Software/hytera_project_v1_original/app.py')
