# -*- coding: utf-8 -*-
"""
sdr_monitor.py – Dummy-Modul für RTL-SDR Integration
Liest (später) Daten von RTL-SDR / DSD+ aus, um Direktfunk von RT81 abzufangen.
"""
import threading
import time

class SDRMonitor:
    def __init__(self, freq, gain="auto", ppm=0, callback=None, log_cb=None):
        self.freq = freq
        self.gain = gain
        self.ppm = ppm
        self.callback = callback
        self.log_cb = log_cb
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="SDRThread")
        self._thread.start()
        if self.log_cb:
            self.log_cb(f"[SDR] Gestartet auf {self.freq} MHz (Gain: {self.gain}, PPM: {self.ppm})", "ok")

    def stop(self):
        if not self._running:
            return
        self._running = False
        if self.log_cb:
            self.log_cb("[SDR] Gestoppt.", "warn")

    def _loop(self):
        """Simulation: Hier kommt später der rtl_fm / DSD+ Aufruf rein."""
        while self._running:
            # Hier würden später die dekodierten IDs vom SDR gelesen und an das Dashboard geleitet
            # z.B. self.callback({"type": "sdr_call", "source": "1001", "target": "1002"})
            time.sleep(1)
