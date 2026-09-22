import re

def patch_app(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        code = f.read()

    # 1. Add Entry field
    if 'self._var_usb_name = tk.StringVar' not in code:
        old_ui = '''        self._cmb_usb_in = ttk.Combobox(u_row, width=42, state="readonly", font=FONT_UI)
        self._cmb_usb_in.pack(side="left")'''
        new_ui = '''        self._cmb_usb_in = ttk.Combobox(u_row, width=42, state="readonly", font=FONT_UI)
        self._cmb_usb_in.pack(side="left")
        
        tk.Label(u_row, text="Kanal-Name:", font=FONT_BOLD,
                 bg=C["panel"], fg=C["sub"]).pack(side="left", padx=(16, 8))
                 
        self._var_usb_name = tk.StringVar(value="USB-Live")
        self._ent_usb_name = tk.Entry(u_row, textvariable=self._var_usb_name, font=FONT_UI, width=15,
                                      bg="#050508", fg=C["tx"], insertbackground=C["tx"], relief="flat")
        self._ent_usb_name.pack(side="left")'''
        code = code.replace(old_ui, new_ui)

    # 2. Add usb_name to logging
    if 'usb_name =' not in code:
        old_cb = '''            def _status_cb(st, fn):
                msg = f"{st}: {os.path.basename(fn)}" if fn else st'''
        new_cb = '''            c_name = getattr(self, "_var_usb_name", None)
            usb_name = c_name.get().strip() if c_name else "USB-Live"
            def _status_cb(st, fn):
                msg = f"{st}: {os.path.basename(fn)}" if fn else st'''
        code = code.replace(old_cb, new_cb)
        
        old_start = '''                        self._mission.log_event({
                            "type": "ptt_start",
                            "radio_id": 999999,
                            "slot": "USB",
                            "rssi": -40,
                            "_byteCount": 0 # Prevent standard audio fetch
                        })'''
        new_start = '''                        self._mission.log_event({
                            "type": "ptt_start",
                            "radio_id": 999999,
                            "slot": "USB",
                            "name": usb_name,
                            "rssi": -40,
                            "_byteCount": 0 # Prevent standard audio fetch
                        })'''
        code = code.replace(old_start, new_start)

        old_end = '''                            self._mission.log_event({
                                "type": "ptt_end",
                                "radio_id": 999999,
                                "slot": "USB",
                                "duration": round(dur, 1),
                                "byte_count": 0,
                                "_audio_file": "/rec/" + fn_base if fn_base else None
                            })'''
        new_end = '''                            self._mission.log_event({
                                "type": "ptt_end",
                                "radio_id": 999999,
                                "slot": "USB",
                                "name": usb_name,
                                "duration": round(dur, 1),
                                "byte_count": 0,
                                "_audio_file": "/rec/" + fn_base if fn_base else None
                            })'''
        code = code.replace(old_end, new_end)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(code)

patch_app('c:/Users/jc/Hytera Repetaer Software/hytera_project/app.py')
patch_app('c:/Users/jc/Hytera Repetaer Software/hytera_project_v1_original/app.py')
print('Custom name support applied!')
