# -*- coding: utf-8 -*-
"""
direct_audio.py
Kontinuierliche Audioaufnahme von einem lokalen Soundkarteneingang.
Unterstützt VOX-Modus (automatische Segmentierung nach Stille) und
durchgehende Aufnahme (ein WAV pro Session).
Gedacht für: Funkgerät via 3,5mm Klinke oder USB-Soundkarte direkt am PC.
"""

import threading
import time
import wave
import struct
import math
import os
import logging
from datetime import datetime

log = logging.getLogger("direct_audio")

RATE     = 44100    # Hz – Standard-Audioqualität
CHUNK    = 4096     # Frames pro Block (~93ms bei 44100)
FMT_PAx  = None     # wird zur Laufzeit aus pyaudio befüllt
CHANNELS = 1        # Mono reicht für Funkaudio


def list_input_devices():
    """Gibt eine Liste von (index, name) aller verfügbaren Eingabegeräte zurück."""
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        devs = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0:
                devs.append((i, info["name"]))
        pa.terminate()
        return devs
    except Exception as e:
        log.warning(f"list_input_devices: {e}")
        return []


class DirectAudioCapture(threading.Thread):
    """
    Zeichnet Audio von einem lokalen Eingang dauerhaft auf.

    Parameters
    ----------
    device_index : int  – PyAudio-Geräte-Index (-1 = Standard)
    out_dir      : str  – Zielordner für WAV-Dateien
    vox_threshold: int  – RMS-Schwelle für VOX (0 = immer aufnehmen)
    vox_hold_sec : float – Haltedauer nach letztem Signal (VOX-Modus)
    rotate_min   : int  – Datei nach N Minuten rotieren (0 = nie, nur im Endlosmodus)
    level_cb     : callable(int 0-100)  – Audiopegel-Callback
    status_cb    : callable(str, str)   – status("recording"|"standby", dateiname)
    file_cb      : callable(str)        – Callback wenn eine Datei fertig ist
    """

    def __init__(self, device_index, out_dir,
                 vox_threshold=0, vox_hold_sec=2.0, rotate_min=60,
                 level_cb=None, status_cb=None, file_cb=None):
        super().__init__(daemon=True, name="DirectAudioCapture")
        self.device_index  = device_index
        self.out_dir       = out_dir
        self.vox_threshold = vox_threshold
        self.vox_hold_sec  = vox_hold_sec
        self.rotate_min    = rotate_min
        self.level_cb      = level_cb
        self.status_cb     = status_cb
        self.file_cb       = file_cb
        self._stop_ev      = threading.Event()
        self.current_file  = ""
        self.bytes_written = 0

    def stop(self):
        self._stop_ev.set()

    @staticmethod
    def _rms(data):
        """Effektivwert des Audio-Chunks (PCM int16)."""
        n = len(data) // 2
        if n == 0:
            return 0
        samples = struct.unpack(f"{n}h", data)
        return math.sqrt(sum(s * s for s in samples) / n)

    def _open_wav(self, pa):
        """Öffnet eine neue WAV-Datei und gibt (wav_file, fname) zurück."""
        import pyaudio
        os.makedirs(self.out_dir, exist_ok=True)
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn  = os.path.join(self.out_dir, f"DIREKT_{ts}.wav")
        wf  = wave.open(fn, "wb")
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
        wf.setframerate(RATE)
        return wf, fn

    def run(self):
        try:
            import pyaudio
        except ImportError:
            log.error("pyaudio nicht installiert – pip install pyaudio")
            if self.status_cb:
                self.status_cb("fehler", "pyaudio fehlt")
            return

        pa     = pyaudio.PyAudio()
        dev_kw = {} if self.device_index < 0 else {"input_device_index": self.device_index}

        # Samplerate des Gerätes bestimmen
        rate = RATE
        if self.device_index >= 0:
            try:
                info = pa.get_device_info_by_index(self.device_index)
                rate = int(info.get("defaultSampleRate", RATE))
            except Exception:
                pass

        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=rate,
                input=True,
                frames_per_buffer=CHUNK,
                **dev_kw,
            )
        except Exception as e:
            log.error(f"Audio-Stream konnte nicht geöffnet werden: {e}")
            if self.status_cb:
                self.status_cb("fehler", str(e))
            pa.terminate()
            return

        log.info(f"Direktaufnahme gestartet: Gerät={self.device_index}, Rate={rate}Hz")

        wf           = None
        fname        = ""
        last_signal  = 0.0      # Zeitpunkt des letzten Signals (VOX)
        recording    = False    # nur für VOX-Modus relevant
        file_start   = 0.0     # Zeitpunkt des Datei-Starts (für Rotation)

        while not self._stop_ev.is_set():
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
            except Exception as e:
                log.warning(f"Lesefehler: {e}")
                time.sleep(0.05)
                continue

            rms   = self._rms(data)
            level = min(100, int(rms / 327))      # 32767 max → 100%
            if self.level_cb:
                try:
                    self.level_cb(level)
                except Exception:
                    pass

            now = time.time()

            # ── Endlos-Modus (vox_threshold == 0) ─────────────
            if self.vox_threshold == 0:
                # Datei öffnen wenn noch keine läuft
                if wf is None:
                    wf, fname = self._open_wav(pa)
                    self.current_file = fname
                    file_start = now
                    if self.status_cb:
                        self.status_cb("recording", fname)
                    log.info(f"Neue Aufnahmedatei: {fname}")

                wf.writeframes(data)
                self.bytes_written += len(data)

                # Datei rotieren wenn rotate_min > 0
                if self.rotate_min > 0 and (now - file_start) >= self.rotate_min * 60:
                    wf.close()
                    if self.file_cb:
                        try:
                            self.file_cb(fname)
                        except Exception:
                            pass
                    wf, fname = self._open_wav(pa)
                    self.current_file = fname
                    file_start = now
                    if self.status_cb:
                        self.status_cb("recording", fname)
                    log.info(f"Datei rotiert → {fname}")

            # ── VOX-Modus ──────────────────────────────────────
            else:
                active_signal = rms >= self.vox_threshold

                if active_signal:
                    last_signal = now
                    if not recording:
                        # Aufnahme starten
                        recording = True
                        wf, fname = self._open_wav(pa)
                        self.current_file = fname
                        file_start = now
                        if self.status_cb:
                            self.status_cb("recording", fname)
                        log.info(f"VOX ausgelöst → {fname}")
                    wf.writeframes(data)
                    self.bytes_written += len(data)

                elif recording:
                    # Tail-Hold: noch CHUNK_HOLD Sekunden schreiben nach Stille
                    wf.writeframes(data)
                    self.bytes_written += len(data)
                    if now - last_signal > self.vox_hold_sec:
                        # Stille: Datei schließen
                        recording = False
                        wf.close()
                        if self.file_cb:
                            try:
                                self.file_cb(fname)
                            except Exception:
                                pass
                        wf = None
                        self.current_file = ""
                        if self.status_cb:
                            self.status_cb("standby", "")
                        log.info("VOX-Ende – Datei geschlossen")
                else:
                    # Stille, keine Aufnahme
                    if self.status_cb and not recording:
                        pass  # Status bleibt "standby"

        # ── Aufräumen ──────────────────────────────────────────
        if wf is not None:
            try:
                wf.close()
                if self.file_cb:
                    self.file_cb(fname)
            except Exception:
                pass

        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        pa.terminate()
        self.current_file = ""
        if self.status_cb:
            self.status_cb("gestoppt", "")
        log.info("Direktaufnahme beendet")
