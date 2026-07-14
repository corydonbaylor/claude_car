#!/usr/bin/env python3
"""
Small, cautious pan-tilt servo test.

Per handoff.md: servos only, motors must be fully stopped and untouched
during this test (this script never imports motor_control, so that's
enforced by construction). Movements are small and slow on purpose —
this is a first-contact test, not a full range-of-motion sweep.

Default (no flags): the original ±15° nudge test on both axes.

With --pan and/or --tilt: centers both servos, then moves smoothly to the
requested angle(s) and holds there (the PCA9685 keeps the position after
the script exits). 0=left/down, 90=forward, 180=right/up.

    python3 tests/test_pan_tilt.py --pan 45
    python3 tests/test_pan_tilt.py --pan 135 --tilt 60
"""

import argparse
import subprocess
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

PAN_CHANNEL = 1
TILT_CHANNEL = 0
PAN_FORWARD = 90
TILT_FORWARD = 90  # physically recalibrated on 2026-07-09 — 90 is now forward (0=left, 180=right)
SMALL_OFFSET = 15  # degrees — small nudge, not a full sweep
STEP_DELAY = 0.05  # seconds between each 1-degree step, for smooth motion


def check_throttled():
    """Check vcgencmd get_throttled. Nonzero means an undervoltage event occurred."""
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True, text=True, timeout=5
        )
        output = result.stdout.strip()
        logger.info(f"vcgencmd: {output}")
        if "0x0" not in output:
            logger.error("Nonzero throttled flag — undervoltage event detected. Stopping.")
            return False
        return True
    except FileNotFoundError:
        logger.warning("vcgencmd not found (not on a Pi?) — skipping throttle check")
        return True


def move_smoothly(kit, channel, from_angle, to_angle, label):
    """Step a servo gradually from one angle to another, 1 degree at a time."""
    logger.info(f"Moving {label}: {from_angle}° -> {to_angle}°")
    step = 1 if to_angle > from_angle else -1
    for angle in range(from_angle, to_angle + step, step):
        kit.servo[channel].angle = angle
        time.sleep(STEP_DELAY)


def move_to_angles(kit, pan_target, tilt_target):
    """Center both servos, then move smoothly to the requested angles and hold."""
    logger.info("Centering both servos first.")
    kit.servo[PAN_CHANNEL].angle = PAN_FORWARD
    kit.servo[TILT_CHANNEL].angle = TILT_FORWARD
    time.sleep(0.5)

    if not check_throttled():
        return

    if pan_target is not None:
        move_smoothly(kit, PAN_CHANNEL, PAN_FORWARD, pan_target, "pan")
        time.sleep(0.5)
        if not check_throttled():
            return

    if tilt_target is not None:
        move_smoothly(kit, TILT_CHANNEL, TILT_FORWARD, tilt_target, "tilt")
        time.sleep(0.5)
        if not check_throttled():
            return

    logger.info(
        f"Done. Holding pan={pan_target if pan_target is not None else PAN_FORWARD}°, "
        f"tilt={tilt_target if tilt_target is not None else TILT_FORWARD}°. "
        "The PCA9685 keeps this position after the script exits."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Pan-tilt servo test. No flags = small ±15° nudge test; "
                    "--pan/--tilt = move to specific angles and hold."
    )
    parser.add_argument("--pan", type=int, default=None,
                        help="Pan angle to move to and hold (0=left, 90=forward, 180=right)")
    parser.add_argument("--tilt", type=int, default=None,
                        help="Tilt angle to move to and hold (0=down, 90=forward, 180=up)")
    args = parser.parse_args()

    for name, val in (("--pan", args.pan), ("--tilt", args.tilt)):
        if val is not None and not 0 <= val <= 180:
            parser.error(f"{name} must be between 0 and 180")

    try:
        from adafruit_servokit import ServoKit
    except ImportError:
        logger.error(
            "adafruit-circuitpython-servokit not installed. "
            "Run: pip install adafruit-circuitpython-servokit"
        )
        return

    logger.info("Connecting to PCA9685 (expected I2C address 0x40)...")
    try:
        kit = ServoKit(channels=16)
    except Exception as e:
        logger.error(
            f"Could not connect to the servo board: {e}\n"
            "Run 'sudo i2cdetect -y 1' and confirm a device shows up at 40 "
            "before retrying."
        )
        return

    if args.pan is not None or args.tilt is not None:
        move_to_angles(kit, args.pan, args.tilt)
        return

    logger.info("Connected. Centering both servos first.")
    kit.servo[PAN_CHANNEL].angle = PAN_FORWARD
    kit.servo[TILT_CHANNEL].angle = TILT_FORWARD
    time.sleep(0.5)

    if not check_throttled():
        return

    # Pan: small nudge one way, back to forward
    move_smoothly(kit, PAN_CHANNEL, PAN_FORWARD, PAN_FORWARD + SMALL_OFFSET, "pan")
    time.sleep(0.5)
    if not check_throttled():
        return

    move_smoothly(kit, PAN_CHANNEL, PAN_FORWARD + SMALL_OFFSET, PAN_FORWARD, "pan")
    time.sleep(0.5)
    if not check_throttled():
        return

    # Tilt: small nudge one way, back to forward
    move_smoothly(kit, TILT_CHANNEL, TILT_FORWARD, TILT_FORWARD + SMALL_OFFSET, "tilt")
    time.sleep(0.5)
    if not check_throttled():
        return

    move_smoothly(kit, TILT_CHANNEL, TILT_FORWARD + SMALL_OFFSET, TILT_FORWARD, "tilt")
    time.sleep(0.5)
    check_throttled()

    logger.info("Done. Both servos back at forward position (pan 90°, tilt 90°).")


if __name__ == "__main__":
    main()
