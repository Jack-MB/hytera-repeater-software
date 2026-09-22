c = open('hytera_protocol.py', encoding='utf-8').read()
c = c.replace('select(timeout=0.3)\n', 'select(timeout=0.02)\n', 1)
open('hytera_protocol.py', 'w', encoding='utf-8').write(c)
print("OK - select timeout = 20ms" if 'select(timeout=0.02)' in c else "FAILED")
