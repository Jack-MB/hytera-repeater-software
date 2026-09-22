import re
import os
import json

path = "mission_logger.py"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

# Replace _build_standalone_html
new_func = """def _build_standalone_html(data):
    \"\"\"Erzeugt eine komplett autonome Timeline-HTML mit eingebetteten Daten
    und relativen Audio-Pfaden (für Öffnen aus Datei ohne Server).\"\"\"
    import json
    import os
    ev_json  = json.dumps(data.get("events", []), ensure_ascii=False)
    ids_json = json.dumps(data.get("ids", {}),    ensure_ascii=False)
    name     = data.get("name", "Einsatz")
    start    = data.get("start", "")

    # Lese das aktuelle timeline_viewer.html
    viewer_path = os.path.join(os.path.dirname(__file__), "timeline_viewer.html")
    with open(viewer_path, "r", encoding="utf-8") as f:
        html = f.read()

    injection = f\"\"\"<script>
window.STANDALONE_DATA = {{
    events: {ev_json},
    ids: {ids_json},
    name: "{name}",
    start: "{start}"
}};
</script>
\"\"\"
    html = html.replace("<head>", "<head>\\n" + injection, 1)
    return html
"""

# Un-escape the f-strings for Python
new_func = new_func.replace('\\"\\"\\"', '"""')

start_idx = text.find('def _build_standalone_html(data):')
if start_idx != -1:
    text = text[:start_idx] + new_func

with open(path, "w", encoding="utf-8") as f:
    f.write(text)
