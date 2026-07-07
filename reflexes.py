import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ReflexResult:
    blocked: bool
    direction: str  # 'left' or 'right' escape direction if blocked, else 'none'
    edge_densities: dict


class ReflexEngine:
    """
    Fast, local obstacle detection using OpenCV.

    Runs on every captured frame before Claude is consulted. A close, flat
    obstacle (wall, box) directly ahead shows up as an unusually low-detail
    patch in the near-field of the frame, since it's too close for the
    camera to resolve texture/edges. When that happens, the car reacts
    immediately by turning toward whichever side has more edge detail
    (i.e. is more open), without waiting on a Claude API round trip.

    This is a coarse heuristic, not true depth sensing — thresholds may
    need tuning for your camera, lighting, and environment.
    """

    def __init__(self, block_threshold: float = 0.02, near_field_fraction: float = 0.5):
        """
        Args:
            block_threshold: minimum edge density (fraction of edge pixels)
                the center region needs to be considered "open." Below this,
                the reflex assumes a close, texture-less obstacle fills the view.
            near_field_fraction: fraction of frame height (from the bottom)
                treated as "near field" — the area closest to the car.
        """
        self.block_threshold = block_threshold
        self.near_field_fraction = near_field_fraction

    def check(self, image_path: Path) -> ReflexResult:
        """
        Analyze a frame for an imminent obstacle directly ahead.

        Args:
            image_path: Path to the captured image

        Returns:
            ReflexResult with blocked flag and suggested escape direction
        """
        img = cv2.imread(str(image_path))
        if img is None:
            logger.warning(f"Reflex check could not read image: {image_path}")
            return ReflexResult(blocked=False, direction="none", edge_densities={})

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)

        h, w = edges.shape
        near_field = edges[int(h * (1 - self.near_field_fraction)):, :]

        third = w // 3
        left = near_field[:, :third]
        center = near_field[:, third:2 * third]
        right = near_field[:, 2 * third:]

        densities = {
            "left": float(np.count_nonzero(left)) / left.size,
            "center": float(np.count_nonzero(center)) / center.size,
            "right": float(np.count_nonzero(right)) / right.size,
        }

        blocked = densities["center"] < self.block_threshold

        if not blocked:
            return ReflexResult(blocked=False, direction="none", edge_densities=densities)

        # Escape toward whichever side has more detail (implying it's more open)
        direction = "left" if densities["left"] > densities["right"] else "right"

        logger.info(
            f"Reflex triggered — obstacle ahead (densities={densities}), "
            f"evading {direction}"
        )
        return ReflexResult(blocked=True, direction=direction, edge_densities=densities)
