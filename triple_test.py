#!/usr/bin/env python3

import os
import time
import csv
import re
import cv2
from picamera2 import Picamera2

# ================= CONFIG =================

WIDTH =  2304
HEIGHT = 1296

BURST_SIZE = 5
SYNC_THRESHOLD_NS = 60_000_000

SWEEP_FACTORS = [1.0, 0.8, 0.3, 1.5]
TRIPLE_FACTOR = 0.5

DATA_DIR = os.path.join(os.getcwd(), "data")
os.makedirs(DATA_DIR, exist_ok=True)

METADATA_FILE = os.path.join(DATA_DIR, "metadata.csv")

USB_INDEX = 16  # tu Arducam USB

# ==========================================

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
                "test","mode","factor","pair",
                "camera","timestamp","delta_ns",
                "exposure","gain","filename"
            ])
        writer.writerow(row)

# ================= MAIN =================

def main():

    prefix = next_test_prefix()
    print("Running", prefix)

    cam0 = setup_csi(0)
    cam1 = setup_csi(1)

    usb = cv2.VideoCapture(USB_INDEX)
    usb.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    time.sleep(1.0)

    print("Getting baseline...")
    base_exp0, base_gain0 = get_baseline(cam0)
    base_exp1, base_gain1 = get_baseline(cam1)

    base_exp = min(base_exp0, base_exp1)
    base_gain = min(base_gain0, base_gain1)

    # ================= SWEEP CSI =================

    for factor in SWEEP_FACTORS:

        shutter = base_exp * factor
        gain = base_gain

        print(f"\nCSI Sweep factor {factor}")

        set_manual(cam0, shutter, gain)
        set_manual(cam1, shutter, gain)

        time.sleep(0.5)

        saved = 0

        while saved < BURST_SIZE:

            req0, req1, ts0, ts1, delta = capture_pair(cam0, cam1)

            if delta < SYNC_THRESHOLD_NS:

                delta_ms = delta / 1e6
                fname0 = f"{prefix}_sweep_f{factor}_p{saved}_d{delta_ms:.2f}ms_cam0.jpg"
                fname1 = fname0.replace("cam0","cam1")

                img0 = req0.make_image("main")
                img1 = req1.make_image("main")

                img0.save(os.path.join(DATA_DIR, fname0))
                img1.save(os.path.join(DATA_DIR, fname1))

                append_metadata([prefix,"sweep",factor,saved,0,ts0,delta,shutter,gain,fname0])
                append_metadata([prefix,"sweep",factor,saved,1,ts1,delta,shutter,gain,fname1])

                print(f"Saved CSI pair {saved} Δ={delta_ms:.3f} ms")
                saved += 1

            req0.release()
            req1.release()

    # ================= TRIPLE TEST =================

    print("\nRunning triple test (factor 0.5)")

    shutter = base_exp * TRIPLE_FACTOR
    gain = base_gain

    set_manual(cam0, shutter, gain)
    set_manual(cam1, shutter, gain)

    time.sleep(0.5)

    saved = 0

    while saved < BURST_SIZE:

        req0, req1, ts0, ts1, delta = capture_pair(cam0, cam1)

        ret, usb_frame = usb.read()
        usb_ts = time.monotonic_ns()

        if delta < SYNC_THRESHOLD_NS and ret:

            delta_ms = delta / 1e6
            delta_usb = abs(usb_ts - ts0)

            fname0 = f"{prefix}_triple_p{saved}_d{delta_ms:.2f}ms_cam0.jpg"
            fname1 = fname0.replace("cam0","cam1")
            fname_usb = fname0.replace("cam0","usb")

            img0 = req0.make_image("main")
            img1 = req1.make_image("main")

            img0.save(os.path.join(DATA_DIR, fname0))
            img1.save(os.path.join(DATA_DIR, fname1))
            cv2.imwrite(os.path.join(DATA_DIR, fname_usb), usb_frame)

            append_metadata([prefix,"triple",TRIPLE_FACTOR,saved,0,ts0,delta,shutter,gain,fname0])
            append_metadata([prefix,"triple",TRIPLE_FACTOR,saved,1,ts1,delta,shutter,gain,fname1])
            append_metadata([prefix,"triple",TRIPLE_FACTOR,saved,"usb",usb_ts,delta_usb,shutter,gain,fname_usb])

            print(f"Saved TRIPLE pair {saved} ΔCSI={delta_ms:.3f} ms")
            saved += 1

        req0.release()
        req1.release()

    cam0.stop()
    cam1.stop()
    usb.release()

    print("\n✅ Done")

if __name__ == "__main__":
    main()

