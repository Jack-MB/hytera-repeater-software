import 'dart:async';
import 'dart:io';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';
import 'package:share_plus/share_plus.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import 'archive_screen.dart';
import 'ptt_detector.dart';
import 'segment_model.dart';
import 'tailscale_server.dart';
import 'wav_writer.dart';

void main() {
  runApp(const HyteraRecorderApp());
}

// ─────────────────────────────────────────────────────────────────────────────
// Design-Tokens (Cyberpunk / Night City)
// ─────────────────────────────────────────────────────────────────────────────
class C {
  static const bg = Color(0xFF0a0a0f);
  static const panel = Color(0xFF0d0d1a);
  static const panelBorder = Color(0xFF1a1a2e);
  static const acc = Color(0xFFfce300); // Night City Yellow
  static const acc2 = Color(0xFF00fff7); // Cyan
  static const red = Color(0xFFff003c);
  static const grn = Color(0xFF39ff14);
  static const org = Color(0xFFff6b00);
  static const textPrimary = Color(0xFFe0e0e0);
  static const textMuted = Color(0xFF666680);
}

// ─────────────────────────────────────────────────────────────────────────────
// App Root
// ─────────────────────────────────────────────────────────────────────────────
class HyteraRecorderApp extends StatelessWidget {
  const HyteraRecorderApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Hytera USB Recorder',
      debugShowCheckedModeBanner: false,
      theme: ThemeData.dark().copyWith(
        scaffoldBackgroundColor: C.bg,
        colorScheme: const ColorScheme.dark(
          primary: C.acc,
          secondary: C.acc2,
          surface: C.panel,
        ),
        textTheme: ThemeData.dark().textTheme.apply(
              fontFamily: 'monospace',
              bodyColor: C.textPrimary,
            ),
      ),
      home: const RecorderHome(),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Screen
// ─────────────────────────────────────────────────────────────────────────────
class RecorderHome extends StatefulWidget {
  const RecorderHome({super.key});

