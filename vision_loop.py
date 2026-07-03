import os
import logging
import sys
from pathlib import Path
from motor_control import MotorController
from camera import Camera
import anthropic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


class VisionControlLoop:
    """Main control loop: capture → Claude vision → decide → move → repeat."""

    def __init__(self, use_gpio: bool = True, headless: bool = False):
        """
        Initialize vision control loop.

        Args:
            use_gpio: If False, runs in simulation mode (no real GPIO)
            headless: If True, doesn't require display for camera preview
        """
        self.motor = MotorController(use_gpio=use_gpio)
        self.camera = Camera()
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.headless = headless

    def get_next_action(self, image_base64: str) -> str:
        """
        Send image to Claude and get next action.

        Args:
            image_base64: Base64-encoded image string

        Returns:
            One of: 'forward', 'backward', 'left', 'right', 'stop'
        """
        try:
            message = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
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
                                    "You are controlling an RC car with a camera. "
                                    "Look at this image and decide what the car should do next. Do not let the car run into anything."
                                    "Respond with ONLY ONE word from this list: "
                                    "forward, backward, left, right, or stop. "
                                    "No explanations, just the word."
                                ),
                            },
                        ],
                    }
                ],
            )

            response_text = message.content[0].text.strip().lower()
            # Extract just the direction word in case Claude adds anything
            for direction in ["forward", "backward", "left", "right", "stop"]:
                if direction in response_text:
                    return direction

            logger.warning(f"Could not parse Claude response: {response_text}")
            return "stop"

        except anthropic.APIError as e:
            logger.error(f"API error: {e}")
            return "stop"

    def run(self, iterations: int = None, duration_per_action: float = 0.5):
        """
        Run the vision control loop.

        Args:
            iterations: Number of action cycles to run. None = infinite.
            duration_per_action: How long to execute each action (seconds)
        """
        logger.info("Starting vision control loop...")
        logger.info(
            f"Configuration: iterations={iterations}, "
            f"duration={duration_per_action}s, headless={self.headless}"
        )

        iteration = 0
        try:
            while iterations is None or iteration < iterations:
                iteration += 1
                logger.info(f"\n--- Iteration {iteration} ---")

                # Capture image
                try:
                    image_path = self.camera.capture_image()
                except FileNotFoundError:
                    logger.info(
                        "Camera not available (not on Pi). Using mock image."
                    )
                    image_path = self.camera.mock_capture()

                # Encode to base64
                image_b64 = self.camera.get_image_base64(image_path)
                logger.info(f"Captured: {image_path.name} ({len(image_b64)} bytes)")

                # Get decision from Claude
                logger.info("Sending to Claude for vision analysis...")
                action = self.get_next_action(image_b64)
                logger.info(f"Claude decided: {action}")

                # Execute action
                logger.info(f"Executing: {action} for {duration_per_action}s")
                self.motor.move(action, duration=duration_per_action)

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
        help="Number of control cycles to run (default: infinite until interrupted)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.5,
        help="Duration (seconds) for each movement action (default: 0.5)",
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

    loop = VisionControlLoop(use_gpio=not args.simulate)
    loop.run(iterations=args.iterations, duration_per_action=args.duration)


if __name__ == "__main__":
    main()
