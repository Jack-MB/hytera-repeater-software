import sys
sys.stdout = __import__('io').TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open('hytera_protocol.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find line index of the broken on_packet in TelemetryListener (around line 425)
# We know lines 424-445 (0-indexed) are broken. Replace them.
GOOD = '''\
    def on_packet(self, payload, addr):
        if len(payload) < 12: return
        try:
            msghdr  = payload[0] & 0x7F
            opcode  = struct.unpack_from('<H', payload, 1)[0]
            n_bytes = struct.unpack_from('<H', payload, 3)[0]
            data    = payload[5:5+n_bytes]
            if len(data) < 6: return
            radio_ip = struct.unpack_from('>I', data, 0)[0]
            radio_id = radio_ip & 0xFFFFFF
            channel  = data[4] if len(data) > 4 else 0
            value    = data[5] if len(data) > 5 else 0
            self.tele_cb(TelemetryEvent(radio_id, channel, value))
        except Exception as e:
            log.debug("[Tele] %s" % e)
'''

new_lines = lines[:424] + [GOOD]
with open('hytera_protocol.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print("OK, total lines:", sum(1 for l in new_lines) + GOOD.count('\n') - 1)
