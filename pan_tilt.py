import logging

logger = logging.getLogger(__name__)

PAN_CHANNEL = 0
TILT_CHANNEL = 1

# Calibrated on hardware 2026-07-09: for both axes, 0=left/down, 180=right/up,
# 90=forward. See handoff.md before changing these.
PAN_FORWARD = 90
TILT_FORWARD = 90


class PanTilt:
    """
    Controls the Arducam pan-tilt mount (PCA9685, I2C 0x40) that the camera
    is mounted on. Simulation mode auto-fallback if the servo board isn't
    reachable, mirroring MotorController's use_gpio pattern.

    Per handoff.md's hard rule, callers are responsible for never moving
    these servos while the drive motors are active, and for settling
    (>=500ms) after a servo move before touching the motors, and vice versa
    — this class only knows about the servos.
    """

    def __init__(self, use_gpio: bool = True):
        self.use_gpio = use_gpio
        self.kit = None

        if use_gpio:
            try:
                from adafruit_servokit import ServoKit
                self.kit = ServoKit(channels=16)
            except ImportError:
                logger.warning("adafruit-circuitpython-servokit not available; running pan-tilt in simulation mode")
                self.use_gpio = False
            except Exception as e:
                logger.warning(f"Could not connect to pan-tilt servo board ({e}); running pan-tilt in simulation mode")
                self.use_gpio = False
        else:
            logger.info("Running pan-tilt in simulation mode")

        self.pan_angle = PAN_FORWARD
        self.tilt_angle = TILT_FORWARD

    def set_pan(self, angle: float):
        angle = max(0, min(180, angle))
        if self.use_gpio and self.kit:
            self.kit.servo[PAN_CHANNEL].angle = angle
        self.pan_angle = angle
        logger.debug(f"Pan set to {angle}")

    def set_tilt(self, angle: float):
        angle = max(0, min(180, angle))
        if self.use_gpio and self.kit:
            self.kit.servo[TILT_CHANNEL].angle = angle
        self.tilt_angle = angle
        logger.debug(f"Tilt set to {angle}")

    def center(self):
        """Return both axes to forward-facing."""
        self.set_pan(PAN_FORWARD)
        self.set_tilt(TILT_FORWARD)

    def cleanup(self):
        """No GPIO handle to release — the PCA9685 holds its last position in hardware."""
        pass
