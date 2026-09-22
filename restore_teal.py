import re

# Update timeline_viewer.html
tv_path = 'c:/Users/jc/Hytera Repetaer Software/hytera_project_v1_original/timeline_viewer.html'
with open(tv_path, 'r', encoding='utf-8') as f:
    html = f.read()

# Replace CSS variables
old_vars = r':root\s*\{[^}]+\}'
new_vars = ''':root {
  --bg:#0f1117; --panel:#1a1d27; --border:#00d4aa;
  --acc:#00d4aa; --acc2:#4fc3f7; --red:#ef5350;
  --grn:#26a69a; --org:#ffb74d; --tx:#eceff4; --sub:#7b8496;
  --ts1:#00d4aa; --ts2:#4fc3f7; --sms:#ce93d8; --gps:#4fc3f7;
  --usv:#ffb74d; --call:#ef5350;
}'''
html = re.sub(old_vars, new_vars, html)

# Replace rgba(252,227,0,...) with rgba(0,212,170,...)
html = html.replace('252,227,0', '0,212,170')
html = html.replace('#fce300', '#00d4aa')

# Also in the JavaScript const C = {...}
old_c = r'const C = \{[^}]+\};'
new_c = '''const C = {
  ts1: '#00d4aa', ts2: '#4fc3f7', grn: '#26a69a', red: '#ef5350',
  gps: '#4fc3f7', usv: '#ffb74d', sms: '#ce93d8', org: '#ffb74d',
  sub: '#7b8496', tx:  '#eceff4', bg: '#0f1117', panel: '#1a1d27',
  border: 'rgba(0,212,170,0.2)',
};'''
html = re.sub(old_c, new_c, html)

with open(tv_path, 'w', encoding='utf-8') as f:
    f.write(html)

# Update app.py
app_path = 'c:/Users/jc/Hytera Repetaer Software/hytera_project_v1_original/app.py'
with open(app_path, 'r', encoding='utf-8') as f:
    app_py = f.read()

old_c_py = r'C = \{\s*\"bg\":.*?\}'
new_c_py = '''C = {
    "bg":    "#0f1117",
    "panel": "#1a1d27",
    "panel2":"#151720",
    "border":"#2d3250",
    "acc":   "#00d4aa",
    "acc2":  "#4fc3f7",
    "red":   "#ef5350",
    "red2":  "#4a1919",
    "grn":   "#26a69a",
    "grn2":  "#0a332f",
    "org":   "#ffb74d",
    "tx":    "#eceff4",
    "tx2":   "#aeb5c2",
    "sub":   "#7b8496",
    "hdr":   "#13151c",
    "sel":   "#183331",
    "dim":   "#1c1f2b",
    "ts1":   "#00d4aa",
    "ts2":   "#4fc3f7",
}'''
app_py = re.sub(old_c_py, new_c_py, app_py, flags=re.DOTALL)

with open(app_path, 'w', encoding='utf-8') as f:
    f.write(app_py)

print('Restored Teal design to v1_original!')
