#!/usr/bin/env python3
"""
Simple test script for motor control.
Defaults to real GPIO (auto-falls back to simulation if RPi.GPIO isn't
available, e.g. on a dev machine). Pass --simulate to force simulation
even on a Pi.

Run a single movement:
    python test_motors.py --forward
    python test_motors.py --backward
    python test_motors.py --left
    python test_motors.py --right
    python test_motors.py --forward --duration 3

Or run the full sequence (forward/backward/left/right/stop) with no
direction flag:
    python test_motors.py
"""

import os
import sys
import time
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motor_control import MotorController

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def run_single(direction: str, duration: float, use_gpio: bool = True):
    """Run one movement for the given duration, then stop."""
    logger.info(f"Running: {direction} for {duration}s (GPIO mode: {use_gpio})")

    motor = MotorController(use_gpio=use_gpio)
    try:
        motor.move(direction, duration=duration)
        logger.info("✓ Done")
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
    finally:
        motor.stop()
        motor.cleanup()


def test_motors(use_gpio: bool = True):
    """Test all motor directions."""
    logger.info(f"Starting motor test (GPIO mode: {use_gpio})...")

    motor = MotorController(use_gpio=use_gpio)

    try:
        movements = [
            ("forward", 2),
            ("backward", 2),
            ("left", 1),
            ("right", 1),
            ("stop", 0),
        ]

        for direction, duration in movements:
            logger.info(f"\nTesting: {direction} for {duration}s")
            motor.move(direction, duration=duration)
            time.sleep(0.5)  # pause between moves

        logger.info("\n✓ All tests completed")

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
    finally:
        motor.cleanup()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Test RC car motors. With a direction flag, runs just that "
                    "movement; with none, runs the full test sequence."
    )
    direction_group = parser.add_mutually_exclusive_group()
    direction_group.add_argument("--forward", action="store_true", help="Drive forward")
    direction_group.add_argument("--backward", action="store_true", help="Drive backward")
    direction_group.add_argument("--left", action="store_true", help="Pivot left")
    direction_group.add_argument("--right", action="store_true", help="Pivot right")
    direction_group.add_argument("--stop", action="store_true", help="Just stop the motors (e.g. to kill a runaway)")
    parser.add_argument(
        "--duration",
        type=float,
        default=2.0,
        help="How long to run the movement, in seconds (default: 2.0)"
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Force simulation mode, even on a Pi with RPi.GPIO available"
    )

    args = parser.parse_args()

    direction = next(
        (d for d in ("forward", "backward", "left", "right", "stop") if getattr(args, d)),
        None,
    )

    if direction:
        run_single(direction, duration=args.duration, use_gpio=not args.simulate)
    else:
        test_motors(use_gpio=not args.simulate)
