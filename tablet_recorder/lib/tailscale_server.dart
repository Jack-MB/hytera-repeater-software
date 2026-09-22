// tailscale_server.dart
// Leichtgewichtiger HTTP-Server (shelf) der WAV-Segmente über das Netz bereitstellt.
// Erreichbar via Tailscale-IP (100.x.x.x) vom PC-Dashboard aus.

import 'dart:convert';
import 'dart:io';

import 'package:shelf/shelf.dart';
import 'package:shelf/shelf_io.dart' as shelf_io;

import 'segment_model.dart';

class TailscaleServer {
  static const int port = 19081;

  HttpServer? _server;
  String? _tailscaleIp;
  Directory? _missionDir;
  List<PttSegment> _segments = [];

  // ─────────────────────────────────────────────────────────────────────────
  // Öffentliche API
  // ─────────────────────────────────────────────────────────────────────────

  /// Startet den HTTP-Server. Gibt die Tailscale-URL zurück oder null wenn
  /// kein Tailscale-Interface gefunden wurde.
  Future<String?> start(Directory missionDir, List<PttSegment> segments) async {
    _missionDir = missionDir;
    _segments = segments;

    _tailscaleIp = await _detectTailscaleIp();

    final handler = const Pipeline()
        .addMiddleware(_corsMiddleware())
        .addHandler(_router);

    _server = await shelf_io.serve(handler, InternetAddress.anyIPv4, port);

    if (_tailscaleIp != null) {
      return 'http://$_tailscaleIp:$port';
    }
    // Fallback: lokale IP
    final localIp = await _getLocalIp();
    return localIp != null ? 'http://$localIp:$port' : null;
  }

  Future<void> stop() async {
    await _server?.close(force: true);
    _server = null;
  }

  void updateSegments(List<PttSegment> segments) {
    _segments = segments;
  }

  bool get isRunning => _server != null;
  String? get tailscaleIp => _tailscaleIp;

  // ─────────────────────────────────────────────────────────────────────────
  // Router
  // ─────────────────────────────────────────────────────────────────────────
  Future<Response> _router(Request req) async {
    final path = req.url.path;

    if (path == '' || path == '/') return _handleIndex(req);
    if (path == 'segments') return _handleSegmentList(req);
    if (path.startsWith('audio/')) return _handleAudio(req, path);

    return Response.notFound('Not found');
  }

