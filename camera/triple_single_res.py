#!/usr/bin/env python3

import os
import sys
import time
import csv
import re
import cv2
import subprocess
import numpy as np
from picamera2 import Picamera2


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))

WEBAPP_LINK = os.path.join(PROJECT_ROOT, "webapp", "static", "last.jpg")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")


def update_symlink(target_path):
    if os.path.islink(WEBAPP_LINK) or os.path.exists(WEBAPP_LINK):
        os.remove(WEBAPP_LINK)
    os.symlink(target_path, WEBAPP_LINK)

# ================= ARGUMENTOS =================

if len(sys.argv) != 4:
    print("Usage: python3 run_single_res.py WIDTH HEIGHT SYNC_TOLERANCE_MS")
    sys.exit(1)

WIDTH = int(sys.argv[1])
HEIGHT = int(sys.argv[2])
SYNC_TOL_MS = float(sys.argv[3])

RES_LABEL = f"{WIDTH}x{HEIGHT}"
SYNC_THRESHOLD_NS = int(SYNC_TOL_MS * 1_000_000)

print(f"Using sync tolerance: {SYNC_TOL_MS} ms")

# ================= CONFIG =================

BURST_SIZE = 1  # How many pictures per exposure
MAX_ATTEMPTS = 200   # max frames to sync

# Fixed exposure times in microseconds (12 values)
EXPOSURE_TIMES_US = [
    333,
    500,
    1000,
    2000,
    3000,
    4000,
    6000,
    8000,
    10000,
    12000,
    16000,
    18000
]

DATA_DIR = os.path.join(os.getcwd(), "data")
os.makedirs(DATA_DIR, exist_ok=True)

METADATA_FILE = os.path.join(DATA_DIR, "metadata.csv")

# ============================================

def next_test_prefix():
    existing = [f for f in os.listdir(DATA_DIR) if f.startswith("test")]
    nums = []
    for f in existing:
        m = re.match(r"test(\d+)", f)
        if m:
            nums.append(int(m.group(1)))
    next_num = max(nums) + 1 if nums else 1
    return f"test{next_num:03d}"


def setup_csi(cam_id):
    cam = Picamera2(cam_id)
    config = cam.create_video_configuration(
        main={"size": (WIDTH, HEIGHT)}
    )
    cam.configure(config)
    cam.start()

    # Force disable AE and AWB globally
    cam.set_controls({
        "AeEnable": False,
        "AwbEnable": False
    })

    return cam


def set_manual(cam, shutter, gain):
    cam.set_controls({
        "AeEnable": False,
        "ExposureTime": int(shutter),
        "AnalogueGain": float(gain),
        "AwbEnable": False
    })


def capture_pair(cam0, cam1):

    req0 = cam0.capture_request()
    req1 = cam1.capture_request()

    meta0 = req0.get_metadata()
    meta1 = req1.get_metadata()

    ts0 = meta0["SensorTimestamp"]
    ts1 = meta1["SensorTimestamp"]

    exp0 = meta0["ExposureTime"]
    exp1 = meta1["ExposureTime"]

    gain0 = meta0["AnalogueGain"]
    gain1 = meta1["AnalogueGain"]

    delta = abs(ts0 - ts1)

    return req0, req1, ts0, ts1, exp0, exp1, gain0, gain1, delta


def closest_pair_to_ts(cam0, cam1, target_ts):

    best = None
    best_delta = 1e18

    for _ in range(3):  # 6 frames each to find the best match 

        req0, req1, ts0, ts1, exp0, exp1, gain0, gain1, delta = capture_pair(cam0, cam1)

        mid = (ts0 + ts1) // 2
        diff = abs(mid - target_ts)

        if diff < best_delta:

            if best:
                best[0].release()
                best[1].release()

            best = (req0, req1)
            best_delta = diff

        else:
            req0.release()
            req1.release()

    return best


def append_metadata(row):
    file_exists = os.path.isfile(METADATA_FILE)
    with open(METADATA_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "test","resolution","mode","exposure_us","pair",
                "camera","timestamp","delta_ns",
                "exposure","gain","filename"
            ])
        writer.writerow(row)


