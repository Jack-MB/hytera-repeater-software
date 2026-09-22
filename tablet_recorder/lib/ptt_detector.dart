// ptt_detector.dart – Erkennt PTT-Aktivität anhand des Amplitudenwertes
import 'dart:typed_data';

enum PttState { idle, active }

class PttDetector {
  /// Amplitude 0.0 – 1.0 ab der PTT als aktiv gilt
  double threshold;

  /// Millisekunden Stille bevor PTT als beendet gilt
  int silenceMs;

  PttState _state = PttState.idle;
  DateTime? _silenceStart;
  DateTime? _pttStart;

  // Callbacks
  final void Function() onPttStart;

  /// pcmBytes: leer (Platzhalter), duration: Dauer der PTT
  final void Function(Uint8List pcmBytes, Duration duration) onPttEnd;

  PttDetector({
    required this.onPttStart,
    required this.onPttEnd,
    this.threshold = 0.015,
    this.silenceMs = 1500,
  });

  // ─────────────────────────────────────────────────────────────────────────
  // Primärer Eingang: PCM-Bytes (Int16 LE)
  // ─────────────────────────────────────────────────────────────────────────
  void processPcm(Uint8List rawPcm) {
    if (rawPcm.isEmpty) return;
    double peak = 0.0;
    final bd = ByteData.sublistView(rawPcm);
    for (int i = 0; i + 1 < rawPcm.length; i += 2) {
      final sample = bd.getInt16(i, Endian.little);
      final amp = sample.abs() / 32768.0;
      if (amp > peak) peak = amp;
    }
    processAmplitude(peak);
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Alternativer Eingang: normalisierter Amplitudenwert 0.0 – 1.0
  // (vom record-Package Amplitude-Callback, kein direkter PCM-Zugriff)
  // ─────────────────────────────────────────────────────────────────────────
  void processAmplitude(double amp) {
    if (amp > threshold) {
      // --- Signal vorhanden ---
      if (_state == PttState.idle) {
        _state = PttState.active;
        _pttStart = DateTime.now();
        _silenceStart = null;
        onPttStart();
      } else {
        // PTT läuft – Stille-Timer zurücksetzen
        _silenceStart = null;
      }
    } else {
      // --- Stille ---
      if (_state == PttState.active) {
        _silenceStart ??= DateTime.now();
        final silentMs =
            DateTime.now().difference(_silenceStart!).inMilliseconds;
        if (silentMs >= silenceMs) {
          final duration = _pttStart != null
              ? DateTime.now().difference(_pttStart!)
              : Duration.zero;
          _state = PttState.idle;
          _silenceStart = null;
          _pttStart = null;
          // PCM-Bytes: der record-Prozess schreibt in die Datei,
          // wir übergeben hier leere Bytes als Signal
          onPttEnd(Uint8List(0), duration);
        }
      }
    }
  }

  void reset() {
    _state = PttState.idle;
    _silenceStart = null;
    _pttStart = null;
  }

  PttState get state => _state;
}
