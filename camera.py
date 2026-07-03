import subprocess
import logging
import base64
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class Camera:
    """Captures images from Arducam camera on Raspberry Pi."""

    def __init__(self, output_dir: str = "./captures"):
        """
        Initialize camera.

        Args:
            output_dir: Directory to save captured images
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.last_image_path = None

    def capture_image(self, filename: str = None) -> Path:
        """
        Capture image using rpicam-still (Raspberry Pi camera stack).

        Args:
            filename: Optional custom filename. If None, uses timestamp.

        Returns:
            Path to saved image file

        Raises:
            RuntimeError: If rpicam-still is not available or capture fails
            FileNotFoundError: If running on non-Pi hardware
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"capture_{timestamp}.jpg"

        output_path = self.output_dir / filename

        try:
            # rpicam-still is the recommended camera tool for Raspberry Pi OS (bullseye+)
            # Falls back to raspistill for older systems
            cmd = [
                "rpicam-still",
                "-o", str(output_path),
                "-t", "1000",  # timeout: 1 second
                "--width", "640",
                "--height", "480",
                "-n",  # don't display preview
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                # Fallback to old raspistill
                logger.info("rpicam-still not found, trying raspistill...")
                cmd = [
                    "raspistill",
                    "-o", str(output_path),
                    "-t", "1000",
                    "-w", "640",
                    "-h", "480",
                    "-n",
                ]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                if result.returncode != 0:
                    raise RuntimeError(
                        f"Camera capture failed: {result.stderr}"
                    )

            self.last_image_path = output_path
            logger.info(f"Captured image: {output_path}")
            return output_path

        except FileNotFoundError:
            raise FileNotFoundError(
                "Camera tools not found. Are you running on a Raspberry Pi? "
                "On dev machines, use mock_capture() instead."
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("Camera capture timed out")

    def get_image_base64(self, image_path: Path = None) -> str:
        """
        Read image and encode as base64 for API transmission.

        Args:
            image_path: Path to image file. If None, uses last captured image.

        Returns:
            Base64-encoded image string
        """
        if image_path is None:
            image_path = self.last_image_path

        if image_path is None:
            raise ValueError("No image path provided and no previous capture")

        with open(image_path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")

    def mock_capture(self, filename: str = None) -> Path:
        """
        Create a placeholder image for testing on non-Pi hardware.

        Args:
            filename: Optional custom filename.

        Returns:
            Path to placeholder image
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"mock_capture_{timestamp}.jpg"

        output_path = self.output_dir / filename

        # Create minimal valid JPEG (1x1 pixel)
        minimal_jpeg = bytes.fromhex(
            "FFD8FFE000104A46494600010100000100010000FFDB"
            "43000301020003020203030303040304050508050505"
            "050509090808090A0C140D0C0B0B0C1912130F141D1A"
            "1F1E1D1A1C1C20242E2720222C231C1C2837292C30313"
            "4341F27393D38323C2E333432FFC90008010101110011"
            "FFC4001F0000010501010101010100000000000000000"
            "102030405060708090A0BFFC400B51010020102040403"
            "0705040404000102771803020100041105122131410613"
            "516107227114328191A1082342B1C11552D1F02433627"
            "282090A161718191A25262728292A3435363738393A4"
            "34445464748494A535455565758595A636465666768696"
            "A737475767778797A838485868788898A92939495969"
            "798999AA2A3A4A5A6A7A8A9AAB2B3B4B5B6B7B8B9BAC2"
            "C3C4C5C6C7C8C9CAD2D3D4D5D6D7D8D9DAE1E2E3E4E5E6"
            "E7E8E9EAF0F1F2F3F4F5F6F7F8F9FAFFC4001F11030102"
            "050402040400010002110203041105122131061341515"
            "107227228108144291A1B1C109233352F0156272D10A1"
            "6171819A25262728292A35363738393A434445464748494"
            "A535455565758595A636465666768696A7374757677787"
            "9A82838485868788898A92939495969798999AA2A3A4A5"
            "A6A7A8A9AAB2B3B4B5B6B7B8B9BAC2C3C4C5C6C7C8C9CAD"
            "2D3D4D5D6D7D8D9DAE2E3E4E5E6E7E8E9EAF1F2F3F4F5F6"
            "F7F8F9FAFFDA000C03010002110311003F00F6DFFD9"
        )

        with open(output_path, "wb") as f:
            f.write(minimal_jpeg)

        self.last_image_path = output_path
        logger.info(f"Created mock image: {output_path}")
        return output_path

    def cleanup(self):
        """Optional cleanup (no resources to release currently)."""
        pass
