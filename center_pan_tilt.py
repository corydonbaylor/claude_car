#!/usr/bin/env python3
"""
Hold the pan-tilt servos at a fixed angle so the mount can be physically
adjusted (e.g. reattaching the servo horn so "forward" lines up with the
commanded angle).

The PCA9685 keeps outputting the last commanded PWM signal in hardware
even after this script exits, so the servo holds position on its own —
no need to keep the script running while you adjust the mount.
"""

import subprocess
import logging
import argparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

PAN_CHANNEL = 0
TILT_CHANNEL = 1


def check_throttled():
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


def main():
    parser = argparse.ArgumentParser(
        description="Hold pan-tilt servos at a fixed angle for mechanical adjustment"
    )
    parser.add_argument("--pan", type=int, default=90, help="Pan angle (default: 90, forward)")
    parser.add_argument("--tilt", type=int, default=90, help="Tilt angle (default: 90, forward)")
    args = parser.parse_args()

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

    if not check_throttled():
        return

    logger.info(f"Setting pan to {args.pan}°, tilt to {args.tilt}°...")
    kit.servo[PAN_CHANNEL].angle = args.pan
    kit.servo[TILT_CHANNEL].angle = args.tilt

    if not check_throttled():
        return

    logger.info(
        "Servos are holding position. The PCA9685 keeps outputting this "
        "signal on its own, so it's safe to adjust the mount/horn now even "
        "after this script exits."
    )


if __name__ == "__main__":
    main()
