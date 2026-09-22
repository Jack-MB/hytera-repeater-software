#!/usr/bin/env python3
"""
Starter-Forwarder für Aufrufe direkt aus dem hytera_project Verzeichnis.
Leitet an das übergeordnete run_map_app.py weiter.
"""
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))

if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# run_map_app importieren und ausführen
from run_map_app import main

if __name__ == "__main__":
    main()
