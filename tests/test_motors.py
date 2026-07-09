#!/usr/bin/env python3
"""
Simple test script for motor control.
Defaults to real GPIO (auto-falls back to simulation if RPi.GPIO isn't
available, e.g. on a dev machine). Pass --simulate to force simulation
even on a Pi.
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

    parser = argparse.ArgumentParser(description="Test RC car motors")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Force simulation mode, even on a Pi with RPi.GPIO available"
    )

    args = parser.parse_args()
    test_motors(use_gpio=not args.simulate)