def find_usb_camera(name):

    try:
        output = subprocess.check_output(
            ["v4l2-ctl","--list-devices"],
            text=True
        )

        blocks = output.split("\n\n")

        for block in blocks:

            if name in block:

                videos = re.findall(r"/dev/video\d+", block)

                for dev in videos:

                    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)

                    if cap.isOpened():

                        ret, frame = cap.read()

                        if ret:
                            print(f"{name} found at {dev}")

                            # Try to disable auto white balance (may not work on all devices)
                            cap.set(cv2.CAP_PROP_AUTO_WB, 0)

                            return cap

                        cap.release()

        return None

    except Exception as e:

        print("USB detection error:", e)
        return None


# ================= MAIN =================

def main():

    prefix = next_test_prefix()
    print(f"\n=== Running {prefix} at {RES_LABEL} ===")

    # ---------- CSI ----------
    cam0 = setup_csi(0)
    cam1 = setup_csi(1)

    # ---------- USB ----------
    print("Opening USB cameras...")

    usb_arducam = find_usb_camera("Arducam")
    arducam_available = usb_arducam is not None

    if not arducam_available:
        print("❌ Arducam not found, exiting.")
        return

    print("Arducam is ready")
    usb_arducam.set(cv2.CAP_PROP_BUFFERSIZE,1)


    time.sleep(1.0)

    # Fixed gain (can be tuned)
    gain = 1.0

    # ================= TRIPLE SYNC TEST =================

    print("\nTriple sync test (2x CSI + Arducam)")

    for exp_time in EXPOSURE_TIMES_US:

        print(f"\nExposure {exp_time} us")

        set_manual(cam0, exp_time, gain)
        set_manual(cam1, exp_time, gain)

        time.sleep(0.3)

        saved = 0
        attempts = 0

        while saved < BURST_SIZE and attempts < MAX_ATTEMPTS:

            attempts += 1

            # Arducam acts as reference clock
            ret_ard, ard_frame = usb_arducam.read()
            ard_ts = time.monotonic_ns()

            if not ret_ard:
                continue

            reqs = closest_pair_to_ts(cam0, cam1, ard_ts)
            if reqs is None:
                continue
            req0, req1 = reqs

            meta0 = req0.get_metadata()
            meta1 = req1.get_metadata()

            ts0 = meta0["SensorTimestamp"]
            ts1 = meta1["SensorTimestamp"]

            exp0 = meta0["ExposureTime"]
            exp1 = meta1["ExposureTime"]

            gain0 = meta0["AnalogueGain"]
            gain1 = meta1["AnalogueGain"]

            fname0 = f"{prefix}_{RES_LABEL}_exp{exp_time}_p{saved}_cam0.jpg"
            fname1 = fname0.replace("cam0","cam1")
            fname_ard = fname0.replace("cam0","arducam")

            img0 = req0.make_image("main")
            img1 = req1.make_image("main")

            img0.save(os.path.join(DATA_DIR, fname0))
            img1.save(os.path.join(DATA_DIR, fname1))

            cv2.imwrite(
                os.path.join(DATA_DIR, fname_ard),
                ard_frame
            )

            append_metadata([
                prefix,RES_LABEL,"triple",
                exp_time,saved,"CSI_0",
                ts0,0,
                exp0,gain0,fname0
            ])

            append_metadata([
                prefix,RES_LABEL,"triple",
                exp_time,saved,"CSI_1",
                ts1,abs(ts1-ts0),
                exp1,gain1,fname1
            ])

            append_metadata([
                prefix,RES_LABEL,"triple",
                exp_time,saved,"arducam",
                ard_ts,abs(ard_ts - ts0),
                -1,-1,fname_ard
            ])

            delta_csi = abs(ts1 - ts0) / 1e6
            delta_ard = abs(ard_ts - ts0) / 1e6
            print(f"ΔCSI: {delta_csi:.2f} ms | ΔARD: {delta_ard:.2f} ms")

            if saved == 0:
                update_symlink(os.path.join(DATA_DIR, fname0))

            req0.release()
            req1.release()

            saved += 1

    # ---------- Cleanup ----------

    cam0.stop()
    cam1.stop()

    cam0.close()
    cam1.close()

    if arducam_available:
        usb_arducam.release()

    print(f"\n✅ Done {RES_LABEL}")


if __name__ == "__main__":
    main()