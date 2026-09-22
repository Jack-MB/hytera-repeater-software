import time
from direct_audio import list_input_devices, DirectAudioCapture

print("=== USB AUDIO PEGEL-TEST ===")
devs = list_input_devices()
for d in devs:
    print(f"[{d[0]}] {d[1]}")

print("\nDieser Test zeigt dir, wie laut dein Audiosignal wirklich am PC ankommt.")
print("Starte Aufnahme auf Standard-Gerät (oder ändere idx im Skript)...")
print("Bitte sprich jetzt oder sende ein Funk-Signal!")
print("-" * 30)

def print_level(lvl):
    bars = "#" * int(lvl/2)
    # the level passed is 0-100 (rms / 327)
    # Let's print the actual RMS if possible, but level is fine.
    print(f"\rPegel: {lvl:3d}% | {bars:<50}", end="", flush=True)

cap = DirectAudioCapture(-1, "rec", vox_threshold=0, level_cb=print_level)
cap.start()

try:
    time.sleep(15)
except KeyboardInterrupt:
    pass

cap.stop()
print("\nTest beendet.")
