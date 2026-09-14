"""Stage 1: PNG -> clean binary alpha mask.

Rules:
- If the image has a useful alpha channel, alpha defines the shape.
- alpha > threshold = foreground.
- Output is a pure binary (0/255) mask, no blur, no geometric smoothing.
"""

import numpy as np
from PIL import Image


def prepare_mask(image: Image.Image, alpha_threshold: int = 128) -> np.ndarray:
    """Return a binary uint8 mask (0 or 255) of the foreground shape.

    Args:
        image: PIL image (any mode).
        alpha_threshold: alpha values strictly above this are foreground.

    Returns:
        (H, W) uint8 array with values 0 or 255.
    """
    img = image.convert("RGBA")
    r, g, b, a = img.split()
    alpha_arr = np.array(a, dtype=np.uint8)

    if alpha_arr.min() < 250:
        # Alpha channel carries meaningful transparency -> use it.
        mask = (alpha_arr > alpha_threshold).astype(np.uint8) * 255
    else:
        # No transparency: threshold on luminance (dark pixels = shape).
        lum = (
            0.299 * np.array(r, dtype=np.float32)
            + 0.587 * np.array(g, dtype=np.float32)
            + 0.114 * np.array(b, dtype=np.float32)
        )
        mask = (lum < 128).astype(np.uint8) * 255

    return mask


def mask_to_silhouette(mask: np.ndarray) -> Image.Image:
    """Build a pure-black RGBA silhouette image from a binary mask."""
    silhouette = Image.new("RGBA", (mask.shape[1], mask.shape[0]), (0, 0, 0, 0))
    silhouette.putalpha(Image.fromarray(mask, mode="L"))
    return silhouette
