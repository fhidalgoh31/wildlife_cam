#!/usr/bin/env python3

import os
import sys
import time
import csv
import re
import cv2
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
TRIPLE_FACTOR = 0.5
MAX_ATTEMPTS = 200

DATA_DIR = os.path.join(os.getcwd(), "data")
os.makedirs(DATA_DIR, exist_ok=True)

METADATA_FILE = os.path.join(DATA_DIR, "metadata.csv")
USB_INDEX = 16

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
    ts0 = req0.get_metadata()["SensorTimestamp"]
    ts1 = req1.get_metadata()["SensorTimestamp"]
    delta = abs(ts0 - ts1)
    return req0, req1, ts0, ts1, delta

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

# ================= MAIN =================

def main():

    prefix = next_test_prefix()
    print(f"\n=== Running {prefix} at {RES_LABEL} ===")

    # ---------- CSI ----------
    cam0 = setup_csi(0)
    cam1 = setup_csi(1)

    # ---------- USB ----------
    # print("Opening USB camera...")
    # usb = cv2.VideoCapture(f"/dev/video{USB_INDEX}", cv2.CAP_V4L2)
    import subprocess
    import re

    def find_arducam_index():
        try:
            output = subprocess.check_output(
                ["v4l2-ctl", "--list-devices"],
                text=True
            )

            blocks = output.split("\n\n")

            for block in blocks:
                if "Arducam" in block:
                    videos = re.findall(r"/dev/video\d+", block)

                    for dev in videos:
                        cap = cv2.VideoCapture(dev)
                        if cap.isOpened():
                            ret, frame = cap.read()
                            if ret:
                                print(f"USB camera found at {dev}")
                                return cap
                            cap.release()

            return None

        except Exception as e:
            print("USB detection error:", e)
            return None


    print("Opening USB camera...")
    usb = find_arducam_index()

    if usb is None:
        print("⚠ WARNING: USB camera did not open. Triple test will skip USB.")
        usb_available = False
    else:
        usb.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        usb_available = True
        print("USB camera OK")


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

            req0, req1, ts0, ts1, delta = capture_pair(cam0, cam1)

            if delta < SYNC_THRESHOLD_NS:

                fname0 = f"{prefix}_{RES_LABEL}_sweep_f{factor}_p{saved}_cam0.jpg"
                fname1 = fname0.replace("cam0","cam1")

                img0 = req0.make_image("main")
                img1 = req1.make_image("main")

                img0.save(os.path.join(DATA_DIR, fname0))
                img1.save(os.path.join(DATA_DIR, fname1))

                append_metadata([prefix,RES_LABEL,"sweep",
                                 factor,saved,0,ts0,delta,
                                 shutter,gain,fname0])
                append_metadata([prefix,RES_LABEL,"sweep",
                                 factor,saved,1,ts1,delta,
                                 shutter,gain,fname1])
                if saved == 0:
                    update_symlink(os.path.join(DATA_DIR, fname0))
                saved += 1
            

            req0.release()
            req1.release()

        if saved < BURST_SIZE:
            print("⚠ Sweep incomplete (sync threshold too strict?)")

    # ================= TRIPLE TEST =================

    print("\nTriple test (factor 0.5)")

    shutter = base_exp * TRIPLE_FACTOR
    gain = base_gain

    set_manual(cam0, shutter, gain)
    set_manual(cam1, shutter, gain)
    time.sleep(0.5)

    saved = 0
    attempts = 0

    while saved < BURST_SIZE and attempts < MAX_ATTEMPTS:
        attempts += 1

        req0, req1, ts0, ts1, delta = capture_pair(cam0, cam1)

        usb_frame = None
        usb_ts = None
        ret = False

        if usb_available:
            ret, usb_frame = usb.read()
            usb_ts = time.monotonic_ns()

        if delta < SYNC_THRESHOLD_NS:

            fname0 = f"{prefix}_{RES_LABEL}_triple_p{saved}_cam0.jpg"
            fname1 = fname0.replace("cam0","cam1")
            fname_usb = fname0.replace("cam0","usb")

            img0 = req0.make_image("main")
            img1 = req1.make_image("main")

            img0.save(os.path.join(DATA_DIR, fname0))
            img1.save(os.path.join(DATA_DIR, fname1))

            append_metadata([prefix,RES_LABEL,"triple",
                             TRIPLE_FACTOR,saved,0,ts0,delta,
                             shutter,gain,fname0])
            append_metadata([prefix,RES_LABEL,"triple",
                             TRIPLE_FACTOR,saved,1,ts1,delta,
                             shutter,gain,fname1])

            if usb_available and ret:
                cv2.imwrite(os.path.join(DATA_DIR, fname_usb), usb_frame)
                append_metadata([prefix,RES_LABEL,"triple",
                                 TRIPLE_FACTOR,saved,"usb",
                                 usb_ts,abs(usb_ts-ts0),
                                 shutter,gain,fname_usb])

            saved += 1

        req0.release()
        req1.release()

    if saved < BURST_SIZE:
        print("⚠ Triple test incomplete")

    # ---------- Cleanup ----------
    cam0.stop()
    cam1.stop()
    cam0.close()
    cam1.close()

    if usb_available:
        usb.release()

    print(f"\n✅ Done {RES_LABEL}")

if __name__ == "__main__":
    main()


