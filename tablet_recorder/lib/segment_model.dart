// segment_model.dart – Datenmodell für ein aufgenommenes PTT-Segment
import 'dart:io';

class PttSegment {
  final int index;
  final DateTime timestamp;
  final Duration duration;
  final File wavFile;
  double peakAmplitude;

  PttSegment({
    required this.index,
    required this.timestamp,
    required this.duration,
    required this.wavFile,
    this.peakAmplitude = 0.0,
  });

  String get displayTime {
    final h = timestamp.hour.toString().padLeft(2, '0');
    final m = timestamp.minute.toString().padLeft(2, '0');
    final s = timestamp.second.toString().padLeft(2, '0');
    return '$h:$m:$s';
  }

  String get displayDuration {
    final secs = duration.inSeconds;
    final ms = (duration.inMilliseconds % 1000) ~/ 100;
    return '$secs.${ms}s';
  }

  String get filename => wavFile.uri.pathSegments.last;
}
