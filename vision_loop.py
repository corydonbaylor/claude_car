import os
import time
import logging
import sys
from enum import Enum

from motor_control import MotorController
from camera import Camera
from reflexes import ReflexEngine
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
    pan-tilt camera mount.

    - SEARCHING: the car stays fully stopped. The pan-tilt sweeps across a
      fixed set of angles, capturing a frame and asking Claude "is the shoe
      here?" at each one. If the full sweep comes up empty, the pan-tilt is
      re-centered and settled, the car body pivots to a new heading, and
      the sweep repeats from there.
    - ALIGNING: once the shoe is found at some pan angle, the camera is
      re-centered to forward first (and settled), then the car body turns
      to face the direction the shoe was found in.
    - APPROACHING: with the shoe roughly dead ahead, the car drives forward
      continuously — OpenCV reflexes watch every tick for close obstacles —
      while periodically stopping just long enough for a clean Claude
      recheck that the shoe is still visible and centered. If it's lost,
      control returns to SEARCHING.

    Motors and the pan-tilt servos are never actuated at the same time —
    see the hard rule in handoff.md. self.servo_motor_settle_time is the
    minimum pause enforced at every handoff between the two subsystems.
    """

    def __init__(self, use_gpio: bool = True,
                 reasoning_interval: float = 2.0,
                 reflex_interval: float = 0.3,
                 capture_settle_time: float = 0.4,
                 pan_sweep_angles=None,
                 pan_settle_time: float = 0.3,
                 servo_motor_settle_time: float = 0.5,
                 search_turn_duration: float = 0.6,
                 body_turn_seconds_per_degree: float = 0.02,
                 max_align_turn_duration: float = 1.5,
                 align_deadband_degrees: float = 10.0):
        """
        Args:
            use_gpio: If False, runs in simulation mode (no real GPIO/servos)
            reasoning_interval: Seconds between Claude rechecks while approaching
            reflex_interval: Seconds between reflex/motor ticks while approaching
            capture_settle_time: Seconds to hold the car still before a
                reasoning recheck captures a frame, so it isn't motion-blurred
            pan_sweep_angles: Pan angles (degrees) to check during a search
                sweep, in order. Defaults to 5 positions from 30 to 150.
            pan_settle_time: Seconds to wait after a pan move before
                capturing, so the servo has physically reached the angle
            servo_motor_settle_time: Minimum seconds to pause at every
                handoff between servos and motors (hard rule; don't go
                below 500ms — see handoff.md)
            search_turn_duration: Seconds to pivot the car body when a full
                pan sweep finds nothing, before sweeping again
            body_turn_seconds_per_degree: Rough estimate of how long the car
                needs to pivot per degree of pan offset when aligning to a
                found target. This is a hardware guess — tune on the car.
            max_align_turn_duration: Cap on how long a single align turn
                can run, regardless of the computed pan offset
            align_deadband_degrees: If the shoe was found within this many
                degrees of forward pan, skip the body turn entirely
        """
        self.motor = MotorController(use_gpio=use_gpio)
        self.camera = Camera()
        self.reflex = ReflexEngine()
        self.pan_tilt = PanTilt(use_gpio=use_gpio)
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

        self.reasoning_interval = reasoning_interval
        self.reflex_interval = reflex_interval
        self.capture_settle_time = capture_settle_time

        self.pan_sweep_angles = pan_sweep_angles or [30, 60, 90, 120, 150]
        self.pan_settle_time = pan_settle_time
        self.servo_motor_settle_time = max(servo_motor_settle_time, 0.5)
        self.search_turn_duration = search_turn_duration
        self.body_turn_seconds_per_degree = body_turn_seconds_per_degree
        self.max_align_turn_duration = max_align_turn_duration
        self.align_deadband_degrees = align_deadband_degrees

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

    def _drive(self, action: str):
        if action == "forward":
            self.motor.forward()
        elif action == "backward":
            self.motor.backward()
        elif action == "left":
            self.motor.left()
        elif action == "right":
            self.motor.right()
        else:
            self.motor.stop()

    # -- SEARCHING ------------------------------------------------------

    def _search_sweep(self):
        """
        Sweep the pan-tilt across self.pan_sweep_angles while the car stays
        fully stopped, checking each frame with Claude.

        Returns the pan angle (degrees) the shoe was found at, or None if
        the full sweep came up empty (or the iteration budget ran out).
        """
        self.motor.stop()

        for angle in self.pan_sweep_angles:
            if not self._consume_tick():
                return None

            self.pan_tilt.set_pan(angle)
            time.sleep(self.pan_settle_time)

            image_path = self._capture()
            image_b64 = self.camera.get_image_base64(image_path)

            logger.info(f"[Search] checking pan={angle}°...")
            found, position, _ = self._observe(image_b64)

            if found:
                logger.info(f"[Search] shoe found at pan={angle}° (position in frame: {position})")
                return angle

        logger.info("[Search] full sweep found nothing")
        return None

    def _rotate_and_continue_search(self):
        """
        Full sweep came up empty. Re-center the pan-tilt, settle, pivot the
        car body to a new heading, settle again, then let the next loop
        pass re-sweep from there.
        """
        self.pan_tilt.center()
        time.sleep(self.pan_settle_time)
        time.sleep(self.servo_motor_settle_time)

        logger.info(f"[Search] rotating body to search a new heading ({self.search_turn_duration}s turn)")
        self.motor.move("left", duration=self.search_turn_duration)

        time.sleep(self.servo_motor_settle_time)

    # -- ALIGNING ---------------------------------------------------------

    def _align_to_target(self, found_pan_angle: float):
        """
        Re-center the camera to forward first, then turn the car body to
        face the direction the shoe was found in.
        """
        logger.info("[Align] re-centering camera to forward")
        self.pan_tilt.center()
        time.sleep(self.pan_settle_time)
        time.sleep(self.servo_motor_settle_time)

        offset = found_pan_angle - PAN_FORWARD  # negative = shoe was left, positive = right

        if abs(offset) <= self.align_deadband_degrees:
            logger.info(f"[Align] pan offset {offset}° within deadband, no body turn needed")
        else:
            direction = "left" if offset < 0 else "right"
            turn_duration = min(abs(offset) * self.body_turn_seconds_per_degree, self.max_align_turn_duration)
            logger.info(f"[Align] turning {direction} for {turn_duration:.2f}s to face target (pan offset {offset}°)")
            self.motor.move(direction, duration=turn_duration)

        time.sleep(self.servo_motor_settle_time)

    # -- APPROACHING ------------------------------------------------------

    def _approach_loop(self) -> bool:
        """
        Drive toward the shoe: reflex ticks keep the car moving and dodge
        obstacles, while periodic clean captures ask Claude to confirm the
        shoe is still visible and steer toward it.

        Returns False once the shoe is lost (or the iteration budget runs
        out) so the caller can fall back to SEARCHING.
        """
        current_action = "forward"
        last_check = 0.0

        while True:
            if not self._consume_tick():
                self.motor.stop()
                return False

            now = time.time()
            if now - last_check >= self.reasoning_interval:
                self.motor.stop()
                time.sleep(self.capture_settle_time)

                image_path = self._capture()
                image_b64 = self.camera.get_image_base64(image_path)
                found, position, _ = self._observe(image_b64)
                last_check = time.time()

                if not found:
                    logger.info("[Approach] lost sight of the shoe, returning to search")
                    self.motor.stop()
                    return False

                if position == "center":
                    current_action = "forward"
                elif position == "left":
                    current_action = "left"
                elif position == "right":
                    current_action = "right"
                else:
                    current_action = "stop"
            else:
                image_path = self._capture()
                reflex_result = self.reflex.check(image_path)

                if reflex_result.blocked:
                    logger.info(
                        f"[Reflex] obstacle ahead, evading {reflex_result.direction} "
                        f"(densities={reflex_result.edge_densities})"
                    )
                    self.motor.move(reflex_result.direction, duration=0.3)
                else:
                    self._drive(current_action)

                time.sleep(self.reflex_interval)

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
                    else:
                        self._rotate_and_continue_search()

                elif self.state == State.ALIGNING:
                    self._align_to_target(self.found_pan_angle)
                    self.state = State.APPROACHING

                elif self.state == State.APPROACHING:
                    still_tracking = self._approach_loop()
                    if not still_tracking:
                        self.state = State.SEARCHING

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
        "--reasoning-interval",
        type=float,
        default=2.0,
        help="Seconds between Claude rechecks while approaching (default: 2.0)",
    )
    parser.add_argument(
        "--reflex-interval",
        type=float,
        default=0.3,
        help="Seconds between reflex/motor ticks while approaching (default: 0.3)",
    )
    parser.add_argument(
        "--capture-settle",
        type=float,
        default=0.4,
        help="Seconds to hold the car still before each reasoning recheck, to avoid motion blur (default: 0.4)",
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
        reasoning_interval=args.reasoning_interval,
        reflex_interval=args.reflex_interval,
        capture_settle_time=args.capture_settle,
        pan_settle_time=args.pan_settle,
        servo_motor_settle_time=args.servo_motor_settle,
    )
    loop.run(iterations=args.iterations)


if __name__ == "__main__":
    main()