  /// GET `/`  → Zeitstrahl-Dashboard der Aufnahmen
  Response _handleIndex(Request req) {
    final missionName = _missionDir?.uri.pathSegments
        .where((s) => s.isNotEmpty)
        .last ?? 'Einsatz';

    // JSON-Daten für den Zeitstrahl
    final segsJson = _segments.reversed.map((s) => '''{"index":${s.index},"time":"${s.displayTime}","dur":"${s.displayDuration}","dur_s":${s.duration.inMilliseconds/1000.0},"filename":"${s.filename}"}''').join(',');

    final html = '''<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HYTERA – $missionName</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:#0a0a0f;color:#e0e0e0;font-family:'Share Tech Mono','Consolas',monospace;display:flex;flex-direction:column;height:100vh;overflow:hidden;
      background-image:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(252,227,0,.012) 2px,rgba(252,227,0,.012) 4px);}
    header{background:#0d0d1a;border-bottom:2px solid #fce300;padding:10px 20px;display:flex;align-items:center;gap:12px;flex-shrink:0;
      box-shadow:0 0 20px rgba(252,227,0,.2);}
    header h1{color:#fce300;font-size:14px;letter-spacing:3px;text-shadow:0 0 14px rgba(252,227,0,.8);flex:1}
    .badge{border:1px solid #00fff7;color:#00fff7;padding:3px 10px;font-size:11px;border-radius:3px}
    .dot{width:10px;height:10px;border-radius:50%;background:#39ff14;box-shadow:0 0 8px #39ff14}
    canvas{display:block;cursor:crosshair;flex-shrink:0}
    #tl-wrap{overflow-x:auto;overflow-y:hidden;flex-shrink:0;background:#0a0a0f;border-bottom:1px solid #1a1a2e;}
    #main{flex:1;overflow:hidden;display:flex;flex-direction:column;}
    #list{flex:1;overflow-y:auto;}
    .row{display:flex;align-items:center;gap:10px;padding:8px 14px;border-bottom:1px solid #1a1a2e;cursor:pointer;transition:.1s}
    .row:hover{background:#0d0d1a}
    .row.playing{background:rgba(0,255,247,.06);border-left:3px solid #00fff7}
    .idx{color:#00fff7;font-size:11px;min-width:32px;text-align:center;border:1px solid rgba(0,255,247,.4);padding:3px;border-radius:2px}
    .ts{color:#e0e0e0;font-size:13px;font-weight:bold;min-width:70px}
    .dur{color:#666680;font-size:10px;min-width:50px}
    .bar-wrap{flex:1;height:20px;background:#1a1a2e;border-radius:3px;overflow:hidden;position:relative}
    .bar{height:100%;background:linear-gradient(90deg,#fce300,#ff6b00);border-radius:3px;transition:width .3s}
    .btn-play{background:rgba(57,255,20,.1);border:1px solid rgba(57,255,20,.6);color:#39ff14;padding:5px 10px;cursor:pointer;font-family:monospace;font-size:11px;border-radius:3px}
    .btn-play:hover{background:rgba(57,255,20,.2)}
    .btn-dl{background:rgba(0,255,247,.1);border:1px solid rgba(0,255,247,.4);color:#00fff7;padding:5px 10px;cursor:pointer;font-family:monospace;font-size:11px;border-radius:3px;text-decoration:none}
    .toolbar{background:#0d0d1a;padding:6px 14px;display:flex;gap:8px;align-items:center;border-bottom:1px solid #1a1a2e;flex-shrink:0}
    .tbtn{background:transparent;border:1px solid #fce300;color:#fce300;padding:3px 10px;font-family:monospace;font-size:10px;cursor:pointer;letter-spacing:1px}
    .tbtn:hover{background:rgba(252,227,0,.15)}
    #player-bar{background:#050508;border-top:1px solid #1a1a2e;padding:8px 14px;display:flex;align-items:center;gap:12px;flex-shrink:0}
    #player-bar audio{flex:1;height:30px;accent-color:#fce300}
    #player-label{color:#666680;font-size:10px;min-width:120px}
    ::-webkit-scrollbar{width:4px;height:4px}
    ::-webkit-scrollbar-track{background:#050508}
    ::-webkit-scrollbar-thumb{background:rgba(252,227,0,.25)}
  </style>
</head>
<body>
<header>
  <span style="font-size:20px">📻</span>
  <h1>HYTERA USB RECORDER · $missionName</h1>
  <div class="dot" id="dot" title="Live"></div>
  <div class="badge" id="badge">${_segments.length} SEG</div>
</header>

<div id="tl-wrap"><canvas id="tl"></canvas></div>

<div class="toolbar">
  <button class="tbtn" onclick="zoom(1.4)">+ Zoom</button>
  <button class="tbtn" onclick="zoom(0.7)">− Zoom</button>
  <button class="tbtn" onclick="resetZoom()">Reset</button>
  <span style="color:#3a3a5a;font-size:10px" id="zlbl">100%</span>
  <span style="flex:1"></span>
  <span style="color:#3a3a5a;font-size:10px">↓ Segmente</span>
</div>

<div id="main">
  <div id="list"></div>
</div>

<div id="player-bar">
  <span id="player-label">– kein Segment –</span>
  <audio id="player" controls></audio>
</div>

<script>
let SEGS = [$segsJson];
let maxDur = SEGS.reduce((m,s)=>Math.max(m,s.dur_s),10) + 2;
let pxPerSec = 60;
const LABEL_H = 30, ROW_H = 28, PAD = 40;

function zoom(f){pxPerSec=Math.max(10,Math.min(400,pxPerSec*f));document.getElementById('zlbl').textContent=Math.round(pxPerSec/60*100)+'%';draw();}
function resetZoom(){pxPerSec=60;document.getElementById('zlbl').textContent='100%';draw();}

function draw(){
  const cv=document.getElementById('tl');
  const W=Math.max(window.innerWidth, PAD+Math.ceil(maxDur*pxPerSec)+60);
  const H=LABEL_H+SEGS.length*ROW_H+20;
  cv.width=W; cv.height=H;
  const ctx=cv.getContext('2d');
  ctx.fillStyle='#0a0a0f'; ctx.fillRect(0,0,W,H);
  // Time axis
  const step=pxPerSec>=80?5:pxPerSec>=30?10:pxPerSec>=10?30:60;
  ctx.strokeStyle='rgba(252,227,0,.15)'; ctx.lineWidth=1;
  ctx.fillStyle='rgba(252,227,0,.5)'; ctx.font='9px Consolas'; ctx.textBaseline='top';
  for(let t=0;t<=maxDur+step;t+=step){
    const x=PAD+t*pxPerSec; if(x>W) break;
    ctx.beginPath(); ctx.moveTo(x,LABEL_H); ctx.lineTo(x,H); ctx.stroke();
    const m=Math.floor(t/60),s=Math.floor(t%60);
    ctx.fillText(String(m).padStart(2,'0')+':'+String(s).padStart(2,'0'),x+2,4);
  }
  // Rows
  SEGS.forEach((seg,i)=>{
    const y=LABEL_H+i*ROW_H;
    const x=PAD;
    const w=Math.max(4,seg.dur_s*pxPerSec);
    ctx.fillStyle=i%2===0?'#0d0d1a':'#0a0a0f';
    ctx.fillRect(0,y,W,ROW_H);
    // Bar
    ctx.fillStyle='rgba(252,227,0,.55)';
    ctx.beginPath(); ctx.roundRect(x,y+5,w,ROW_H-10,3); ctx.fill();
    ctx.strokeStyle='#fce300'; ctx.lineWidth=1.2;
    ctx.beginPath(); ctx.roundRect(x,y+5,w,ROW_H-10,3); ctx.stroke();
    // Label
    if(w>30){
      ctx.fillStyle='#000'; ctx.font='bold 9px Consolas'; ctx.textBaseline='middle';
      ctx.fillText('#'+String(seg.index).padStart(3,'0')+' '+seg.time,x+4,y+ROW_H/2);
    }
  });
}

function buildList(){
  const el=document.getElementById('list');
  el.innerHTML='';
  if(SEGS.length===0){el.innerHTML='<div style="text-align:center;padding:40px;color:#3a3a5a">Noch keine Aufnahmen</div>';return;}
  SEGS.forEach(seg=>{
    const maxD=Math.max(...SEGS.map(s=>s.dur_s),1);
    const pct=Math.min(100,seg.dur_s/maxD*100).toFixed(1);
    const row=document.createElement('div');
    row.className='row'; row.id='row-'+seg.index;
    row.innerHTML=\`<div class="idx">\${String(seg.index).padStart(3,'0')}</div>
      <div class="ts">\${seg.time}</div>
      <div class="dur">\${seg.dur}</div>
      <div class="bar-wrap"><div class="bar" style="width:\${pct}%"></div></div>
      <button class="btn-play" onclick="playSeq(\${seg.index})">▶ Play</button>
      <a class="btn-dl" href="/audio/\${seg.filename}" download>⬇ WAV</a>\`;
    row.querySelector('.btn-play').onclick=e=>{e.stopPropagation();playSeq(seg.index);};
    el.appendChild(row);
  });
}

function playSeq(idx){
  const seg=SEGS.find(s=>s.index===idx); if(!seg) return;
  const audio=document.getElementById('player');
  const lbl=document.getElementById('player-label');
  document.querySelectorAll('.row').forEach(r=>r.classList.remove('playing'));
  const row=document.getElementById('row-'+idx);
  if(row) row.classList.add('playing');
  audio.src='/audio/'+seg.filename;
  audio.play().catch(()=>{});
  lbl.textContent='▶ #'+String(idx).padStart(3,'0')+' · '+seg.time;
}

// Auto-refresh JSON alle 8s
async function refresh(){
  try{
    const r=await fetch('/segments');
    if(!r.ok) return;
    const d=await r.json();
    const newSegs=(d.segments||[]).map(s=>({
      index:s.index, time:s.time, dur:s.duration_s.toFixed(1)+'s',
      dur_s:s.duration_s, filename:s.filename
    }));
    if(newSegs.length!==SEGS.length){
      SEGS=newSegs;
      maxDur=SEGS.reduce((m,s)=>Math.max(m,s.dur_s),10)+2;
      buildList(); draw();
      document.getElementById('badge').textContent=SEGS.length+' SEG';
    }
  }catch(_){}
}

if(!CanvasRenderingContext2D.prototype.roundRect){
  CanvasRenderingContext2D.prototype.roundRect=function(x,y,w,h,r){
    this.beginPath();this.moveTo(x+r,y);this.lineTo(x+w-r,y);this.quadraticCurveTo(x+w,y,x+w,y+r);
    this.lineTo(x+w,y+h-r);this.quadraticCurveTo(x+w,y+h,x+w-r,y+h);
    this.lineTo(x+r,y+h);this.quadraticCurveTo(x,y+h,x,y+h-r);
    this.lineTo(x,y+r);this.quadraticCurveTo(x,y,x+r,y);this.closePath();
  };
}

buildList(); draw();
window.addEventListener('resize',draw);
setInterval(refresh,8000);
</script>
</body>
</html>''';

    return Response.ok(html, headers: {'content-type': 'text/html; charset=utf-8'});
  }

