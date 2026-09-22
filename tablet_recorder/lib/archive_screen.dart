// archive_screen.dart – Einsatz-Archiv: vergangene Missionen laden & abspielen
import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import 'segment_model.dart';
import 'tailscale_server.dart';

// ─── Design-Tokens (identisch zu main.dart) ──────────────────────────────────
class _C {
  static const bg        = Color(0xFF0a0a0f);
  static const panel     = Color(0xFF0d0d1a);
  static const border    = Color(0xFF1a1a2e);
  static const acc       = Color(0xFFfce300);
  static const acc2      = Color(0xFF00fff7);
  static const red       = Color(0xFFff003c);
  static const grn       = Color(0xFF39ff14);
  static const org       = Color(0xFFff6b00);
  static const tx        = Color(0xFFe0e0e0);
  static const muted     = Color(0xFF666680);
}

// ─── Modell ───────────────────────────────────────────────────────────────────
class _Mission {
  final String name;
  final Directory dir;
  final DateTime date;
  List<PttSegment> segments;

  _Mission({
    required this.name,
    required this.dir,
    required this.date,
    this.segments = const [],
  });

  // WAV-Dateien im Ordner laden und als PttSegment-Liste aufbauen
  static Future<_Mission> fromDir(Directory dir) async {
    final name = dir.uri.pathSegments.where((s) => s.isNotEmpty).last;

    // Datum aus Ordnernamen parsen: Einsatz_YYYY-MM-DD_HH-MM
    DateTime date = dir.statSync().modified;
    try {
      final parts = name.split('_');
      if (parts.length >= 3) {
        final dateParts = parts[1].split('-');
        final timeParts = parts[2].split('-');
        if (dateParts.length == 3 && timeParts.length >= 2) {
          date = DateTime(
            int.parse(dateParts[0]),
            int.parse(dateParts[1]),
            int.parse(dateParts[2]),
            int.parse(timeParts[0]),
            int.parse(timeParts[1]),
          );
        }
      }
    } catch (_) {}

    // WAV-Dateien laden (PTT_*.wav)
    final wavFiles = dir
        .listSync()
        .whereType<File>()
        .where((f) => f.path.endsWith('.wav') && f.uri.pathSegments.last.startsWith('PTT_'))
        .toList()
      ..sort((a, b) => a.path.compareTo(b.path));

    int idx = 0;
    final segs = <PttSegment>[];
    for (final f in wavFiles) {
      idx++;
      // Uhrzeit aus Dateinamen: PTT_HH-MM-SS_NNN.wav
      DateTime ts = date;
      try {
        final fname = f.uri.pathSegments.last; // PTT_11-09-22_001.wav
        final tp = fname.substring(4, 12).split('-'); // ["11","09","22"]
        ts = DateTime(date.year, date.month, date.day,
            int.parse(tp[0]), int.parse(tp[1]), int.parse(tp[2]));
      } catch (_) {}

      // Dauer aus WAV-Header (bytes 40-43 = data chunk size, 44100 Hz, 16-bit mono)
      Duration dur = Duration.zero;
      try {
        final bytes = await f.readAsBytes();
        if (bytes.length > 44) {
          final dataSize = bytes[40] |
              (bytes[41] << 8) |
              (bytes[42] << 16) |
              (bytes[43] << 24);
          final samples = dataSize ~/ 2; // 16-bit = 2 bytes/sample
          dur = Duration(milliseconds: (samples / 44100 * 1000).round());
        }
      } catch (_) {}

      segs.add(PttSegment(
        index: idx,
        timestamp: ts,
        duration: dur,
        wavFile: f,
      ));
    }

    return _Mission(name: name, dir: dir, date: date, segments: segs);
  }
}

// ─── Archiv-Screen ────────────────────────────────────────────────────────────
class ArchiveScreen extends StatefulWidget {
  const ArchiveScreen({super.key});

  @override
  State<ArchiveScreen> createState() => _ArchiveScreenState();
}

class _ArchiveScreenState extends State<ArchiveScreen> {
  List<_Mission> _missions = [];
  bool _loading = true;
  _Mission? _selected;
  final AudioPlayer _player = AudioPlayer();
  int _playingIndex = -1;

  // HTTP-Server für Browser-Ansicht (optional)
  final TailscaleServer _tsServer = TailscaleServer();
  String? _serverUrl;
  bool _serverRunning = false;

  @override
  void initState() {
    super.initState();
    _loadMissions();
  }

  @override
  void dispose() {
    _player.dispose();
    _tsServer.stop();
    super.dispose();
  }

  // ── Alle Missionsordner laden ──────────────────────────────────────────────
  Future<void> _loadMissions() async {
    setState(() => _loading = true);
    try {
      final base = await getExternalStorageDirectory() ??
          await getApplicationDocumentsDirectory();
      final missionsDir = Directory('${base.path}/hytera_missions');
      if (!missionsDir.existsSync()) {
        setState(() { _missions = []; _loading = false; });
        return;
      }
      final dirs = missionsDir
          .listSync()
          .whereType<Directory>()
          .toList();

      final missions = <_Mission>[];
      for (final d in dirs) {
        missions.add(await _Mission.fromDir(d));
      }
      missions.sort((a, b) => b.date.compareTo(a.date)); // Neueste zuerst

      if (mounted) setState(() { _missions = missions; _loading = false; });
    } catch (e) {
      if (mounted) setState(() => _loading = false);
    }
  }

