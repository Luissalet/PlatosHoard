"""Stage 2: binary mask -> SVG via VTracer.

VTracer runs in spline mode. The SVG contains ONLY the shape (no white
background rect — the preview background is CSS, not part of the SVG).
"""

import re
from dataclasses import dataclass
from typing import Optional

import numpy as np

import vtracer


@dataclass
class TraceSettings:
    """Independent trace parameters (no single 'simplify' value)."""
    mode: str = "spline"
    simplify: float = 0.4          # VTracer corner simplification 0..1
    path_precision: int = 3        # VTracer path precision
    color_precision: int = 8
    layer_difference: int = 8
    corner_threshold: int = 90
    length_threshold: float = 4.0
    max_iterations: int = 10
    splice_threshold: int = 0
    filter_speckle: int = 1


# Presets
PRESETS = {
    "exact":  TraceSettings(simplify=0.1, path_precision=4, corner_threshold=120),
    "clean":  TraceSettings(simplify=0.4, path_precision=3, corner_threshold=90),
    "smooth": TraceSettings(simplify=0.7, path_precision=2, corner_threshold=60),
}


class VTracerBackend:
    """Vectorize a binary mask into an SVG string using VTracer."""

    def trace(self, mask: np.ndarray, settings: Optional[TraceSettings] = None) -> str:
        """Trace a (H, W) binary mask (0/255) into an SVG string.

        The mask is rendered as a black-on-transparent RGBA array and traced
        in-memory with VTracer (no temp files). The resulting SVG contains
        only the shape paths (no background rect).
        """
        settings = settings or PRESETS["clean"]
        h, w = mask.shape

        # RGBA: black pixels on WHITE background, alpha=255 everywhere.
        # VTracer's binary clustering ignores alpha and thresholds on color,
        # so the shape must be encoded as black-on-white.
        #
        # IMPORTANT: pad the canvas with a white border. VTracer traces the
        # image border as a contour, so a shape touching the edge would
        # produce a full-canvas ring as an extra "hole". The pad keeps the
        # shape away from the border; we crop the viewBox back afterwards.
        # The pad must be large enough that spline overshoot (control points
        # can extend beyond the shape) never reaches the padded border.
        pad = 16
        ph, pw = h + 2 * pad, w + 2 * pad
        rgba = np.full((ph, pw, 4), 255, dtype=np.uint8)
        rgba[pad:pad + h, pad:pad + w][mask == 0] = (0, 0, 0, 255)

        config = vtracer.Config(
            clustering="binary",
            mode=settings.mode,
            filter_speckle=settings.filter_speckle,
            color_precision=settings.color_precision,
            layer_difference=settings.layer_difference,
            corner_threshold=settings.corner_threshold,
            length_threshold=settings.length_threshold,
            max_iterations=settings.max_iterations,
            splice_threshold=settings.splice_threshold,
            simplify=settings.simplify,
            path_precision=settings.path_precision,
        )

        svg = vtracer.convert_pixels(rgba.flatten(), pw, ph, config)
        if isinstance(svg, bytes):
            svg = svg.decode("utf-8")

        return _strip_background(svg, w, h, pad)


def _strip_background(svg: str, width: int, height: int, pad: int = 0) -> str:
    """Remove background rect, crop the pad offset, normalize the SVG.

    The traced SVG lives in a padded canvas (pad px of white border around
    the original image). We shift all path coordinates by -pad and set the
    viewBox back to the original image size.
    """
    # Drop any <rect .../> elements (background).
    svg = re.sub(r"<rect[^>]*/>", "", svg)
    svg = re.sub(r"<rect[^>]*>.*?</rect>", "", svg, flags=re.DOTALL)

    if pad:
        svg = _shift_path_data(svg, -pad, -pad)

    # Normalize viewBox / width / height to the original image size.
    svg = re.sub(r'viewBox="[^"]*"', f'viewBox="0 0 {width} {height}"', svg)
    svg = re.sub(r'(?<![\w-])width="[^"]*"', f'width="{width}"', svg, count=1)
    svg = re.sub(r'(?<![\w-])height="[^"]*"', f'height="{height}"', svg, count=1)

    # Ensure fill-rule evenodd so holes work when rendered.
    if "fill-rule" not in svg:
        svg = svg.replace("<path", '<path fill-rule="evenodd"', 1)

    return svg.strip() + "\n"


def _shift_path_data(svg: str, dx: float, dy: float) -> str:
    """Shift all coordinates in every path 'd' attribute by (dx, dy)."""
    def shift_d(m):
        d = m.group(1)
        # Tokenize: commands and numbers
        tokens = re.findall(r"[MLCQZmlcqz]|-?\d*\.?\d+(?:e-?\d+)?", d)
        out = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if re.match(r"[MLCQZmlcqz]", tok):
                out.append(tok)
                i += 1
                # Consume coordinate pairs following this command
                while i < len(tokens) and not re.match(r"[MLCQZmlcqz]", tokens[i]):
                    x = float(tokens[i]) + dx
                    y = float(tokens[i + 1]) + dy
                    out.append(f"{x:g} {y:g}")
                    i += 2
            else:
                # Implicit repeated coordinates (shouldn't happen at start)
                out.append(tok)
                i += 1
        return f'd="{" ".join(out)}"'

    return re.sub(r'd="([^"]+)"', shift_d, svg)


def trace_mask(mask: np.ndarray, settings: Optional[TraceSettings] = None) -> str:
    """Convenience wrapper: binary mask -> SVG string."""
    return VTracerBackend().trace(mask, settings)
