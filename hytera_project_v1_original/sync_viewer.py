import re
import os

def main():
    with open('timeline_viewer.html', 'r', encoding='utf-8') as f:
        html = f.read()

    # Replace single curly braces in HTML for python f-string formatting
    html_escaped = html.replace('{', '{{').replace('}', '}}')
    
    inject_js_escaped = """<script>
const STANDALONE_DATA = {{
    events: {ev_json},
    ids: {ids_json},
    name: "{name}",
    start: "{start}"
}};
"""
    html_escaped = html_escaped.replace('<script>', inject_js_escaped, 1)

    new_func = f'''def _build_standalone_html(data):
    """Erzeugt eine komplett autonome Timeline-HTML mit eingebetteten Daten
    und relativen Audio-Pfaden (für Öffnen aus Datei ohne Server)."""
    import json
    ev_json  = json.dumps(data.get("events", []), ensure_ascii=False)
    ids_json = json.dumps(data.get("ids", {{}}),    ensure_ascii=False)
    name     = data.get("name", "Einsatz")
    start    = data.get("start", "")
    
    return f"""{html_escaped}"""
'''

    with open('mission_logger.py', 'r', encoding='utf-8') as f:
        logger_code = f.read()

    start_idx = logger_code.find('def _build_standalone_html(data):')
    end_idx = logger_code.find('# ─────────────────────────────────────────────────────────────\n#  HTML-Generator', start_idx)
    
    new_logger_code = logger_code[:start_idx] + new_func + '\n\n' + logger_code[end_idx:]
    
    with open('mission_logger.py', 'w', encoding='utf-8') as f:
        f.write(new_logger_code)
        
    print("mission_logger.py updated successfully!")

if __name__ == '__main__':
    main()