  // ── Segment abspielen ─────────────────────────────────────────────────────
  Future<void> _play(PttSegment seg) async {
    if (_playingIndex == seg.index) {
      await _player.stop();
      setState(() => _playingIndex = -1);
      return;
    }
    setState(() => _playingIndex = seg.index);
    await _player.stop();
    await _player.play(DeviceFileSource(seg.wavFile.path));
    _player.onPlayerComplete.first.then((_) {
      if (mounted) setState(() => _playingIndex = -1);
    });
  }

  // ── Segment per Taildrop teilen ────────────────────────────────────────────
  Future<void> _share(PttSegment seg) async {
    await SharePlus.instance.share(ShareParams(
      files: [XFile(seg.wavFile.path, mimeType: 'audio/wav')],
      subject: 'PTT ${seg.displayTime}',
    ));
  }

  // ── Ganze Mission per Taildrop teilen ─────────────────────────────────────
  Future<void> _shareAll(_Mission m) async {
    if (m.segments.isEmpty) return;
    final files = m.segments
        .map((s) => XFile(s.wavFile.path, mimeType: 'audio/wav'))
        .toList();
    await SharePlus.instance.share(ShareParams(
      files: files,
      subject: 'Einsatz ${m.name}',
      text: '${files.length} Aufnahmen – ${m.name}',
    ));
  }

