#!/usr/bin/env python3
# 2CSI + Arducam + Thermal, baseline for exposure and factors

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

BURST_SIZE = 5
SWEEP_FACTORS = [1.0, 0.8, 0.3, 1.5]
TRIPLE_FACTOR = 0.8
MAX_ATTEMPTS = 200

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
    return cam


def get_baseline(cam):
    cam.set_controls({"AeEnable": True})
    time.sleep(1.0)
    req = cam.capture_request()
    meta = req.get_metadata()
    req.release()
    return meta["ExposureTime"], meta["AnalogueGain"]


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

    for _ in range(6):

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
                "test","resolution","mode","factor","pair",
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

    usb_thermal = find_usb_camera("Pure")
    usb_arducam = find_usb_camera("Arducam")

    thermal_available = usb_thermal is not None
    arducam_available = usb_arducam is not None

    if thermal_available:
        usb_thermal.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        usb_thermal.set(cv2.CAP_PROP_FRAME_WIDTH,160)
        usb_thermal.set(cv2.CAP_PROP_FRAME_HEIGHT,120)

        usb_thermal.set(
            cv2.CAP_PROP_FOURCC,
            cv2.VideoWriter_fourcc('Y','1','6',' ')
        )

    if arducam_available:
        usb_arducam.set(cv2.CAP_PROP_BUFFERSIZE,1)

    print("Thermal:", thermal_available)
    print("Arducam:", arducam_available)

    time.sleep(1.0)

    # ---------- Baseline ----------
    base_exp0, base_gain0 = get_baseline(cam0)
    base_exp1, base_gain1 = get_baseline(cam1)

    base_exp = min(base_exp0, base_exp1)
    base_gain = min(base_gain0, base_gain1)

    print("Baseline exposure:", base_exp)
    print("Baseline gain:", base_gain)

    # ================= SWEEP CSI =================

    for factor in SWEEP_FACTORS:

        print(f"\nSweep factor {factor}")

        shutter = base_exp * factor
        gain = base_gain

        set_manual(cam0, shutter, gain)
        set_manual(cam1, shutter, gain)
        time.sleep(0.5)

        saved = 0
        attempts = 0

        while saved < BURST_SIZE and attempts < MAX_ATTEMPTS:

            attempts += 1

            req0, req1, ts0, ts1, exp0, exp1, gain0, gain1, delta = capture_pair(cam0, cam1)

            if delta < SYNC_THRESHOLD_NS:

                fname0 = f"{prefix}_{RES_LABEL}_sweep_f{factor}_p{saved}_cam0.jpg"
                fname1 = fname0.replace("cam0","cam1")

                img0 = req0.make_image("main")
                img1 = req1.make_image("main")

                img0.save(os.path.join(DATA_DIR, fname0))
                img1.save(os.path.join(DATA_DIR, fname1))

                append_metadata([prefix,RES_LABEL,"sweep",
                                 factor,saved,0,ts0,delta,
                                 exp0,gain0,fname0])

                append_metadata([prefix,RES_LABEL,"sweep",
                                 factor,saved,1,ts1,delta,
                                 exp1,gain1,fname1])

                if saved == 0:
                    update_symlink(os.path.join(DATA_DIR, fname0))

                saved += 1

            req0.release()
            req1.release()

    # ================= QUAD TEST =================

    print("\nQuad test using thermal reference")

    shutter = base_exp * TRIPLE_FACTOR
    gain = base_gain

    set_manual(cam0, shutter, gain)
    set_manual(cam1, shutter, gain)

    saved = 0
    attempts = 0

    while saved < BURST_SIZE and attempts < MAX_ATTEMPTS:

        attempts += 1

        ret_th, thermal_frame = usb_thermal.read()
        thermal_ts = time.monotonic_ns()

        if not ret_th:
            continue

        ret_ard = False
        ard_frame = None
        ard_ts = None

        if arducam_available:

            ret_ard, ard_frame = usb_arducam.read()
            ard_ts = time.monotonic_ns()

        req0, req1 = closest_pair_to_ts(
            cam0,
            cam1,
            thermal_ts
        )

        meta0 = req0.get_metadata()
        meta1 = req1.get_metadata()

        ts0 = meta0["SensorTimestamp"]
        ts1 = meta1["SensorTimestamp"]

        exp0 = meta0["ExposureTime"]
        exp1 = meta1["ExposureTime"]

        gain0 = meta0["AnalogueGain"]
        gain1 = meta1["AnalogueGain"]

        fname0 = f"{prefix}_{RES_LABEL}_quad_p{saved}_cam0.jpg"
        fname1 = fname0.replace("cam0","cam1")
        fname_ard = fname0.replace("cam0","arducam")
        fname_th = fname0.replace("cam0","thermal").replace(".jpg",".png")

        img0 = req0.make_image("main")
        img1 = req1.make_image("main")

        img0.save(os.path.join(DATA_DIR, fname0))
        img1.save(os.path.join(DATA_DIR, fname1))

        append_metadata([
            prefix,RES_LABEL,"quad",
            TRIPLE_FACTOR,saved,0,
            ts0,abs(ts0-thermal_ts),
            exp0,gain0,fname0
        ])

        append_metadata([
            prefix,RES_LABEL,"quad",
            TRIPLE_FACTOR,saved,1,
            ts1,abs(ts1-thermal_ts),
            exp1,gain1,fname1
        ])

        if arducam_available and ret_ard:

            cv2.imwrite(
                os.path.join(DATA_DIR, fname_ard),
                ard_frame
            )

            append_metadata([
                prefix,RES_LABEL,"quad",
                TRIPLE_FACTOR,saved,"arducam",
                ard_ts,abs(ard_ts-thermal_ts),
                0,0,fname_ard
            ])

        thermal = thermal_frame.astype(np.uint16)

        print("thermal range:",
            thermal_frame.min(),
            thermal_frame.max())

        cv2.imwrite(
            os.path.join(DATA_DIR, fname_th),
            thermal
        )

        append_metadata([
            prefix,RES_LABEL,"quad",
            TRIPLE_FACTOR,saved,"thermal",
            thermal_ts,0,
            0,0,fname_th
        ])

        req0.release()
        req1.release()

        saved += 1

    # ---------- Cleanup ----------

    cam0.stop()
    cam1.stop()

    cam0.close()
    cam1.close()

    if thermal_available:
        usb_thermal.release()

    if arducam_available:
        usb_arducam.release()

    print(f"\n✅ Done {RES_LABEL}")


if __name__ == "__main__":
    main()