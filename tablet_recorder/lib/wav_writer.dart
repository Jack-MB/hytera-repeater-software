// wav_writer.dart – Schreibt PCM-16-Samples als WAV-Datei
import 'dart:io';
import 'dart:typed_data';

class WavWriter {
  static const int _sampleRate = 44100; // record-Package Standard
  static const int _channels = 1;
  static const int _bitsPerSample = 16;

  /// Schreibt einen WAV-Header + PCM-Daten in eine Datei.
  /// [pcmBytes] sind rohe Int16 Little-Endian Samples.
  static Future<void> write(File file, Uint8List pcmBytes,
      {int sampleRate = _sampleRate}) async {
    final header = _buildHeader(pcmBytes.length, sampleRate);
    final raf = await file.open(mode: FileMode.write);
    await raf.writeFrom(header);
    await raf.writeFrom(pcmBytes);
    await raf.close();
  }

  static Uint8List _buildHeader(int dataLen, int sampleRate) {
    final byteRate = sampleRate * _channels * (_bitsPerSample ~/ 8);
    final blockAlign = _channels * (_bitsPerSample ~/ 8);

    final buf = ByteData(44);

    // RIFF
    _writeStr(buf, 0, 'RIFF');
    buf.setUint32(4, 36 + dataLen, Endian.little);
    _writeStr(buf, 8, 'WAVE');
    // fmt
    _writeStr(buf, 12, 'fmt ');
    buf.setUint32(16, 16, Endian.little);
    buf.setUint16(20, 1, Endian.little); // PCM
    buf.setUint16(22, _channels, Endian.little);
    buf.setUint32(24, sampleRate, Endian.little);
    buf.setUint32(28, byteRate, Endian.little);
    buf.setUint16(32, blockAlign, Endian.little);
    buf.setUint16(34, _bitsPerSample, Endian.little);
    // data
    _writeStr(buf, 36, 'data');
    buf.setUint32(40, dataLen, Endian.little);

    return buf.buffer.asUint8List();
  }

  static void _writeStr(ByteData bd, int offset, String s) {
    for (int i = 0; i < s.length; i++) {
      bd.setUint8(offset + i, s.codeUnitAt(i));
    }
  }
}
