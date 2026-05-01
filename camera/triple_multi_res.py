#!/usr/bin/env python3

import subprocess
import os
import sys
# ---------- PATHS DINÁMICOS ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # camera/
SINGLE_SCRIPT = os.path.join(BASE_DIR, "triple_single_res.py")

# (width, height, tolerance_ms)
RESOLUTIONS = [
    # (1280, 720, 10),
    # (1536, 864, 25),
    # (2304, 1296, 45),
    (4608, 2592, 85),
]

for w, h, tol in RESOLUTIONS:
    print(f"\nLaunching {w}x{h} with tolerance {tol} ms")

    subprocess.run(
        [sys.executable, SINGLE_SCRIPT, str(w), str(h), str(tol)],
        check=True
    )

print("\n✅ All resolutions completed")