  // ── Browser-Server für gewählte Mission starten/stoppen ───────────────────
  Future<void> _toggleServer(_Mission m) async {
    if (_serverRunning) {
      await _tsServer.stop();
      setState(() { _serverRunning = false; _serverUrl = null; });
      return;
    }
    final url = await _tsServer.start(m.dir, m.segments);
    setState(() { _serverRunning = true; _serverUrl = url; });
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(url != null ? '🌐 $url' : 'Server gestartet (keine Tailscale-IP)',
            style: const TextStyle(fontFamily: 'monospace', color: _C.bg)),
        backgroundColor: _C.acc,
        duration: const Duration(seconds: 4),
      ));
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _C.bg,
      appBar: AppBar(
        backgroundColor: _C.panel,
        foregroundColor: _C.acc,
        title: const Text('EINSATZ-ARCHIV',
            style: TextStyle(
                color: _C.acc, fontFamily: 'monospace',
                fontSize: 14, letterSpacing: 3)),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh, color: _C.muted),
            onPressed: _loadMissions,
            tooltip: 'Neu laden',
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: _C.acc))
          : _missions.isEmpty
              ? _buildEmpty()
              : _selected == null
                  ? _buildMissionList()
                  : _buildSegmentView(_selected!),
    );
  }

  Widget _buildEmpty() => Center(
    child: Column(mainAxisSize: MainAxisSize.min, children: [
      Icon(Icons.folder_off, color: _C.muted.withAlpha(80), size: 64),
      const SizedBox(height: 16),
      const Text('Keine Einsätze gespeichert',
          style: TextStyle(color: _C.muted, fontFamily: 'monospace')),
    ]),
  );

  // ── Mission-Liste ─────────────────────────────────────────────────────────
  Widget _buildMissionList() {
    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: 8),
      itemCount: _missions.length,
      itemBuilder: (_, i) {
        final m = _missions[i];
        final d = m.date;
        final dateStr =
            '${d.day.toString().padLeft(2,'0')}.${d.month.toString().padLeft(2,'0')}.${d.year}  '
            '${d.hour.toString().padLeft(2,'0')}:${d.minute.toString().padLeft(2,'0')}';
        return GestureDetector(
          onTap: () => setState(() => _selected = m),
          child: Container(
            margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: _C.panel,
              border: Border.all(color: _C.border),
              borderRadius: BorderRadius.circular(4),
            ),
            child: Row(children: [
              const Icon(Icons.folder_open, color: _C.org, size: 28),
              const SizedBox(width: 14),
              Expanded(child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(m.name,
                      style: const TextStyle(
                          color: _C.acc, fontFamily: 'monospace',
                          fontSize: 12, fontWeight: FontWeight.bold),
                      overflow: TextOverflow.ellipsis),
                  const SizedBox(height: 4),
                  Text('$dateStr  •  ${m.segments.length} Aufnahmen',
                      style: const TextStyle(
                          color: _C.muted, fontFamily: 'monospace', fontSize: 10)),
                ],
              )),
              const Icon(Icons.chevron_right, color: _C.muted),
            ]),
          ),
        );
      },
    );
  }

  // ── Segment-Ansicht für gewählte Mission ──────────────────────────────────
  Widget _buildSegmentView(_Mission m) {
    return Column(children: [
      // ─ Header
      Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        color: _C.panel,
        child: Row(children: [
          GestureDetector(
            onTap: () {
              _tsServer.stop();
              setState(() { _selected = null; _serverRunning = false; _serverUrl = null; _playingIndex = -1; });
            },
            child: const Icon(Icons.arrow_back, color: _C.acc2),
          ),
          const SizedBox(width: 12),
          Expanded(child: Text(m.name,
              style: const TextStyle(color: _C.acc, fontFamily: 'monospace',
                  fontSize: 11, letterSpacing: 1),
              overflow: TextOverflow.ellipsis)),
          // Taildrop alle
          if (m.segments.isNotEmpty)
            GestureDetector(
              onTap: () => _shareAll(m),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                margin: const EdgeInsets.only(right: 8),
                decoration: BoxDecoration(
                  border: Border.all(color: _C.acc.withAlpha(160)),
                  borderRadius: BorderRadius.circular(3),
                ),
                child: const Row(mainAxisSize: MainAxisSize.min, children: [
                  Icon(Icons.send, color: _C.acc, size: 14),
                  SizedBox(width: 4),
                  Text('ALLE', style: TextStyle(
                      color: _C.acc, fontSize: 10, fontFamily: 'monospace',
                      letterSpacing: 1)),
                ]),
              ),
            ),
          // Browser-Server
          GestureDetector(
            onTap: () => _toggleServer(m),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              decoration: BoxDecoration(
                border: Border.all(
                    color: _serverRunning
                        ? _C.acc2
                        : _C.muted.withAlpha(100)),
                borderRadius: BorderRadius.circular(3),
              ),
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Icon(Icons.wifi_tethering,
                    color: _serverRunning ? _C.acc2 : _C.muted, size: 14),
                const SizedBox(width: 4),
                Text(_serverRunning ? 'STOP' : 'SERVER',
                    style: TextStyle(
                        color: _serverRunning ? _C.acc2 : _C.muted,
                        fontSize: 10, fontFamily: 'monospace', letterSpacing: 1)),
              ]),
            ),
          ),
        ]),
      ),
      // ─ Server-URL Banner
      if (_serverUrl != null)
        Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
          color: _C.acc2.withAlpha(15),
          child: Text('🌐 $_serverUrl  •  Browser öffnen',
              style: const TextStyle(color: _C.acc2, fontSize: 10,
                  fontFamily: 'monospace'),
              overflow: TextOverflow.ellipsis),
        ),
      // ─ Segment-Liste
      Expanded(
        child: m.segments.isEmpty
            ? Center(
                child: Text('Keine WAV-Dateien in diesem Ordner',
                    style: const TextStyle(color: _C.muted,
                        fontFamily: 'monospace')))
            : ListView.builder(
                padding: const EdgeInsets.symmetric(vertical: 8),
                itemCount: m.segments.length,
                itemBuilder: (_, i) => _buildSegTile(m.segments[i]),
              ),
      ),
    ]);
  }

  Widget _buildSegTile(PttSegment seg) {
    final isPlaying = _playingIndex == seg.index;
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 3),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: isPlaying ? _C.acc2.withAlpha(12) : _C.panel,
        border: Border.all(
            color: isPlaying ? _C.acc2.withAlpha(120) : _C.border),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Row(children: [
        // Index
        Container(
          width: 32, height: 32,
          alignment: Alignment.center,
          decoration: BoxDecoration(
              border: Border.all(color: _C.acc2.withAlpha(80)),
              borderRadius: BorderRadius.circular(2)),
          child: Text('${seg.index}',
              style: const TextStyle(color: _C.acc2, fontSize: 11,
                  fontFamily: 'monospace')),
        ),
        const SizedBox(width: 12),
        // Zeit + Dauer
        Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(seg.displayTime,
              style: const TextStyle(color: _C.tx, fontSize: 13,
                  fontFamily: 'monospace', fontWeight: FontWeight.bold)),
          Text(seg.displayDuration,
              style: const TextStyle(color: _C.muted, fontSize: 10,
                  fontFamily: 'monospace')),
        ]),
        const Spacer(),
        // Dateiname
        Flexible(
          child: Text(seg.filename,
              style: const TextStyle(color: _C.muted, fontSize: 9,
                  fontFamily: 'monospace'),
              overflow: TextOverflow.ellipsis),
        ),
        const SizedBox(width: 8),
        // Play
        GestureDetector(
          onTap: () => _play(seg),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 150),
            width: 36, height: 36,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: isPlaying ? _C.acc2.withAlpha(30) : _C.grn.withAlpha(20),
              border: Border.all(
                  color: isPlaying ? _C.acc2 : _C.grn.withAlpha(160)),
              borderRadius: BorderRadius.circular(4),
            ),
            child: Icon(isPlaying ? Icons.stop : Icons.play_arrow,
                color: isPlaying ? _C.acc2 : _C.grn, size: 20),
          ),
        ),
        const SizedBox(width: 6),
        // Taildrop
        GestureDetector(
          onTap: () => _share(seg),
          child: Container(
            width: 36, height: 36,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: _C.acc.withAlpha(18),
              border: Border.all(color: _C.acc.withAlpha(140)),
              borderRadius: BorderRadius.circular(4),
            ),
            child: const Icon(Icons.send, color: _C.acc, size: 18),
          ),
        ),
      ]),
    );
  }
}
