import re
import time
import logging
import subprocess

logger = logging.getLogger(__name__)

# GPIO pins (BCM numbering)
# Right side on OUT1/OUT2, Left side on OUT3/OUT4
# IN1 was GPIO18 (physical pin 12) until 2026-07-10; remapped to GPIO23
# (physical pin 16) after GPIO18 spent an extended period sinking a hard
# 5V short from a faulty L298N (internal 5V-to-IN1 bridge). GPIO18 tested
# functional afterward but is retired as a precaution — see handoff.md.
IN1, IN2 = 23, 17   # right side: forward/backward
IN3, IN4 = 22, 27   # left side: forward/backward
ENA, ENB = 25, 24   # enable pins for speed control

PINS = [IN1, IN2, IN3, IN4, ENA, ENB]
PIN_NAMES = {IN1: "IN1", IN2: "IN2", IN3: "IN3", IN4: "IN4", ENA: "ENA", ENB: "ENB"}


def read_hardware_pin_states():
    """
    Read the actual electrical level of the motor pins at the Pi header,
    via `pinctrl` (Bookworm) or `raspi-gpio` (older OS). This measures what
    the pin really is, independent of what RPi.GPIO believes it set —
    added to debug motors running while software commands stop.

    Returns {bcm_pin: (mode, level, drive)} with mode 'op'/'ip', level
    'hi'/'lo' (measured), and drive 'dh'/'dl' (what the SoC is commanding,
    pinctrl only — None otherwise). Returns None if no readback tool is
    available (dev machine).
    """
    try:
        result = subprocess.run(
            ["pinctrl", "get", ",".join(str(p) for p in PINS)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            states = {}
            # e.g. "18: op dh pn | hi // GPIO18 = output" — 'dh'/'dl' is the
            # level the SoC is *commanding*, 'hi'/'lo' after the bar is what
            # the pin actually *measures*. Drive low + measure high = an
            # external source is overpowering the pin.
            for match in re.finditer(
                r"^\s*(\d+):\s+(\w+)(?:\s+(d[hl]))?.*?\|\s+(hi|lo)", result.stdout, re.MULTILINE
            ):
                states[int(match.group(1))] = (match.group(2), match.group(4), match.group(3))
            if states:
                return states
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        result = subprocess.run(
            ["raspi-gpio", "get"] + [str(p) for p in PINS],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            states = {}
            # raspi-gpio can't report commanded drive separately, so that
            # slot is None here.
            for match in re.finditer(r"GPIO (\d+): level=(\d).*?func=(\w+)", result.stdout):
                mode = "op" if match.group(3).upper() == "OUTPUT" else "ip"
                states[int(match.group(1))] = (mode, "hi" if match.group(2) == "1" else "lo", None)
            if states:
                return states
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


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
        self.enabled = True

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
        self.enabled = True
        logger.info("GPIO initialized (pincheck v2, drive-aware)")
        self._log_hardware_pin_states(expected=None)

    def disable(self):
        """
        Drop every L298N pin LOW — direction pins AND both enables — so the
        driver's outputs are fully off (not even braking) while the pan-tilt
        servos are active. Direction commands automatically re-enable.
        """
        if self.use_gpio and self.gpio:
            self.gpio.output(PINS, self.gpio.LOW)
        self.enabled = False
        logger.info("[MOTOR] disabled -> all six L298N pins LOW (outputs off)")
        self._log_hardware_pin_states(
            expected={IN1: False, IN2: False, IN3: False, IN4: False, ENA: False, ENB: False}
        )

    def _ensure_enabled(self):
        """Raise ENA/ENB again before driving, if disable() dropped them."""
        if self.enabled:
            return
        if self.use_gpio and self.gpio:
            self.gpio.output(ENA, self.gpio.HIGH)
            self.gpio.output(ENB, self.gpio.HIGH)
        self.enabled = True
        logger.info("[MOTOR] re-enabled -> ENA/ENB HIGH")

    def _log_hardware_pin_states(self, expected=None):
        """
        Log the measured electrical state of every motor pin, and flag any
        pin whose measured level contradicts what was just commanded.

        expected: optional {bcm_pin: bool} of just-commanded levels. A
        mismatch here means the Pi header itself disagrees with the
        software — if instead all pins read as commanded while a motor
        still runs, the fault is past the header (wiring or L298N).
        """
        states = read_hardware_pin_states()
        if states is None:
            return

        summary = " ".join(
            f"{PIN_NAMES[p]}/GPIO{p}={states[p][1]}({states[p][0]}"
            + (f",{states[p][2]}" if states[p][2] else "") + ")"
            for p in PINS if p in states
        )
        logger.info(f"[PINCHECK] measured at header: {summary}")

        for pin, (mode, level, drive) in states.items():
            if mode != "op":
                logger.warning(
                    f"[PINCHECK] {PIN_NAMES.get(pin, pin)}/GPIO{pin} is in INPUT mode (floating) "
                    "while the controller is active — it should be an output"
                )

        if expected:
            for pin, want_high in expected.items():
                if pin not in states:
                    continue
                mode, level, drive = states[pin]
                if (level == "hi") != bool(want_high):
                    name = PIN_NAMES.get(pin, pin)
                    commanded = "HIGH" if want_high else "LOW"
                    if drive == "dl" and level == "hi":
                        # The SoC is actively driving low yet the pin sits
                        # high: an external source is overpowering the pin.
                        logger.error(
                            f"[PINCHECK] MISMATCH on {name}/GPIO{pin}: SoC is DRIVING LOW (dl) but the "
                            f"pin measures hi — something EXTERNAL is pulling this line up (likely a "
                            f"short to 5V in the wiring; power down and inspect)"
                        )
                    elif drive == "dh" and not want_high:
                        logger.error(
                            f"[PINCHECK] MISMATCH on {name}/GPIO{pin}: software commanded LOW but the "
                            f"SoC's own drive register is HIGH (dh) — the GPIO write is not taking "
                            f"effect (another process/driver owns this pin?)"
                        )
                    else:
                        logger.error(
                            f"[PINCHECK] MISMATCH on {name}/GPIO{pin}: software commanded {commanded} "
                            f"but the pin measures {level}" + (f" (drive={drive})" if drive else "")
                        )

    def _set_pins(self, in1, in2, in3, in4, label=""):
        """
        Set individual motor direction pins (internal helper).

        Logs at INFO (not DEBUG) unconditionally, including on real GPIO —
        this is the one place that knows the actual pin values being
        written, so it's the ground truth for "are the motors moving right
        now." vision_loop.py's logging.basicConfig runs at INFO, so a
        DEBUG-level log here would be silently dropped and this class would
        be a black box from the log output alone.
        """
        logger.info(f"[MOTOR] {label} -> IN1={int(in1)} IN2={int(in2)} IN3={int(in3)} IN4={int(in4)}")

        if not self.use_gpio or not self.gpio:
            return

        self.gpio.output(IN1, in1)
        self.gpio.output(IN2, in2)
        self.gpio.output(IN3, in3)
        self.gpio.output(IN4, in4)

        # Read back what the pins actually measure at the header, so the log
        # shows electrical reality next to the software command.
        self._log_hardware_pin_states(
            expected={IN1: in1, IN2: in2, IN3: in3, IN4: in4,
                      ENA: self.enabled, ENB: self.enabled}
        )

    # NOTE (2026-07-13): the L298N rewiring inverted both motor channels'
    # polarity (motor leads swapped relative to the original build), so the
    # IN pin patterns below are intentionally the reverse of the classic
    # layout — IN2/IN4 now mean "forward". Verified on hardware. If motors
    # are ever rewired again and directions flip, swap these patterns back
    # rather than touching the wiring.

    def forward(self):
        """Drive car forward."""
        self._ensure_enabled()
        self._set_pins(
            self.gpio.LOW if self.use_gpio else False,   # IN1
            self.gpio.HIGH if self.use_gpio else True,   # IN2 - right forward
            self.gpio.LOW if self.use_gpio else False,   # IN3
            self.gpio.HIGH if self.use_gpio else True,   # IN4 - left forward
            label="forward"
        )

    def backward(self):
        """Drive car backward."""
        self._ensure_enabled()
        self._set_pins(
            self.gpio.HIGH if self.use_gpio else True,   # IN1 - right backward
            self.gpio.LOW if self.use_gpio else False,   # IN2
            self.gpio.HIGH if self.use_gpio else True,   # IN3 - left backward
            self.gpio.LOW if self.use_gpio else False,   # IN4
            label="backward"
        )

    def left(self):
        """Pivot left (left side backward, right side forward)."""
        self._ensure_enabled()
        self._set_pins(
            self.gpio.LOW if self.use_gpio else False,   # IN1
            self.gpio.HIGH if self.use_gpio else True,   # IN2 - right forward
            self.gpio.HIGH if self.use_gpio else True,   # IN3 - left backward
            self.gpio.LOW if self.use_gpio else False,   # IN4
            label="left"
        )

    def right(self):
        """Pivot right (right side backward, left side forward)."""
        self._ensure_enabled()
        self._set_pins(
            self.gpio.HIGH if self.use_gpio else True,   # IN1 - right backward
            self.gpio.LOW if self.use_gpio else False,   # IN2
            self.gpio.LOW if self.use_gpio else False,   # IN3
            self.gpio.HIGH if self.use_gpio else True,   # IN4 - left forward
            label="right"
        )

    def stop(self):
        """Stop all motors."""
        self._set_pins(
            self.gpio.LOW if self.use_gpio else False,
            self.gpio.LOW if self.use_gpio else False,
            self.gpio.LOW if self.use_gpio else False,
            self.gpio.LOW if self.use_gpio else False,
            label="stop"
        )

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
