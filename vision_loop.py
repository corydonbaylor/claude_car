import os
import time
import logging
import sys
from enum import Enum

from motor_control import MotorController
from camera import Camera
from pan_tilt import PanTilt, PAN_FORWARD
import anthropic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


class State(Enum):
    SEARCHING = "searching"
    ALIGNING = "aligning"
    APPROACHING = "approaching"


class VisionControlLoop:
    """
    Claude-controlled search-and-approach loop for a shoe, built around a
    pan-tilt camera mount. Exactly three modes:

    - SEARCHING: the L298N direction pins are held LOW (car fully stopped)
      for the entire mode — nothing here ever moves the drive motors. The
      pan-tilt sweeps its five fixed angles, capturing a frame and asking
      Claude "is the shoe here?" at each one. If none of the five show the
      shoe, the sweep just repeats from the first angle. As soon as one
      does, SEARCHING exits immediately for ALIGNING.
    - ALIGNING: the camera is re-centered to forward first, then the car
      body turns toward the direction the shoe was found in. After turning,
      it takes a fresh photo — if the shoe is still in frame, move on to
      APPROACHING; if not, go back to SEARCHING.
    - APPROACHING: drive straight forward until interrupted.

    Motors and the pan-tilt servos are never actuated at the same time —
    see the hard rule in handoff.md. Every pan-tilt move goes through
    _move_pan/_center_camera, which force the motors stopped and settled
    immediately beforehand, unconditionally.
    """

    def __init__(self, use_gpio: bool = True,
                 pan_sweep_angles=None,
                 pan_settle_time: float = 0.3,
                 servo_motor_settle_time: float = 0.5,
                 capture_settle_time: float = 0.4,
                 body_turn_seconds_per_degree: float = 0.02,
                 max_align_turn_duration: float = 1.5,
                 align_deadband_degrees: float = 10.0,
                 approach_tick_interval: float = 0.3):
        """
        Args:
            use_gpio: If False, runs in simulation mode (no real GPIO/servos)
            pan_sweep_angles: Pan angles (degrees) to check during a search
                sweep, in order. Defaults to 5 positions from 30 to 150.
            pan_settle_time: Seconds to wait after a pan move before
                capturing, so the servo has physically reached the angle
            servo_motor_settle_time: Minimum seconds to pause at every
                handoff between servos and motors (hard rule; don't go
                below 500ms — see handoff.md)
            capture_settle_time: Seconds to pause after the align turn
                before taking the confirmation photo, so it isn't
                motion-blurred
            body_turn_seconds_per_degree: Rough estimate of how long the car
                needs to pivot per degree of pan offset when aligning to a
                found target. This is a hardware guess — tune on the car.
            max_align_turn_duration: Cap on how long a single align turn
                can run, regardless of the computed pan offset
            align_deadband_degrees: If the shoe was found within this many
                degrees of forward pan, skip the body turn entirely
            approach_tick_interval: Seconds between forward-drive ticks
                while approaching
        """
        self.motor = MotorController(use_gpio=use_gpio)
        self.camera = Camera()
        self.pan_tilt = PanTilt(use_gpio=use_gpio)
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

        self.pan_sweep_angles = pan_sweep_angles or [30, 60, 90, 120, 150]
        self.pan_settle_time = pan_settle_time
        self.servo_motor_settle_time = max(servo_motor_settle_time, 0.5)
        self.capture_settle_time = capture_settle_time
        self.body_turn_seconds_per_degree = body_turn_seconds_per_degree
        self.max_align_turn_duration = max_align_turn_duration
        self.align_deadband_degrees = align_deadband_degrees
        self.approach_tick_interval = approach_tick_interval

        self.state = State.SEARCHING
        self.found_pan_angle = None
        self._remaining_iterations = None

    def _capture(self):
        try:
            return self.camera.capture_image()
        except FileNotFoundError:
            return self.camera.mock_capture()

    def _consume_tick(self) -> bool:
        """
        Call before each action point that should count against
        --iterations. Returns False once the budget is exhausted; True if
        there's budget left (or the run is unbounded).
        """
        if self._remaining_iterations is None:
            return True
        if self._remaining_iterations <= 0:
            return False
        self._remaining_iterations -= 1
        return True

    def _observe(self, image_b64: str):
        """
        Ask Claude whether the shoe is visible in this frame.

        Claude only reports structured observations — it does not decide
        any movement itself. Returns (found, position, seen_text).
        """
        try:
            message = self.client.messages.create(
                model="claude-sonnet-5",
                max_tokens=150,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": (
                                    "You are the vision system for an RC car searching for a shoe. "
                                    "Look at this image and report what you observe — do not decide "
                                    "any movement yourself.\n\n"
                                    "Respond in exactly this format, three lines:\n"
                                    "FOUND: <yes|no>\n"
                                    "POSITION: <left|center|right|none>\n"
                                    "SEEN: <one short sentence describing what's in the frame>\n\n"
                                    "FOUND is 'yes' only if a shoe is clearly visible somewhere in the "
                                    "frame. POSITION is 'none' if FOUND is 'no'; otherwise it's 'left' if "
                                    "the shoe is in the left portion of the frame, 'right' if in the right "
                                    "portion, or 'center' if roughly in the middle."
                                ),
                            },
                        ],
                    }
                ],
            )

            # Sonnet may return non-text blocks (e.g. ThinkingBlock) before the
            # text block, so find the text block explicitly rather than
            # assuming content[0] is it.
            text_blocks = [block.text for block in message.content if block.type == "text"]
            response_text = "\n".join(text_blocks).strip()

            found = False
            position = "none"
            seen_text = None

            for line in response_text.splitlines():
                line_lower = line.strip().lower()
                if line_lower.startswith("found:"):
                    found = "yes" in line_lower
                elif line_lower.startswith("position:"):
                    position = line.split(":", 1)[1].strip().lower()
                elif line_lower.startswith("seen:"):
                    seen_text = line.split(":", 1)[1].strip()

            if seen_text:
                logger.info(f"[Claude sees] {seen_text}")
            logger.info(f"[Claude reports] found={found}, position={position}")

            return found, position, seen_text

        except anthropic.APIError as e:
            logger.error(f"API error: {e}")
            return False, "none", None
        except Exception as e:
            logger.error(f"Error observing frame: {e}", exc_info=True)
            return False, "none", None

    # -- servo/motor guard --------------------------------------------------

    def _move_pan(self, angle: float):
        """
        Point the camera to a pan angle.

        Hard rule, no exceptions: motors are force-stopped and settled
        immediately before every single pan-tilt move, regardless of what
        the caller thinks the motor state already is. This is the only
        place in the codebase allowed to call self.pan_tilt.set_pan, so
        that guarantee can't be bypassed by a call site forgetting the
        stop/settle sequence.
        """
        self.motor.stop()
        time.sleep(self.servo_motor_settle_time)
        self.pan_tilt.set_pan(angle)
        time.sleep(self.pan_settle_time)

    def _center_camera(self):
        """Same hard-rule guard as _move_pan, for returning to forward."""
        self.motor.stop()
        time.sleep(self.servo_motor_settle_time)
        self.pan_tilt.center()
        time.sleep(self.pan_settle_time)

    # -- SEARCHING ------------------------------------------------------

    def _search_sweep(self):
        """
        Sweep the pan-tilt across self.pan_sweep_angles. The drive motors
        are never touched here except to hold them stopped (via _move_pan's
        guard) — the car does not move at all during search.

        Returns the pan angle (degrees) the shoe was found at, or None if
        the full sweep came up empty (or the iteration budget ran out) —
        the caller just calls this again to keep sweeping.
        """
        for angle in self.pan_sweep_angles:
            if not self._consume_tick():
                return None

            self._move_pan(angle)

            image_path = self._capture()
            image_b64 = self.camera.get_image_base64(image_path)

            logger.info(f"[Search] checking pan={angle}°...")
            found, position, _ = self._observe(image_b64)

            if found:
                logger.info(f"[Search] shoe found at pan={angle}° (position in frame: {position})")
                return angle

        logger.info("[Search] full sweep found nothing, sweeping again")
        return None

    # -- ALIGNING ---------------------------------------------------------

    def _align_to_target(self, found_pan_angle: float) -> bool:
        """
        Re-center the camera to forward, turn the car body toward the
        direction the shoe was found in, then take a fresh photo to confirm
        the shoe is still in frame.

        Returns True if the shoe is confirmed in frame (ready to approach),
        False if it's not (caller should go back to SEARCHING).
        """
        logger.info("[Align] re-centering camera to forward")
        self._center_camera()

        offset = found_pan_angle - PAN_FORWARD  # negative = shoe was left, positive = right

        if abs(offset) <= self.align_deadband_degrees:
            logger.info(f"[Align] pan offset {offset}° within deadband, no body turn needed")
        else:
            direction = "left" if offset < 0 else "right"
            turn_duration = min(abs(offset) * self.body_turn_seconds_per_degree, self.max_align_turn_duration)
            logger.info(f"[Align] turning {direction} for {turn_duration:.2f}s to face target (pan offset {offset}°)")
            self.motor.move(direction, duration=turn_duration)  # blocks, then stops itself

        time.sleep(self.capture_settle_time)

        logger.info("[Align] checking whether the shoe is still in frame after turning")
        image_path = self._capture()
        image_b64 = self.camera.get_image_base64(image_path)
        found, position, _ = self._observe(image_b64)

        if found:
            logger.info(f"[Align] shoe confirmed in frame (position={position}), moving to approach")
        else:
            logger.info("[Align] shoe not in frame after turning, returning to search")

        return found

    # -- APPROACHING ------------------------------------------------------

    def _approach(self):
        """Drive straight forward toward the shoe until interrupted (Ctrl+C or --iterations budget)."""
        logger.info("[Approach] driving forward")
        while self._consume_tick():
            self.motor.forward()
            time.sleep(self.approach_tick_interval)
        self.motor.stop()

    # -- main loop --------------------------------------------------------

    def run(self, iterations: int = None):
        """
        Run the search -> align -> approach state machine.

        Args:
            iterations: Number of Claude/action ticks to run in total.
                None = infinite (Ctrl+C to stop).
        """
        logger.info("Starting vision control loop...")
        logger.info(f"Configuration: iterations={iterations}, pan_sweep_angles={self.pan_sweep_angles}")

        self._remaining_iterations = iterations

        try:
            while True:
                if self._remaining_iterations is not None and self._remaining_iterations <= 0:
                    logger.info("Iteration budget exhausted, stopping.")
                    break

                if self.state == State.SEARCHING:
                    found_angle = self._search_sweep()
                    if found_angle is not None:
                        self.found_pan_angle = found_angle
                        self.state = State.ALIGNING

                elif self.state == State.ALIGNING:
                    shoe_in_frame = self._align_to_target(self.found_pan_angle)
                    self.state = State.APPROACHING if shoe_in_frame else State.SEARCHING

                elif self.state == State.APPROACHING:
                    self._approach()

        except KeyboardInterrupt:
            logger.info("\nInterrupt received, stopping...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up resources."""
        logger.info("Cleaning up...")
        self.motor.stop()
        self.motor.cleanup()
        time.sleep(self.servo_motor_settle_time)
        self.pan_tilt.center()
        self.pan_tilt.cleanup()
        self.camera.cleanup()
        logger.info("Done.")


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="RC car pan-tilt search-and-approach loop powered by Claude"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Number of Claude/action ticks to run in total (default: infinite until interrupted)",
    )
    parser.add_argument(
        "--pan-settle",
        type=float,
        default=0.3,
        help="Seconds to wait after a pan move before capturing (default: 0.3)",
    )
    parser.add_argument(
        "--servo-motor-settle",
        type=float,
        default=0.5,
        help="Minimum seconds to pause at every servo/motor handoff (default: 0.5, don't go lower)",
    )
    parser.add_argument(
        "--capture-settle",
        type=float,
        default=0.4,
        help="Seconds to pause after the align turn before the confirmation photo (default: 0.4)",
    )
    parser.add_argument(
        "--approach-tick",
        type=float,
        default=0.3,
        help="Seconds between forward-drive ticks while approaching (default: 0.3)",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run in simulation mode (no GPIO, no servo board, mock camera)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var)",
    )

    args = parser.parse_args()

    if args.api_key:
        os.environ["ANTHROPIC_API_KEY"] = args.api_key

    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)

    loop = VisionControlLoop(
        use_gpio=not args.simulate,
        pan_settle_time=args.pan_settle,
        servo_motor_settle_time=args.servo_motor_settle,
        capture_settle_time=args.capture_settle,
        approach_tick_interval=args.approach_tick,
    )
    loop.run(iterations=args.iterations)


if __name__ == "__main__":
    main()
