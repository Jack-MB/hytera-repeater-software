"""
Hytera Tactical Suite - Audio Manager & Funkspruch-Sprachrekorder (Voice-Log)
Erzeugt und verwaltet Audio-Mitschnitte von DMR-Funksprüchen.
Synthetisiert realistische taktische DMR-Audiodateien (WAV mit Roger-Beep und Rauschsperre)
zur sofortigen Wiedergabe im Browser ohne externe Bibliotheken.
"""

import os
import wave
import math
import struct
import random
import logging
from typing import Optional
try:
    from .config import RECORDINGS_DIR
except (ImportError, ValueError):
    from config import RECORDINGS_DIR

logger = logging.getLogger("audio_manager")


class AudioManager:
    """Verwaltet Audio-Mitschnitte und generiert taktische Funk-Audiosamples."""

    def __init__(self, recordings_dir: str = RECORDINGS_DIR):
        self.recordings_dir = recordings_dir
        os.makedirs(self.recordings_dir, exist_ok=True)

    def generate_tactical_radio_audio(self, call_id: int, duration_sec: float = 2.5, alias: str = "Einheit") -> str:
        """
        Synthetisiert eine realistische DMR-Funkaufnahme als standardkonforme 16-Bit WAV-Datei:
        - 1. Kurzer Squelch / DMR Preamble Beep (150ms)
        - 2. Moduliertes Funkrauschen / Sprachsimulation (Dauer)
        - 3. Hytera Roger-Beep Doppel-Quittungston am Ende (100ms)
        """
        filename = f"call_{call_id}.wav"
        file_path = os.path.join(self.recordings_dir, filename)

        # Wenn die Datei schon existiert, URL direkt zurückgeben
        if os.path.isfile(file_path):
            return f"/static/recordings/{filename}"

        sample_rate = 16000
        num_samples = int(sample_rate * max(1.0, min(duration_sec, 6.0)))

        samples = []

        # 1. Preamble: Kurzer DMR Sync-Ton (1200 Hz, 80 ms)
        pre_samples = int(sample_rate * 0.08)
        for i in range(pre_samples):
            t = i / sample_rate
            val = math.sin(2.0 * math.pi * 1200.0 * t) * 0.4
            samples.append(val)

        # 2. Body: Gefiltertes Sprechrauschen & Formant-Schwingung
        body_samples = num_samples - pre_samples - int(sample_rate * 0.15)
        phase1 = 0.0
        phase2 = 0.0
        base_freq = random.uniform(180.0, 260.0)

        for i in range(body_samples):
            t = i / sample_rate
            # Sprach-Grundfrequenz & Obertöne
            mod = 0.3 * math.sin(2.0 * math.pi * base_freq * t) + 0.15 * math.sin(2.0 * math.pi * base_freq * 2.1 * t)
            # Dezentes Funk-Grundrauschen
            noise = (random.random() * 2.0 - 1.0) * 0.08
            val = mod + noise
            samples.append(val)

        # 3. Roger-Beep: Klassischer Hytera/Motorola 2-Ton Quittungston (880 Hz -> 1760 Hz)
        tone1_samples = int(sample_rate * 0.06)
        for i in range(tone1_samples):
            t = i / sample_rate
            val = math.sin(2.0 * math.pi * 880.0 * t) * 0.5
            samples.append(val)

        tone2_samples = int(sample_rate * 0.06)
        for i in range(tone2_samples):
            t = i / sample_rate
            val = math.sin(2.0 * math.pi * 1760.0 * t) * 0.5
            samples.append(val)

        # In 16-Bit Signed Integer wandeln und WAV schreiben
        raw_bytes = bytearray()
        for s in samples:
            clamped = max(-1.0, min(1.0, s))
            int_val = int(clamped * 32767.0)
            raw_bytes.extend(struct.pack("<h", int_val))

        try:
            with wave.open(file_path, "wb") as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-Bit
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(raw_bytes)
            logger.info(f"Funkspruch-Audio generiert: {filename}")
        except Exception as e:
            logger.error(f"Fehler beim Erstellen der Audiodatei: {e}")

        return f"/static/recordings/{filename}"

    def get_audio_path(self, filename: str) -> Optional[str]:
        p = os.path.join(self.recordings_dir, filename)
        return p if os.path.isfile(p) else None


audio_manager = AudioManager()
