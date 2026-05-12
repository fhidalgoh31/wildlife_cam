#!/usr/bin/env python3

import os
import time
import csv
import re
from picamera2 import Picamera2

# ================= CONFIG =================

WIDTH = 1280
HEIGHT = 720

BURST_SIZE = 5
SYNC_THRESHOLD_NS = 6_000_000  # 6 ms tolerancia

SWEEP_FACTORS = [1.0, 0.5, 0.3, 1.5]

DATA_DIR = os.path.join(os.getcwd(), "data")
os.makedirs(DATA_DIR, exist_ok=True)

METADATA_FILE = os.path.join(DATA_DIR, "metadata.csv")

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

def setup_camera(cam_id):
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
                "test",
                "factor",
                "pair_index",
                "camera",
                "sensor_timestamp",
                "delta_ns",
                "exposure_us",
                "gain",
                "filename"
            ])
        writer.writerow(row)

# ================= MAIN =================

def main():

    prefix = next_test_prefix()
    print("Running", prefix)

    cam0 = setup_camera(0)
    cam1 = setup_camera(1)

    time.sleep(1.0)

    print("Getting baseline...")
    base_exp0, base_gain0 = get_baseline(cam0)
    base_exp1, base_gain1 = get_baseline(cam1)

    base_exp = min(base_exp0, base_exp1)
    base_gain = min(base_gain0, base_gain1)

    print("Baseline exposure:", base_exp)

    for factor in SWEEP_FACTORS:

        shutter = base_exp * factor
        gain = base_gain

        print(f"\nFactor {factor} → shutter {shutter}")

        set_manual(cam0, shutter, gain)
        set_manual(cam1, shutter, gain)

        time.sleep(0.5)

        saved = 0

        while saved < BURST_SIZE:

            req0, req1, ts0, ts1, delta = capture_pair(cam0, cam1)

            if delta < SYNC_THRESHOLD_NS:

                delta_ms = delta / 1e6
                short_ts = str(ts0)[-6:]

                fname0 = (
                    f"{prefix}_f{factor}_p{saved}"
                    f"_d{delta_ms:.2f}ms"
                    f"_exp{int(shutter)}"
                    f"_g{gain:.2f}"
                    f"_t{short_ts}"
                    f"_cam0.jpg"
                )

                fname1 = fname0.replace("_cam0", "_cam1")

                img0 = req0.make_image("main")
                img1 = req1.make_image("main")

                img0.save(os.path.join(DATA_DIR, fname0))
                img1.save(os.path.join(DATA_DIR, fname1))

                append_metadata([
                    prefix,
                    factor,
                    saved,
                    0,
                    ts0,
                    delta,
                    shutter,
                    gain,
                    fname0
                ])

                append_metadata([
                    prefix,
                    factor,
                    saved,
                    1,
                    ts1,
                    delta,
                    shutter,
                    gain,
                    fname1
                ])

                print(f"Saved pair {saved} | Δ={delta_ms:.3f} ms")

                saved += 1

            req0.release()
            req1.release()

    cam0.stop()
    cam1.stop()

    print("\n✅ Done")

if __name__ == "__main__":
    main()

