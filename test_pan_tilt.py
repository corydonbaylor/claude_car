#!/usr/bin/env python3
"""
Small, cautious pan-tilt servo test.

Per handoff.md: servos only, motors must be fully stopped and untouched
during this test (this script never imports motor_control, so that's
enforced by construction). Movements are small and slow on purpose —
this is a first-contact test, not a full range-of-motion sweep.
"""

import subprocess
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

PAN_CHANNEL = 0
TILT_CHANNEL = 1
CENTER_ANGLE = 90
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


def main():
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

    logger.info("Connected. Centering both servos first.")
    kit.servo[PAN_CHANNEL].angle = CENTER_ANGLE
    kit.servo[TILT_CHANNEL].angle = CENTER_ANGLE
    time.sleep(0.5)

    if not check_throttled():
        return

    # Pan: small nudge one way, back to center
    move_smoothly(kit, PAN_CHANNEL, CENTER_ANGLE, CENTER_ANGLE + SMALL_OFFSET, "pan")
    time.sleep(0.5)
    if not check_throttled():
        return

    move_smoothly(kit, PAN_CHANNEL, CENTER_ANGLE + SMALL_OFFSET, CENTER_ANGLE, "pan")
    time.sleep(0.5)
    if not check_throttled():
        return

    # Tilt: small nudge one way, back to center
    move_smoothly(kit, TILT_CHANNEL, CENTER_ANGLE, CENTER_ANGLE + SMALL_OFFSET, "tilt")
    time.sleep(0.5)
    if not check_throttled():
        return

    move_smoothly(kit, TILT_CHANNEL, CENTER_ANGLE + SMALL_OFFSET, CENTER_ANGLE, "tilt")
    time.sleep(0.5)
    check_throttled()

    logger.info("Done. Both servos back at center.")


if __name__ == "__main__":
    main()
