import json
import re
p2 = r'C:\Users\jc\Hytera Repetaer Software\hytera_project\missions\Osnabrück Test 16.08.2026\timeline.html'
try:
    with open(p2, 'r', encoding='utf-8') as f:
        text = f.read()
    m = re.search(r'window\.STANDALONE_DATA = (\{.*?\});\n', text, re.DOTALL)
    if m:
        jstr = m.group(1)
        # Fix unquoted keys
        jstr = re.sub(r'^\s*events:', '"events":', jstr, flags=re.MULTILINE)
        jstr = re.sub(r'^\s*name:', '"name":', jstr, flags=re.MULTILINE)
        jstr = re.sub(r'^\s*start:', '"start":', jstr, flags=re.MULTILINE)
        jstr = re.sub(r'^\s*ids:', '"ids":', jstr, flags=re.MULTILINE)
        d = json.loads(jstr)
        print('Events in timeline.html:', len(d.get('events', [])))
        # Recover them!
        p1 = r'C:\Users\jc\Hytera Repetaer Software\hytera_project\missions\Osnabrück Test 16.08.2026\mission.json'
        with open(p1, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        print('Recovered mission.json!')
    else:
        print('No STANDALONE_DATA found in timeline.html')
except Exception as e:
    print('Error:', e)
