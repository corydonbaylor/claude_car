#!/usr/bin/env python3
"""
Simple test script for motor control.
Run in simulation mode on dev machines, GPIO mode on Raspberry Pi.
"""

import time
import logging
from motor_control import MotorController

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def test_motors(use_gpio: bool = False):
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
        "--gpio",
        action="store_true",
        help="Use real GPIO (requires Raspberry Pi)"
    )

    args = parser.parse_args()
    test_motors(use_gpio=args.gpio)
