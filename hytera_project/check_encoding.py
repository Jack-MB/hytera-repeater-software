import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if '\ufffd' in line:
        # Zeige Kontext: was steht links und rechts vom Ersatzzeichen
        safe = line.replace('\ufffd', '[?]').rstrip()
        print(f'{i+1:5d}: {safe}')
