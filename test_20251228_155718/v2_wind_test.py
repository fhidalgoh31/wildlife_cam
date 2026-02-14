#!/usr/bin/env python3
# python3 v2_wind_test.py --burst-factor 1.5

import subprocess
import argparse
import csv
import os
import sys
import time
from datetime import datetime

# ---------------- CONFIG ----------------

SWEEP_FACTORS = [0.5, 3.0, 0.8, 2.0, 1.0, 1.3, 1.2, 1.1]
BURST_DURATION_SEC = 3
DEFAULT_FPS = 10

# ----------------------------------------

def run_libcamera(cmd):
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if proc.returncode != 0:
        print("Error running libcamera:", proc.stderr)
        sys.exit(1)
    return proc.stderr


import re

def parse_metadata(stderr):
    meta = {}

    for line in stderr.splitlines():
        line = line.strip()

        # Exposure time (primary)
        if line.startswith("Exposure time:"):
            meta["ExposureTime"] = float(line.split(":")[1].strip())

        # Gain line: "Ag 9.48148 Dg 1.03618 Total 9.82454"
        elif line.startswith("Ag "):
            nums = re.findall(r"[\d\.]+", line)
            if len(nums) >= 3:
                meta["AnalogueGain"] = float(nums[0])
                meta["DigitalGain"] = float(nums[1])
                meta["TotalGain"] = float(nums[2])

        # ISO line
        elif "ISO" in line:
            nums = re.findall(r"\d+", line)
            if nums:
                meta["ISO"] = int(nums[-1])

        # Sensor temperature (optional, may not always appear)
        elif "temperature" in line.lower():
            nums = re.findall(r"[\d\.]+", line)
            if nums:
                meta["SensorTemperature"] = float(nums[0])

    return meta



def iso_equivalent(analogue_gain):
    return round(100 * analogue_gain)


def capture_image(base_name, shutter=None, gain=None, auto_exposure=True):
    cmd = [
        "libcamera-still",
        "--raw",
        "--verbose",
        "-o", f"{base_name}.jpg",
        "--awb", "auto"
    ]

    # --- Timing ---
    if auto_exposure:
        # AE ON (implícito)
        cmd += ["--timeout", "1000"]
    else:
        # AE OFF (implícito al fijar shutter)
        cmd += ["--immediate"]

    # --- Manual exposure ---
    if shutter is not None:
        cmd += ["--shutter", str(int(shutter))]

    if gain is not None:
        cmd += ["--gain", str(gain)]

    stderr = run_libcamera(cmd)
    return parse_metadata(stderr)


def write_csv_row(writer, filename, test_type, frame_idx, shutter_us,
                  shutter_factor, meta, ae_enabled, notes=""):
    writer.writerow({
        "timestamp": datetime.now().isoformat(),
        "filename": filename,
        "test_type": test_type,
        "frame_idx": frame_idx,
        "shutter_us": shutter_us,
        "shutter_factor": shutter_factor,
        "analogue_gain": meta.get("AnalogueGain"),
        "digital_gain": meta.get("DigitalGain"),
        "iso_eq": meta.get("ISO"),
        "ae_enabled": ae_enabled,
        "lux": meta.get("Lux"),
        "sensor_temperature": meta.get("SensorTemperature"),
        "exposure_time_reported": meta.get("ExposureTime"),
        "notes": notes
    })

def run_burst_vid(
    shutter_us,
    gain,
    burst_factor,
    writer,
    duration_sec=3,
    fps=10
):
    print("Test 3: Burst (libcamera-vid)")

    video_file = "03-burst.mjpeg"
    frame_pattern = "03-frame_%04d.jpg"

    # --- 1) Captura de video ---
    cmd = [
        "libcamera-vid",
        "--codec", "mjpeg",
        "--timeout", str(duration_sec * 1000),
        "--framerate", str(fps),
        "--shutter", str(int(shutter_us)),
        "--gain", str(gain),
        "--awb", "auto",
        "--nopreview",
        "--verbose",
        "-o", video_file
    ]

    subprocess.run(cmd, check=True)

    # --- 2) Extraer frames ---
    subprocess.run([
        "ffmpeg",
        "-loglevel", "error",
        "-i", video_file,
        frame_pattern
    ], check=True)

    # --- 3) Registrar frames en CSV ---
    frames = sorted(f for f in os.listdir(".") if f.startswith("03-frame_"))

    for idx, fname in enumerate(frames):
        writer.writerow({
            "timestamp": datetime.now().isoformat(),
            "filename": fname.replace(".jpg", ""),
            "test_type": "burst",
            "frame_idx": idx,
            "shutter_us": shutter_us,
            "shutter_factor": burst_factor,
            "analogue_gain": gain,
            "digital_gain": None,
            "iso_eq": None,
            "ae_enabled": False,
            "lux": None,
            "sensor_temperature": None,
            "exposure_time_reported": shutter_us,
            "notes": "libcamera-vid burst"
        })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--burst-factor",
        type=float,
        default=1.1,
        help="Factor del shutter automático para el burst (default 1.1)"
    )
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = f"test_{timestamp}"
    os.makedirs(outdir, exist_ok=True)
    os.chdir(outdir)

    with open("metadata.csv", "w", newline="") as csvfile:
        fieldnames = [
            "timestamp", "filename", "test_type", "frame_idx",
            "shutter_us", "shutter_factor",
            "analogue_gain", "digital_gain", "iso_eq",
            "ae_enabled", "lux", "sensor_temperature",
            "exposure_time_reported", "notes"
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        # ---------- TEST 1: BASELINE ----------
        print("Test 1: Baseline automático")
        meta = capture_image("01-Baseline", auto_exposure=True)
        shutter_auto = meta.get("ExposureTime")
        gain_auto = meta.get("AnalogueGain")

        write_csv_row(
            writer,
            "01-Baseline",
            "baseline",
            None,
            shutter_auto,
            1.0,
            meta,
            True
        )
        if "ExposureTime" not in meta or "AnalogueGain" not in meta:
            print("ERROR: No se pudieron leer metadatos críticos del baseline")
            print(meta)
            sys.exit(1)

        shutter_auto = meta["ExposureTime"]
        gain_auto = meta["AnalogueGain"]
    
        # ---------- TEST 2: SHUTTER SWEEP ----------
        print("Test 2: Sweep de shutter")
        for factor in SWEEP_FACTORS:
            shutter = shutter_auto * factor
            name = f"02-shutter_{factor}x_{int(shutter)}us"
            meta = capture_image(name, shutter=shutter, gain=gain_auto, auto_exposure=False)
            write_csv_row(
                writer,
                name,
                "shutter_sweep",
                None,
                shutter,
                factor,
                meta,
                False
            )
            time.sleep(0.5)

        # ---------- TEST 3: BURST ----------
        print("Test 3: Burst")
        burst_shutter = shutter_auto * args.burst_factor
        frame_count = BURST_DURATION_SEC * DEFAULT_FPS

        burst_shutter = shutter_auto * args.burst_factor

        run_burst_vid(
            shutter_us=burst_shutter,
            gain=gain_auto,
            burst_factor=args.burst_factor,
            writer=writer,
            duration_sec=3,
            fps=DEFAULT_FPS
        )


    print("Test completo.")

if __name__ == "__main__":
    main()
