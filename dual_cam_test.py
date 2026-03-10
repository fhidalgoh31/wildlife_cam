#!/usr/bin/env python3

import subprocess
import threading
import csv
import os
import sys
import time
import re
from datetime import datetime

# ================= CONFIG =================

CAMERAS = {
    0: { "name": "IR_CUT" },
    1: { "name": "IR_FULL" }
}

WIDTH = 3280
HEIGHT = 2464

SWEEP_FACTORS = [0.5, 2, 0.8, 0.3, 1.0, 1.2, 1.5]
BURST_DURATION_SEC = 2
VID_WIDTH = 1280
VID_HEIGHT = 720
BURST_FPS = 10

OUTDIR = datetime.now().strftime("dualcam_exp_%Y%m%d_%H%M%S")

# =========================================

os.makedirs(OUTDIR, exist_ok=True)
CSV_LOCK = threading.Lock()
BARRIER = threading.Barrier(len(CAMERAS))

# -------- helpers --------

def run(cmd):
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if p.returncode != 0:
        print("ERROR:\n", p.stderr)
        sys.exit(1)
    return p.stderr

def parse_meta(stderr):
    meta = {}
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith("Exposure time:"):
            meta["ExposureTime"] = float(line.split(":")[1])
        elif line.startswith("Ag "):
            nums = re.findall(r"[\d\.]+", line)
            if len(nums) >= 2:
                meta["AnalogueGain"] = float(nums[0])
    return meta

# -------- baseline (AE) --------

def baseline_capture(cam_id, writer):
    name = f"01-baseline_cam{cam_id}"
    path = os.path.join(OUTDIR, name + ".jpg")

    cmd = [
        "rpicam-still",
        "--camera", str(cam_id),
        "--width", str(WIDTH),
        "--height", str(HEIGHT),
        "--timeout", "1000",
        "--awb", "auto",
        "--nopreview",
        "--verbose",
        "-o", path
    ]

    BARRIER.wait()
    t0 = time.monotonic_ns()
    stderr = run(cmd)
    t1 = time.monotonic_ns()

    meta = parse_meta(stderr)

    with CSV_LOCK:
        writer.writerow({
            "test": "baseline",
            "camera": cam_id,
            "camera_type": CAMERAS[cam_id]["name"],
            "filename": name,
            "shutter_us": meta.get("ExposureTime"),
            "gain": meta.get("AnalogueGain"),
            "factor": 1.0,
            "t_before_ns": t0,
            "t_after_ns": t1,
            "duration_ms": (t1 - t0) / 1e6
        })

    return meta["ExposureTime"], meta["AnalogueGain"]

# -------- shutter sweep --------

def sweep_capture(cam_id, base_shutter, gain, factor, writer):
    shutter = base_shutter * factor
    name = f"02-sweep_{factor:.2f}x_cam{cam_id}"
    path = os.path.join(OUTDIR, name + ".jpg")

    cmd = [
        "rpicam-still",
        "--camera", str(cam_id),
        "--width", str(WIDTH),
        "--height", str(HEIGHT),
        "--shutter", str(int(shutter)),
        "--gain", str(gain),
        "--awb", "auto",
        "--immediate",
        "--nopreview",
        "--verbose",
        "-o", path
    ]

    BARRIER.wait()
    t0 = time.monotonic_ns()
    stderr = run(cmd)
    t1 = time.monotonic_ns()

    meta = parse_meta(stderr)

    with CSV_LOCK:
        writer.writerow({
            "test": "shutter_sweep",
            "camera": cam_id,
            "camera_type": CAMERAS[cam_id]["name"],
            "filename": name,
            "shutter_us": shutter,
            "gain": gain,
            "factor": factor,
            "t_before_ns": t0,
            "t_after_ns": t1,
            "duration_ms": (t1 - t0) / 1e6
        })

# -------- burst video --------

def burst_video(cam_id, shutter, gain, writer):
    vid =  os.path.join(OUTDIR,f"03-burst_cam{cam_id}.mjpeg")
    pattern =  os.path.join(OUTDIR,f"03-frame_cam{cam_id}_%04d.jpg")

    cmd = [
        "rpicam-vid",
        "--camera", str(cam_id),
        "--codec", "mjpeg",
        "--width", str(VID_WIDTH),
        "--height", str(VID_HEIGHT),
        "--timeout", str(BURST_DURATION_SEC * 1000),
        "--framerate", str(BURST_FPS),
        "--shutter", str(int(shutter)),
        "--gain", str(gain),
        "--awb", "auto",
        "--nopreview",
        "--verbose",
        "-o", vid
    ]

    BARRIER.wait()
    t0 = time.monotonic_ns()
    run(cmd)
    t1 = time.monotonic_ns()

    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", vid, pattern],
        check=True
    )

    with CSV_LOCK:
        writer.writerow({
            "test": "burst_video",
            "camera": cam_id,
            "camera_type": CAMERAS[cam_id]["name"],
            "filename": vid,
            "shutter_us": shutter,
            "gain": gain,
            "factor": None,
            "t_before_ns": t0,
            "t_after_ns": t1,
            "duration_ms": (t1 - t0) / 1e6
        })

# ================= MAIN =================

def main():
    csv_path = os.path.join(OUTDIR, "metadata.csv")

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "test",
            "camera",
            "camera_type",
            "filename",
            "shutter_us",
            "gain",
            "factor",
            "t_before_ns",
            "t_after_ns",
            "duration_ms"
        ])
        writer.writeheader()

        print("=== BASELINE (AE) ===")
        baseline = {}

        threads = []
        for cam in CAMERAS:
            t = threading.Thread(
                target=lambda c=cam:
                    baseline.setdefault(
                        c,
                        baseline_capture(c, writer)
                    )
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        print("=== SHUTTER SWEEP ===")
        for factor in SWEEP_FACTORS:
            threads = []
            for cam in CAMERAS:
                shutter, gain = baseline[cam]
                t = threading.Thread(
                    target=sweep_capture,
                    args=(cam, shutter, gain, factor, writer)
                )
                threads.append(t)
                t.start()
            for t in threads:
                t.join()
            time.sleep(0.3)

        print("=== BURST VIDEO ===")
        threads = []
        for cam in CAMERAS:
            shutter, gain = baseline[cam]
            t = threading.Thread(
                target=burst_video,
                args=(cam, shutter, gain, writer)
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

    print("✅ Experimento completo")
    print(f"📂 {OUTDIR}")

if __name__ == "__main__":
    main()