  @override
  State<RecorderHome> createState() => _RecorderHomeState();
}

class _RecorderHomeState extends State<RecorderHome>
    with TickerProviderStateMixin {
  // ── State
  bool _missionActive = false;
  bool _pttActive = false;
  bool _recording = false;
  String _missionName = '';
  Directory? _missionDir;
  int _segmentCount = 0;
  final List<PttSegment> _segments = [];

  // ── Audio
  final AudioRecorder _recorder = AudioRecorder();
  StreamSubscription<Amplitude>? _ampSub;
  PttDetector? _pttDetector;
  double _currentAmp = 0.0;
  double _threshold = 0.015;
  int _silenceMs = 1500;

  // ── PTT Byte-Offset (Segment-Extraktion aus Master-Datei)
  int _pttStartOffset = 0;
  Timer? _flushTimer;

  // ── Tailscale HTTP-Server
  final TailscaleServer _tsServer = TailscaleServer();
  String? _tsUrl;

  // ── Audio-Playback (Segment-Abspielen)
  final AudioPlayer _player = AudioPlayer();
  int _playingIndex = -1;

  // ── USB-Audio-Gerät (Retevis RT81 über USB-Soundkarte)
  List<InputDevice> _inputDevices = [];
  InputDevice? _selectedDevice; // null = System-Standard (internes Mikro)
  bool _loadingDevices = false;

  // ── Animation
  late final AnimationController _pulseCtrl;
  late final Animation<double> _pulseAnim;

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 600),
    )..repeat(reverse: true);
    _pulseAnim = Tween<double>(begin: 0.7, end: 1.0).animate(
      CurvedAnimation(parent: _pulseCtrl, curve: Curves.easeInOut),
    );
    // Verfügbare Audio-Eingabegeräte laden (USB-Soundkarte erkennen)
    _loadInputDevices();
  }

  @override
  void dispose() {
    _pulseCtrl.dispose();
    _ampSub?.cancel();
    _flushTimer?.cancel();
    _recorder.dispose();
    _player.dispose();
    _tsServer.stop();
    super.dispose();
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Mission-Verwaltung
  // ──────────────────────────────────────────────────────────────────────────
  Future<void> _startMission() async {
    // Berechtigungen
    final micOk = await Permission.microphone.request();
    if (!micOk.isGranted) {
      _showSnack('Mikrofon-Berechtigung verweigert', isError: true);
      return;
    }

    // Ordner anlegen
    final baseDir = await getExternalStorageDirectory() ??
        await getApplicationDocumentsDirectory();
    final ts = DateTime.now();
    final name =
        'Einsatz_${ts.year}-${_pad(ts.month)}-${_pad(ts.day)}_${_pad(ts.hour)}-${_pad(ts.minute)}';
    _missionDir = Directory('${baseDir.path}/hytera_missions/$name');
    await _missionDir!.create(recursive: true);

    setState(() {
      _missionName = name;
      _missionActive = true;
      _segmentCount = 0;
      _segments.clear();
    });

    WakelockPlus.enable();
    await _startAudioStream();

    // Tailscale HTTP-Server starten
    final url = await _tsServer.start(_missionDir!, _segments);
    if (mounted) {
      setState(() => _tsUrl = url);
      _showSnack(url != null ? 'Server: $url' : 'Einsatz gestartet: $name');
    }
  }

  Future<void> _stopMission() async {
    await _stopAudioStream();
    await _tsServer.stop();
    WakelockPlus.disable();
    setState(() {
      _missionActive = false;
      _pttActive = false;
      _tsUrl = null;
    });
    _showSnack('Einsatz beendet – $_segmentCount Segmente gespeichert');
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Audio-Playback (Segment abspielen / stoppen)
  // ──────────────────────────────────────────────────────────────────────────
  Future<void> _playSegment(PttSegment seg) async {
    if (_playingIndex == seg.index) {
      // Gleiches Segment: stoppen
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

  // ──────────────────────────────────────────────────────────────────────────
  // Geräte-Erkennung (RT81 via USB-Soundkarte)
  // ──────────────────────────────────────────────────────────────────────────
  Future<void> _loadInputDevices() async {
    setState(() => _loadingDevices = true);
    try {
      final devices = await _recorder.listInputDevices();
      InputDevice? autoSelect;
      // USB-Gerät automatisch erkennen:
      // Android nennt USB-Audio-Class-Geräte meist "USB Audio" oder "USB-Audiokarte"
      // oder den Herstellernamen der Soundkarte
      for (final d in devices) {
        final label = d.label.toLowerCase();
        if (label.contains('usb') ||
            label.contains('externe') ||
            label.contains('headset') && label.contains('usb')) {
          autoSelect = d;
          break;
        }
      }
      if (mounted) {
        setState(() {
          _inputDevices = devices;
          // Nur auto-selektieren wenn noch kein Gerät gewählt
          if (_selectedDevice == null && autoSelect != null) {
            _selectedDevice = autoSelect;
          }
          _loadingDevices = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loadingDevices = false);
    }
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Audio-Stream (USB-Soundkarte → RT81 Audioquelle → Master-PCM-Datei)
  // ──────────────────────────────────────────────────────────────────────────
  Future<void> _startAudioStream() async {
    if (_recording) return;

    _pttDetector = PttDetector(
      threshold: _threshold,
      silenceMs: _silenceMs,
      onPttStart: _onPttStart,
      onPttEnd: _onPttEnd,
    );

    // Amplitude-Callback: VU-Meter + PTT-Erkennung
    _ampSub = _recorder
        .onAmplitudeChanged(const Duration(milliseconds: 80))
        .listen((amp) {
      // dBFS (-160…0) → 0.0–1.0
      final normalized = ((amp.current + 60) / 60).clamp(0.0, 1.0);
      if (mounted) setState(() => _currentAmp = normalized);
      _pttDetector?.processAmplitude(normalized);
    });

    // Master-Aufnahme: kontinuierliche PCM-16-Datei
    // Gerät: USB-Soundkarte (Retevis RT81 Lautsprecher/Ohrhörer-Ausgang)
    // Falls kein USB-Gerät gewählt: internes Mikrofon (Fallback)
    final masterPath = '${_missionDir!.path}/master.pcm';
    await _recorder.start(
      RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: 44100,
        numChannels: 1,
        autoGain: false,      // WICHTIG: kein Auto-Gain bei Funk-Audio
        echoCancel: false,    // Kein Echo-Cancel (kein Mikro-Einsatz)
        noiseSuppress: false, // Kein Rauschunterdrücken (DMR ist digitales Audio)
        device: _selectedDevice, // USB-Soundkarte oder null = internes Mikro
      ),
      path: masterPath,
    );

    setState(() => _recording = true);
    // Warnung falls kein USB-Gerät gewählt
    if (_selectedDevice == null && mounted) {
      _showSnack('⚠ Kein USB-Gerät gewählt – internes Mikro aktiv!', isError: true);
    }
  }

  Future<void> _stopAudioStream() async {
    _ampSub?.cancel();
    await _recorder.stop();
    setState(() => _recording = false);
    _pttDetector?.reset();
    if (_pttActive) setState(() => _pttActive = false);
  }

  // ──────────────────────────────────────────────────────────────────────────
  // PTT-Events (Byte-Offset-Strategie auf Master-Datei)
  // ──────────────────────────────────────────────────────────────────────────
  void _onPttStart() {
    if (!mounted || _missionDir == null) return;
    setState(() => _pttActive = true);
    // Aktuellen Schreibkopf in master.pcm merken
    final masterFile = File('${_missionDir!.path}/master.pcm');
    _pttStartOffset = masterFile.existsSync() ? masterFile.lengthSync() : 0;
  }

  void _onPttEnd(Uint8List _, Duration duration) async {
    if (!mounted || _missionDir == null) return;
    setState(() => _pttActive = false);

    // Warte kurz – Record-Prozess schreibt async in Datei
    await Future.delayed(const Duration(milliseconds: 300));

    final masterFile = File('${_missionDir!.path}/master.pcm');
    if (!masterFile.existsSync()) return;

    final masterLen = masterFile.lengthSync();
    final byteCount = masterLen - _pttStartOffset;
    if (byteCount < 100) return; // zu kurz, ignorieren

    _segmentCount++;
    final idx = _segmentCount;
    final ts = DateTime.now();
    final fname =
        'PTT_${_pad(ts.hour)}-${_pad(ts.minute)}-${_pad(ts.second)}_${idx.toString().padLeft(3, '0')}.wav';
    final wavFile = File('${_missionDir!.path}/$fname');

    // Segment aus Master ausschneiden
    final raf = await masterFile.open(mode: FileMode.read);
    await raf.setPosition(_pttStartOffset);
    final pcmBytes = await raf.read(byteCount);
    await raf.close();

    await WavWriter.write(wavFile, Uint8List.fromList(pcmBytes), sampleRate: 44100);

    final seg = PttSegment(
      index: idx,
      timestamp: ts,
      duration: duration,
      wavFile: wavFile,
    );

    if (mounted) {
      setState(() {
        _segments.insert(0, seg);
        if (_segments.length > 100) _segments.removeLast();
      });
      // Tailscale-Server mit neuer Liste aktualisieren
      _tsServer.updateSegments(_segments);
    }
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Helpers
  // ──────────────────────────────────────────────────────────────────────────
  String _pad(int v) => v.toString().padLeft(2, '0');

  void _showSnack(String msg, {bool isError = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(msg,
          style: TextStyle(color: isError ? C.red : C.bg, fontFamily: 'monospace')),
      backgroundColor: isError ? C.red.withAlpha(30) : C.acc,
      duration: const Duration(seconds: 3),
    ));
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Build
  // ──────────────────────────────────────────────────────────────────────────
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: C.bg,
      body: SafeArea(
        child: Column(
          children: [
            _buildHeader(),
            _buildStatusBar(),
            _buildVuMeter(),
            _buildSettings(),
            const Divider(color: C.panelBorder, height: 1),
            Expanded(child: _buildSegmentList()),
            _buildBottomBar(),
          ],
        ),
      ),
    );
  }

  // ── Header
  Widget _buildHeader() {
    return Column(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          decoration: BoxDecoration(
            color: C.panel,
            border: Border(bottom: BorderSide(
                color: _tsUrl != null ? C.acc2.withAlpha(60) : C.panelBorder)),
          ),
          child: Row(
            children: [
              const Icon(Icons.radio, color: C.acc, size: 28),
              const SizedBox(width: 12),
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('HYTERA USB RECORDER',
                      style: TextStyle(
                          color: C.acc,
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                          letterSpacing: 2,
                          fontFamily: 'monospace')),
                  Text(
                    _missionActive ? '▶ $_missionName' : '■ BEREIT',
                    style: const TextStyle(
                        color: C.textMuted, fontSize: 10, fontFamily: 'monospace'),
                  ),
                ],
              ),
              const Spacer(),
              // Archiv-Button
              GestureDetector(
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const ArchiveScreen()),
                ),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                  margin: const EdgeInsets.only(right: 8),
                  decoration: BoxDecoration(
                    border: Border.all(color: C.org.withAlpha(160)),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: const Row(mainAxisSize: MainAxisSize.min, children: [
                    Icon(Icons.folder_open, color: C.org, size: 14),
                    SizedBox(width: 4),
                    Text('ARCHIV', style: TextStyle(
                        color: C.org, fontSize: 10, fontFamily: 'monospace',
                        letterSpacing: 1, fontWeight: FontWeight.bold)),
                  ]),
                ),
              ),
              if (_missionActive)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  decoration: BoxDecoration(
                    border: Border.all(color: C.acc2),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    '$_segmentCount SEG',
                    style: const TextStyle(
                        color: C.acc2,
                        fontSize: 12,
                        fontFamily: 'monospace',
                        fontWeight: FontWeight.bold),
                  ),
                ),
            ],
          ),
        ),
        // ─ Tailscale URL Banner
        if (_tsUrl != null)
          GestureDetector(
            onTap: () {
              Clipboard.setData(ClipboardData(text: _tsUrl!));
              _showSnack('URL kopiert: $_tsUrl');
            },
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 7),
              color: C.acc2.withAlpha(15),
              child: Row(
                children: [
                  const Icon(Icons.wifi_tethering, color: C.acc2, size: 14),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      '$_tsUrl  •  tippen zum Kopieren',
                      style: const TextStyle(
                          color: C.acc2,
                          fontSize: 10,
                          fontFamily: 'monospace',
                          letterSpacing: 1),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const Icon(Icons.copy, color: C.acc2, size: 12),
                ],
              ),
            ),
          ),
      ],
    );
  }

  // ── Status-Bar (PTT-Indikator)
  Widget _buildStatusBar() {
    return AnimatedBuilder(
      animation: _pulseAnim,
      builder: (context, _) {
        final Color statusColor;
        final String statusText;
        final Color bgColor;

        if (!_missionActive) {
          statusColor = C.textMuted;
          statusText = '● KEIN EINSATZ';
          bgColor = C.panel;
        } else if (_pttActive) {
          statusColor = C.red;
          statusText = '● PTT AKTIV – AUFNAHME';
          bgColor = C.red.withAlpha((0.08 * 255).round());
        } else {
          statusColor = C.grn;
          statusText = '● BEREIT – WARTE AUF FUNK';
          bgColor = C.grn.withAlpha((0.05 * 255).round());
        }

        return AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          width: double.infinity,
          padding: const EdgeInsets.symmetric(vertical: 14),
          color: bgColor,
          child: Center(
            child: Opacity(
              opacity: _pttActive ? _pulseAnim.value : 1.0,
              child: Text(
                statusText,
                style: TextStyle(
                  color: statusColor,
                  fontSize: 14,
                  fontWeight: FontWeight.bold,
                  letterSpacing: 3,
                  fontFamily: 'monospace',
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  // ── VU-Meter
  Widget _buildVuMeter() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      color: C.panel,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Text('VU  ', style: TextStyle(color: C.textMuted, fontSize: 11, fontFamily: 'monospace')),
              Expanded(
                child: LayoutBuilder(builder: (ctx, box) {
                  final w = box.maxWidth;
                  final fillW = (w * _currentAmp).clamp(0.0, w);
                  final threshW = (w * _threshold * 5).clamp(0.0, w); // visual scale
                  return Stack(
                    children: [
                      Container(height: 18, color: C.panelBorder),
                      AnimatedContainer(
                        duration: const Duration(milliseconds: 50),
                        height: 18,
                        width: fillW,
                        decoration: BoxDecoration(
                          gradient: LinearGradient(colors: [
                            C.grn,
                            _currentAmp > 0.7 ? C.org : C.grn,
                            _currentAmp > 0.9 ? C.red : C.org,
                          ]),
                        ),
                      ),
                      // Threshold-Marker
                      Positioned(
                        left: threshW,
                        top: 0,
                        bottom: 0,
                        child: Container(width: 2, color: C.acc),
                      ),
                    ],
                  );
                }),
              ),
              const SizedBox(width: 8),
              Text(
                '${(_currentAmp * 100).round()}%',
                style: const TextStyle(color: C.acc2, fontSize: 11, fontFamily: 'monospace'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  // ── Einstellungen (Geräteauswahl + Schwellwert + Stille-Timer)
  Widget _buildSettings() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      color: C.panel,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ─ USB-Geräte-Auswahl
          Row(
            children: [
              const Icon(Icons.usb, color: C.org, size: 14),
              const SizedBox(width: 6),
              const Text('USB-EING.',
                  style: TextStyle(
                      color: C.org,
                      fontSize: 10,
                      letterSpacing: 2,
                      fontFamily: 'monospace')),
              const SizedBox(width: 8),
              Expanded(
                child: _loadingDevices
                    ? const Text('Suche Geräte...',
                        style: TextStyle(
                            color: C.textMuted,
                            fontSize: 10,
                            fontFamily: 'monospace'))
                    : DropdownButton<InputDevice?>(
                        value: _selectedDevice,
                        isExpanded: true,
                        dropdownColor: C.panel,
                        underline: Container(
                            height: 1,
                            color: _selectedDevice != null
                                ? C.org.withAlpha(120)
                                : C.panelBorder),
                        style: const TextStyle(
                            color: C.textPrimary,
                            fontSize: 11,
                            fontFamily: 'monospace'),
                        hint: const Text('System-Standard (internes Mikro)',
                            style: TextStyle(
                                color: C.textMuted,
                                fontSize: 10,
                                fontFamily: 'monospace')),
                        items: [
                          const DropdownMenuItem<InputDevice?>(
                            value: null,
                            child: Text('System-Standard',
                                style: TextStyle(
                                    color: C.textMuted,
                                    fontSize: 10,
                                    fontFamily: 'monospace')),
                          ),
                          ..._inputDevices.map((d) {
                            final isUsb =
                                d.label.toLowerCase().contains('usb');
                            return DropdownMenuItem<InputDevice?>(
                              value: d,
                              child: Row(
                                children: [
                                  if (isUsb)
                                    const Icon(Icons.usb,
                                        color: C.org, size: 12),
                                  if (isUsb) const SizedBox(width: 4),
                                  Flexible(
                                    child: Text(
                                      d.label,
                                      style: TextStyle(
                                          color: isUsb ? C.org : C.textPrimary,
                                          fontSize: 10,
                                          fontFamily: 'monospace'),
                                      overflow: TextOverflow.ellipsis,
                                    ),
                                  ),
                                ],
                              ),
                            );
                          }),
                        ],
                        onChanged: _missionActive
                            ? null
                            : (v) => setState(() => _selectedDevice = v),
                      ),
              ),
              // Refresh-Button
              IconButton(
                icon: const Icon(Icons.refresh, color: C.textMuted, size: 16),
                onPressed:
                    _missionActive ? null : () => _loadInputDevices(),
                tooltip: 'Geräteliste neu laden',
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
              ),
            ],
          ),
          // Gerät-Status-Zeile
          if (_selectedDevice != null)
            Padding(
              padding: const EdgeInsets.only(left: 20, bottom: 4),
              child: Text(
                '✓ ${_selectedDevice!.label}',
                style: const TextStyle(
                    color: C.org,
                    fontSize: 9,
                    fontFamily: 'monospace'),
                overflow: TextOverflow.ellipsis,
              ),
            )
          else
            const Padding(
              padding: EdgeInsets.only(left: 20, bottom: 4),
              child: Text(
                '⚠ RT81 USB-Soundkarte wählen!',
                style: TextStyle(
                    color: C.org, fontSize: 9, fontFamily: 'monospace'),
              ),
            ),
          const Divider(color: C.panelBorder, height: 12),
          // ─ Schwellwert
          Row(
            children: [
              const Icon(Icons.tune, color: C.textMuted, size: 14),
              const SizedBox(width: 6),
              const Text('SCHWELLWERT',
                  style: TextStyle(
                      color: C.textMuted,
                      fontSize: 10,
                      letterSpacing: 2,
                      fontFamily: 'monospace')),
              Expanded(
                child: Slider(
                  value: _threshold,
                  min: 0.005,
                  max: 0.1,
                  divisions: 19,
                  activeColor: C.acc,
                  inactiveColor: C.panelBorder,
                  onChanged: _missionActive
                      ? null
                      : (v) {
                          setState(() => _threshold = v);
                        },
                ),
              ),
              Text(
                '${(_threshold * 100).toStringAsFixed(1)}%',
                style: const TextStyle(
                    color: C.acc, fontSize: 11, fontFamily: 'monospace'),
              ),
            ],
          ),
          // ─ Stille-Timer
          Row(
            children: [
              const Icon(Icons.timer, color: C.textMuted, size: 14),
              const SizedBox(width: 6),
              const Text('STILLE-TIMER',
                  style: TextStyle(
                      color: C.textMuted,
                      fontSize: 10,
                      letterSpacing: 2,
                      fontFamily: 'monospace')),
              Expanded(
                child: Slider(
                  value: _silenceMs.toDouble(),
                  min: 500,
                  max: 4000,
                  divisions: 7,
                  activeColor: C.acc2,
                  inactiveColor: C.panelBorder,
                  onChanged: _missionActive
                      ? null
                      : (v) {
                          setState(() => _silenceMs = v.round());
                        },
                ),
              ),
              Text(
                '${(_silenceMs / 1000).toStringAsFixed(1)}s',
                style: const TextStyle(
                    color: C.acc2, fontSize: 11, fontFamily: 'monospace'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  // ── Segment-Liste
  Widget _buildSegmentList() {
    if (_segments.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.mic_none, color: C.textMuted.withAlpha(80), size: 64),
            const SizedBox(height: 16),
            Text(
              _missionActive
                  ? 'Warte auf Funkkontakt...'
                  : 'Einsatz starten zum Aufnehmen',
              style: const TextStyle(color: C.textMuted, fontFamily: 'monospace'),
            ),
          ],
        ),
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: 8),
      itemCount: _segments.length,
      itemBuilder: (ctx, i) => _buildSegmentTile(_segments[i]),
    );
  }

  Widget _buildSegmentTile(PttSegment seg) {
    final isPlaying = _playingIndex == seg.index;
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 3),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: isPlaying ? C.acc2.withAlpha(12) : C.panel,
        border: Border.all(
          color: isPlaying
              ? C.acc2.withAlpha(120)
              : seg.index == _segmentCount
                  ? C.acc.withAlpha(80)
                  : C.panelBorder,
          width: isPlaying ? 1.5 : 1.0,
        ),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Row(
        children: [
          // Index
          Container(
            width: 32,
            height: 32,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              border: Border.all(color: C.acc2.withAlpha(80)),
              borderRadius: BorderRadius.circular(2),
            ),
            child: Text(
              '${seg.index}',
              style: const TextStyle(color: C.acc2, fontSize: 11, fontFamily: 'monospace'),
            ),
          ),
          const SizedBox(width: 12),
          // Zeit + Dauer
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(seg.displayTime,
                  style: const TextStyle(
                      color: C.textPrimary,
                      fontSize: 13,
                      fontFamily: 'monospace',
                      fontWeight: FontWeight.bold)),
              Text(seg.displayDuration,
                  style: const TextStyle(
                      color: C.textMuted, fontSize: 10, fontFamily: 'monospace')),
            ],
          ),
          const Spacer(),
          // Dateiname (kurz)
          Flexible(
            child: Text(
              seg.filename,
              style: const TextStyle(
                  color: C.textMuted, fontSize: 9, fontFamily: 'monospace'),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          const SizedBox(width: 10),
          // ▶ Abspiel-Button
          GestureDetector(
            onTap: () => _playSegment(seg),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 150),
              width: 36,
              height: 36,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: isPlaying
                    ? C.acc2.withAlpha(30)
                    : C.grn.withAlpha(20),
                border: Border.all(
                    color: isPlaying ? C.acc2 : C.grn.withAlpha(160)),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Icon(
                isPlaying ? Icons.stop : Icons.play_arrow,
                color: isPlaying ? C.acc2 : C.grn,
                size: 20,
              ),
            ),
          ),
          const SizedBox(width: 6),
          // 📤 Taildrop-Share-Button
          GestureDetector(
            onTap: () => _shareSegment(seg),
            child: Container(
              width: 36,
              height: 36,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: C.acc.withAlpha(18),
                border: Border.all(color: C.acc.withAlpha(140)),
                borderRadius: BorderRadius.circular(4),
              ),
              child: const Icon(Icons.send, color: C.acc, size: 18),
            ),
          ),
        ],
      ),
    );
  }

  // ── Taildrop: einzelnes Segment teilen
  Future<void> _shareSegment(PttSegment seg) async {
    try {
      await SharePlus.instance.share(
        ShareParams(
          files: [XFile(seg.wavFile.path, mimeType: 'audio/wav')],
          subject: 'Hytera PTT ${seg.displayTime}',
          text: 'PTT-Aufnahme ${seg.displayTime} (${seg.displayDuration})',
        ),
      );
    } catch (e) {
      _showSnack('Fehler beim Teilen: $e', isError: true);
    }
  }

  // ── Taildrop: alle Segmente teilen
  Future<void> _shareAllSegments() async {
    if (_segments.isEmpty) {
      _showSnack('Keine Segmente vorhanden', isError: true);
      return;
    }
    try {
      final files = _segments
          .map((s) => XFile(s.wavFile.path, mimeType: 'audio/wav'))
          .toList();
      await SharePlus.instance.share(
        ShareParams(
          files: files,
          subject: 'Hytera Einsatz $_missionName',
          text: '${files.length} PTT-Aufnahmen aus Einsatz $_missionName',
        ),
      );
    } catch (e) {
      _showSnack('Fehler beim Teilen: $e', isError: true);
    }
  }

  // ── Bottom Bar (Start/Stop)
  Widget _buildBottomBar() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: const BoxDecoration(
        color: C.panel,
        border: Border(top: BorderSide(color: C.panelBorder)),
      ),
      child: Row(
        children: [
          // Info
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                _missionActive ? 'EINSATZ AKTIV' : 'BEREIT',
                style: TextStyle(
                    color: _missionActive ? C.acc : C.textMuted,
                    fontSize: 10,
                    fontFamily: 'monospace',
                    letterSpacing: 2),
              ),
              Text(
                _missionActive
                    ? '$_segmentCount Aufnahmen'
                    : 'USB-Audio anschließen',
                style: const TextStyle(
                    color: C.textMuted, fontSize: 9, fontFamily: 'monospace'),
              ),
            ],
          ),
          const Spacer(),
          // 📤 Alle teilen (Taildrop)
          if (_missionActive && _segments.isNotEmpty)
            GestureDetector(
              onTap: _shareAllSegments,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
                margin: const EdgeInsets.only(right: 10),
                decoration: BoxDecoration(
                  color: C.acc.withAlpha(15),
                  border: Border.all(color: C.acc.withAlpha(160), width: 1.5),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: const Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.send, color: C.acc, size: 16),
                    SizedBox(width: 6),
                    Text('TAILDROP',
                        style: TextStyle(
                            color: C.acc,
                            fontSize: 11,
                            fontWeight: FontWeight.bold,
                            letterSpacing: 1.5,
                            fontFamily: 'monospace')),
                  ],
                ),
              ),
            ),
          // Start / Stop Button
          GestureDetector(
            onTap: _missionActive ? _stopMission : _startMission,
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 14),
              decoration: BoxDecoration(
                color: _missionActive
                    ? C.red.withAlpha((0.15 * 255).round())
                    : C.acc.withAlpha((0.15 * 255).round()),
                border: Border.all(
                  color: _missionActive ? C.red : C.acc,
                  width: 1.5,
                ),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    _missionActive ? Icons.stop : Icons.fiber_manual_record,
                    color: _missionActive ? C.red : C.acc,
                    size: 18,
                  ),
                  const SizedBox(width: 10),
                  Text(
                    _missionActive ? 'EINSATZ BEENDEN' : 'EINSATZ STARTEN',
                    style: TextStyle(
                      color: _missionActive ? C.red : C.acc,
                      fontSize: 13,
                      fontWeight: FontWeight.bold,
                      letterSpacing: 2,
                      fontFamily: 'monospace',
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
