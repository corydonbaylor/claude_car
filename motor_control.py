import time
import logging

logger = logging.getLogger(__name__)

# GPIO pins (BCM numbering)
# Right side on OUT1/OUT2, Left side on OUT3/OUT4
IN1, IN2 = 18, 17   # right side: forward/backward
IN3, IN4 = 22, 27   # left side: forward/backward
ENA, ENB = 25, 24   # enable pins for speed control

PINS = [IN1, IN2, IN3, IN4, ENA, ENB]


class MotorController:
    """Controls RC car movement via L298N motor driver."""

    def __init__(self, use_gpio=True):
        """
        Initialize motor controller.

        Args:
            use_gpio: If True, uses actual GPIO on Raspberry Pi.
                     If False, simulates GPIO for testing on development machines.
        """
        self.use_gpio = use_gpio
        self.gpio = None

        if use_gpio:
            try:
                import RPi.GPIO as GPIO
                self.gpio = GPIO
                self._setup_gpio()
            except ImportError:
                logger.warning("RPi.GPIO not available; running in simulation mode")
                self.use_gpio = False
        else:
            logger.info("Running in GPIO simulation mode")

    def _setup_gpio(self):
        """Configure GPIO pins."""
        if not self.use_gpio or not self.gpio:
            return

        self.gpio.setmode(self.gpio.BCM)
        self.gpio.setup(PINS, self.gpio.OUT)
        # Enable both channels at full speed by default
        self.gpio.output(ENA, self.gpio.HIGH)
        self.gpio.output(ENB, self.gpio.HIGH)
        logger.info("GPIO initialized")

    def _set_pins(self, in1, in2, in3, in4):
        """Set individual motor direction pins (internal helper)."""
        if not self.use_gpio or not self.gpio:
            # Simulation: just log
            state = f"IN1={in1} IN2={in2} IN3={in3} IN4={in4}"
            logger.debug(f"GPIO state: {state}")
            return

        self.gpio.output(IN1, in1)
        self.gpio.output(IN2, in2)
        self.gpio.output(IN3, in3)
        self.gpio.output(IN4, in4)

    def forward(self):
        """Drive car forward."""
        self._set_pins(
            self.gpio.HIGH if self.use_gpio else True,   # IN1
            self.gpio.LOW if self.use_gpio else False,   # IN2
            self.gpio.HIGH if self.use_gpio else True,   # IN3
            self.gpio.LOW if self.use_gpio else False    # IN4
        )
        logger.debug("Moving forward")

    def backward(self):
        """Drive car backward."""
        self._set_pins(
            self.gpio.LOW if self.use_gpio else False,   # IN1
            self.gpio.HIGH if self.use_gpio else True,   # IN2
            self.gpio.LOW if self.use_gpio else False,   # IN3
            self.gpio.HIGH if self.use_gpio else True    # IN4
        )
        logger.debug("Moving backward")

    def left(self):
        """Pivot left (left side backward, right side forward)."""
        self._set_pins(
            self.gpio.HIGH if self.use_gpio else True,   # IN1 - right forward
            self.gpio.LOW if self.use_gpio else False,   # IN2
            self.gpio.LOW if self.use_gpio else False,   # IN3 - left backward
            self.gpio.HIGH if self.use_gpio else True    # IN4
        )
        logger.debug("Turning left")

    def right(self):
        """Pivot right (right side backward, left side forward)."""
        self._set_pins(
            self.gpio.LOW if self.use_gpio else False,   # IN1 - right backward
            self.gpio.HIGH if self.use_gpio else True,   # IN2
            self.gpio.HIGH if self.use_gpio else True,   # IN3 - left forward
            self.gpio.LOW if self.use_gpio else False    # IN4
        )
        logger.debug("Turning right")

    def stop(self):
        """Stop all motors."""
        self._set_pins(
            self.gpio.LOW if self.use_gpio else False,
            self.gpio.LOW if self.use_gpio else False,
            self.gpio.LOW if self.use_gpio else False,
            self.gpio.LOW if self.use_gpio else False
        )
        logger.debug("Stopped")

    def move(self, direction, duration=0.5):
        """
        Execute a movement command for a given duration.

        Args:
            direction: One of 'forward', 'backward', 'left', 'right', 'stop'
            duration: How long to move (seconds)
        """
        direction = direction.lower().strip()

        if direction == 'forward':
            self.forward()
        elif direction == 'backward':
            self.backward()
        elif direction == 'left':
            self.left()
        elif direction == 'right':
            self.right()
        elif direction == 'stop':
            self.stop()
        else:
            logger.warning(f"Unknown direction: {direction}")
            self.stop()
            return

        if duration > 0:
            time.sleep(duration)
            self.stop()

    def cleanup(self):
        """Clean up GPIO."""
        if self.use_gpio and self.gpio:
            self.gpio.cleanup()
            logger.info("GPIO cleaned up")