  /// GET /segments → JSON-Liste aller Segmente
  Response _handleSegmentList(Request req) {
    final list = _segments.map((s) => {
      'index': s.index,
      'time': s.displayTime,
      'duration_s': s.duration.inMilliseconds / 1000.0,
      'filename': s.filename,
      'url': '/audio/${s.filename}',
    }).toList();
    return Response.ok(
      jsonEncode({'segments': list, 'count': list.length}),
      headers: {'content-type': 'application/json'},
    );
  }

  /// GET `/audio/{filename}` → WAV-Datei streamen
  Future<Response> _handleAudio(Request req, String path) async {
    if (_missionDir == null) return Response.notFound('No mission');
    final filename = path.replaceFirst('audio/', '');
    // Sicherheitscheck: kein Path-Traversal
    if (filename.contains('..') || filename.contains('/')) {
      return Response.forbidden('Forbidden');
    }
    final file = File('${_missionDir!.path}/$filename');
    if (!file.existsSync()) return Response.notFound('File not found');

    final bytes = await file.readAsBytes();
    return Response.ok(bytes, headers: {
      'content-type': 'audio/wav',
      'content-length': bytes.length.toString(),
      'content-disposition': 'inline; filename="$filename"',
      'accept-ranges': 'bytes',
    });
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Hilfsfunktionen
  // ─────────────────────────────────────────────────────────────────────────

  /// Sucht das Tailscale-Interface (100.x.x.x)
  static Future<String?> _detectTailscaleIp() async {
    try {
      final interfaces = await NetworkInterface.list(
        type: InternetAddressType.IPv4,
        includeLinkLocal: false,
      );
      for (final iface in interfaces) {
        for (final addr in iface.addresses) {
          if (addr.address.startsWith('100.')) {
            return addr.address;
          }
        }
      }
    } catch (_) {}
    return null;
  }

  static Future<String?> _getLocalIp() async {
    try {
      final interfaces = await NetworkInterface.list(
        type: InternetAddressType.IPv4,
        includeLinkLocal: false,
      );
      for (final iface in interfaces) {
        for (final addr in iface.addresses) {
          if (!addr.isLoopback && addr.address.startsWith('192.168.')) {
            return addr.address;
          }
        }
      }
    } catch (_) {}
    return null;
  }

  static Middleware _corsMiddleware() {
    return (Handler handler) {
      return (Request req) async {
        final resp = await handler(req);
        return resp.change(headers: {
          'access-control-allow-origin': '*',
          ...resp.headers,
        });
      };
    };
  }
}
