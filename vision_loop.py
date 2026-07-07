import os
import logging
import sys
import threading
from motor_control import MotorController
from camera import Camera
from reflexes import ReflexEngine
import anthropic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


class VisionControlLoop:
    """
    Continuous control loop split across two threads:

    - Reflex loop (fast, no API calls): the only thread that touches the
      camera. Captures frames on a short tick, checks for an obstacle
      directly ahead via OpenCV, and either evades it or keeps driving
      continuously in the current target direction. No stop-start between
      ticks — this is what removes the jerkiness of the old capture ->
      Claude -> move -> stop cycle.
    - Reasoning loop (slow, calls Claude): periodically reads the most
      recently captured frame and asks Claude to reason about the goal
      (e.g. navigating toward a roll of duct tape), then updates the
      target direction the reflex loop drives toward.
    """

    def __init__(self, use_gpio: bool = True, headless: bool = False,
                 reasoning_interval: float = 2.0, reflex_interval: float = 0.3):
        """
        Initialize vision control loop.

        Args:
            use_gpio: If False, runs in simulation mode (no real GPIO)
            headless: If True, doesn't require display for camera preview
            reasoning_interval: Seconds between Claude reasoning calls
            reflex_interval: Seconds between reflex/motor ticks
        """
        self.motor = MotorController(use_gpio=use_gpio)
        self.camera = Camera()
        self.reflex = ReflexEngine()
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.headless = headless
        self.reasoning_interval = reasoning_interval
        self.reflex_interval = reflex_interval

        self.current_action = "stop"
        self.action_lock = threading.Lock()

        self.latest_image_path = None
        self.image_lock = threading.Lock()

        self.stop_event = threading.Event()

    def _capture(self):
        try:
            return self.camera.capture_image()
        except FileNotFoundError:
            return self.camera.mock_capture()

    def get_next_action(self, image_base64: str) -> str:
        """
        Send image to Claude and get next action.

        Claude only reports structured observations (found? where? what's
        visible) — it does NOT pick the direction itself. The direction is
        derived deterministically in _decide_action(). This prevents Claude
        from guessing a direction (e.g. 'forward') when the target isn't
        actually in frame, and enforces a clear search -> approach state
        machine: turn to search while not found, keep turning toward the
        target until it's centered, then drive forward.

        Args:
            image_base64: Base64-encoded image string

        Returns:
            One of: 'forward', 'left', 'right', 'stop'
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
                                    "data": image_base64,
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

            return self._decide_action(found, position)

        except anthropic.APIError as e:
            logger.error(f"API error: {e}")
            return "stop"
        except Exception as e:
            logger.error(f"Error getting next action: {e}", exc_info=True)
            return "stop"

    def _decide_action(self, found: bool, position: str) -> str:
        """
        Deterministically map Claude's structured observation to a motor
        action. Claude never picks the direction directly, so it can't
        drive forward on a hunch when the target isn't actually visible.
        """
        if not found:
            return "left"  # search by turning; a fixed direction avoids oscillation

        if position == "center":
            return "forward"
        elif position == "left":
            return "left"
        elif position == "right":
            return "right"
        else:
            return "stop"

    def _drive(self, action: str):
        """Continuously execute an action without blocking or auto-stopping."""
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

    def _reflex_loop(self):
        """Fast loop: owns the camera, drives motors continuously, evades obstacles."""
        while not self.stop_event.is_set():
            image_path = self._capture()
            with self.image_lock:
                self.latest_image_path = image_path

            reflex_result = self.reflex.check(image_path)

            if reflex_result.blocked:
                logger.info(
                    f"[Reflex] obstacle ahead, evading {reflex_result.direction} "
                    f"(densities={reflex_result.edge_densities})"
                )
                self.motor.move(reflex_result.direction, duration=0.3)
            else:
                with self.action_lock:
                    action = self.current_action
                self._drive(action)

            self.stop_event.wait(self.reflex_interval)

    def _reasoning_loop(self, iterations):
        """Slow loop: asks Claude for the target direction toward the goal."""
        count = 0
        while not self.stop_event.is_set():
            if iterations is not None and count >= iterations:
                logger.info("Reasoning iteration limit reached, stopping.")
                self.stop_event.set()
                break

            with self.image_lock:
                image_path = self.latest_image_path

            if image_path is None:
                self.stop_event.wait(0.1)
                continue

            count += 1
            try:
                image_b64 = self.camera.get_image_base64(image_path)

                logger.info(f"[Reasoning #{count}] Sending frame to Claude...")
                action = self.get_next_action(image_b64)
                logger.info(f"[Reasoning #{count}] Claude decided: {action}")

                with self.action_lock:
                    self.current_action = action
            except Exception as e:
                logger.error(f"[Reasoning #{count}] Unexpected error: {e}", exc_info=True)

            self.stop_event.wait(self.reasoning_interval)

    def run(self, iterations: int = None, duration_per_action: float = 0.5):
        """
        Run the continuous vision control loop.

        Args:
            iterations: Number of Claude reasoning cycles to run. None = infinite.
            duration_per_action: Unused directly; kept for CLI backward compatibility.
        """
        logger.info("Starting continuous vision control loop...")
        logger.info(
            f"Configuration: iterations={iterations}, "
            f"reasoning_interval={self.reasoning_interval}s, "
            f"reflex_interval={self.reflex_interval}s"
        )

        reasoning_thread = threading.Thread(
            target=self._reasoning_loop, args=(iterations,), daemon=True
        )
        reflex_thread = threading.Thread(target=self._reflex_loop, daemon=True)

        try:
            reflex_thread.start()
            reasoning_thread.start()

            while reasoning_thread.is_alive() or reflex_thread.is_alive():
                reasoning_thread.join(timeout=0.2)
                reflex_thread.join(timeout=0.2)

        except KeyboardInterrupt:
            logger.info("\nInterrupt received, stopping...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
        finally:
            self.stop_event.set()
            reasoning_thread.join(timeout=2)
            reflex_thread.join(timeout=2)
            self.cleanup()

    def cleanup(self):
        """Clean up resources."""
        logger.info("Cleaning up...")
        self.motor.stop()
        self.motor.cleanup()
        self.camera.cleanup()
        logger.info("Done.")


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="RC car vision control loop powered by Claude"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Number of Claude reasoning cycles to run (default: infinite until interrupted)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.5,
        help="Unused directly; kept for backward compatibility",
    )
    parser.add_argument(
        "--reasoning-interval",
        type=float,
        default=2.0,
        help="Seconds between Claude reasoning calls (default: 2.0)",
    )
    parser.add_argument(
        "--reflex-interval",
        type=float,
        default=0.3,
        help="Seconds between reflex/motor ticks (default: 0.3)",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run in simulation mode (no GPIO, mock camera)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var)",
    )

    args = parser.parse_args()

    # Set API key if provided
    if args.api_key:
        os.environ["ANTHROPIC_API_KEY"] = args.api_key

    # Validate API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)

    loop = VisionControlLoop(
        use_gpio=not args.simulate,
        reasoning_interval=args.reasoning_interval,
        reflex_interval=args.reflex_interval,
    )
    loop.run(iterations=args.iterations, duration_per_action=args.duration)


if __name__ == "__main__":
    main()
