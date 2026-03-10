#!/usr/bin/env python3

import subprocess
import threading
import csv
import os
import sys
import re
import time
from datetime import datetime

# ---------------- CONFIG ----------------

OUTDIR = datetime.now().strftime("dualcam_test_%Y%m%d_%H%M%S")

CAMERAS = [0, 1]

WIDTH = 3280
HEIGHT = 2464

SHUTTER_US = 20000       # exposición fija
GAIN = 1.5               # ganancia fija
AWB_GAINS = "1.2,1.2"    # balance blanco fijo

# --------------------------------------

os.makedirs(OUTDIR, exist_ok=True)

BARRIER = threading.Barrier(len(CAMERAS))
CSV_LOCK = threading.Lock()

def run_libcamera(cmd):
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if proc.returncode != 0:
        print("ERROR libcamera:\n", proc.stderr)
        sys.exit(1)
    return proc.stderr

def parse_metadata(stderr):
    meta = {}
    for line in stderr.splitlines():
        line = line.strip()

        if line.startswith("Exposure time:"):
            meta["ExposureTime"] = float(line.split(":")[1])

        elif line.startswith("Ag "):
            nums = re.findall(r"[\d\.]+", line)
            if len(nums) >= 3:
                meta["AnalogueGain"] = float(nums[0])
                meta["DigitalGain"] = float(nums[1])

        elif "ISO" in line:
            nums = re.findall(r"\d+", line)
            if nums:
                meta["ISO"] = int(nums[-1])

    return meta

def capture_camera(cam_id, writer):
    filename = f"cam{cam_id}_{datetime.now().strftime('%H%M%S_%f')}.jpg"
    filepath = os.path.join(OUTDIR, filename)

    cmd = [
        "rpicam-still",
        "--camera", str(cam_id),
        "--width", str(WIDTH),
        "--height", str(HEIGHT),
        "--shutter", str(int(SHUTTER_US)),
        "--gain", str(GAIN),
        "--awb", "custom",
        "--awbgains", AWB_GAINS,
        "--immediate",
        "--nopreview",
        "--verbose",
        "-o", filepath
    ]

    # ---- sincronización ----
    BARRIER.wait()

    t_before_ns = time.monotonic_ns()
    stderr = run_libcamera(cmd)
    t_after_ns = time.monotonic_ns()

    meta = parse_metadata(stderr)

    with CSV_LOCK:
        writer.writerow({
            "camera": cam_id,
            "filename": filename,
            "t_before_ns": t_before_ns,
            "t_after_ns": t_after_ns,
            "duration_ms": (t_after_ns - t_before_ns) / 1e6,
            "exposure_reported_us": meta.get("ExposureTime"),
            "analogue_gain": meta.get("AnalogueGain"),
            "digital_gain": meta.get("DigitalGain"),
            "iso": meta.get("ISO")
        })

def main():
    csv_path = os.path.join(OUTDIR, "metadata.csv")

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "camera",
                "filename",
                "t_before_ns",
                "t_after_ns",
                "duration_ms",
                "exposure_reported_us",
                "analogue_gain",
                "digital_gain",
                "iso"
            ]
        )
        writer.writeheader()

        threads = []
        for cam_id in CAMERAS:
            t = threading.Thread(target=capture_camera, args=(cam_id, writer))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    print("✅ Captura dual sincronizada completada")
    print(f"📂 Resultados en: {OUTDIR}")

if __name__ == "__main__":
    main()
