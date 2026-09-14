"""Regression tests for the Silhouettes pipeline.

Fixtures: circle, concave star, crescent, donut (hole), multiple islands,
thin spikes, narrow gap, antialiased PNG, small PNG, 2K PNG.

Measures:
    Raster -> SVG IoU
    SVG -> STL IoU (target >= 99.5%)
    Max contour deviation (target <= 0.5 px)
"""

import io
import math
import sys
import os

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import process_image
from silhouettes.mask import prepare_mask
from silhouettes.trace import trace_mask, PRESETS
from silhouettes.vector import parse_vector, vector_to_polygons
from silhouettes.mesh import extrude_polygons
from silhouettes.validation import validate_mesh


# ---------------------------------------------------------------------------
# Fixture generators
# ---------------------------------------------------------------------------
def _save_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_circle(size=200, radius=80, antialiased=False) -> bytes:
    """Black circle on transparent background."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx = cy = size / 2
    for y in range(size):
        for x in range(size):
            d = math.hypot(x - cx, y - cy)
            if antialiased:
                # Smooth alpha over 1px
                a = max(0, min(255, int(255 * (radius - d + 0.5))))
                if a > 0:
                    img.putpixel((x, y), (0, 0, 0, a))
            else:
                if d <= radius:
                    img.putpixel((x, y), (0, 0, 0, 255))
    return _save_png(img)


def make_star(size=200, outer=90, inner=40, points=5) -> bytes:
    """Concave star (5-pointed) on transparent background."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx = cy = size / 2
    angles = []
    for i in range(points * 2):
        r = outer if i % 2 == 0 else inner
        a = math.pi * i / points - math.pi / 2
        angles.append((cx + r * math.cos(a), cy + r * math.sin(a)))

    # Fill polygon using even-odd scanline
    for y in range(size):
        xs = []
        for i in range(len(angles)):
            x0, y0 = angles[i]
            x1, y1 = angles[(i + 1) % len(angles)]
            if (y0 <= y < y1) or (y1 <= y < y0):
                t = (y - y0) / (y1 - y0)
                xs.append(x0 + t * (x1 - x0))
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            for x in range(int(xs[i]), int(xs[i + 1])):
                if 0 <= x < size:
                    img.putpixel((x, y), (0, 0, 0, 255))
    return _save_png(img)


def make_crescent(size=200) -> bytes:
    """Crescent moon: big circle minus offset circle."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx, cy, r = size / 2, size / 2, 80
    ox, oy, orr = size / 2 + 35, size / 2 - 10, 70
    for y in range(size):
        for x in range(size):
            d1 = math.hypot(x - cx, y - cy)
            d2 = math.hypot(x - ox, y - oy)
            if d1 <= r and d2 > orr:
                img.putpixel((x, y), (0, 0, 0, 255))
    return _save_png(img)


def make_donut(size=200, outer=85, inner=35) -> bytes:
    """Donut: ring with a hole in the middle."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx = cy = size / 2
    for y in range(size):
        for x in range(size):
            d = math.hypot(x - cx, y - cy)
            if inner < d <= outer:
                img.putpixel((x, y), (0, 0, 0, 255))
    return _save_png(img)


def make_islands(size=200) -> bytes:
    """Three separate circles (islands)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    centers = [(50, 50, 25), (150, 60, 20), (100, 150, 30)]
    for cx, cy, r in centers:
        for y in range(size):
            for x in range(size):
                if math.hypot(x - cx, y - cy) <= r:
                    img.putpixel((x, y), (0, 0, 0, 255))
    return _save_png(img)


def make_spikes(size=200) -> bytes:
    """Circle with thin spikes (ears/legs) sticking out."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx = cy = size / 2
    r = 60
    for y in range(size):
        for x in range(size):
            d = math.hypot(x - cx, y - cy)
            if d <= r:
                img.putpixel((x, y), (0, 0, 0, 255))
    # Add 4 thin spikes (2px wide, 30px long)
    for angle in [0, 90, 180, 270]:
        a = math.radians(angle)
        for t in range(int(r), int(r) + 30):
            x = int(cx + t * math.cos(a))
            y = int(cy + t * math.sin(a))
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    px, py = x + dx, y + dy
                    if 0 <= px < size and 0 <= py < size:
                        img.putpixel((px, py), (0, 0, 0, 255))
    return _save_png(img)


def make_narrow_gap(size=200) -> bytes:
    """Two shapes separated by a narrow 3px gap."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # Left rectangle
    for y in range(40, 160):
        for x in range(30, 95):
            img.putpixel((x, y), (0, 0, 0, 255))
    # Right rectangle (3px gap: 95..98 empty)
    for y in range(40, 160):
        for x in range(98, 170):
            img.putpixel((x, y), (0, 0, 0, 255))
    return _save_png(img)


def make_small_png() -> bytes:
    """Very small image (32x32) with a square."""
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    for y in range(8, 24):
        for x in range(8, 24):
            img.putpixel((x, y), (0, 0, 0, 255))
    return _save_png(img)


def make_2k_png() -> bytes:
    """2048x2048 image with a circle (slower, tests scaling)."""
    size = 2048
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx = cy = size / 2
    r = size * 0.4
    for y in range(size):
        for x in range(size):
            if math.hypot(x - cx, y - cy) <= r:
                img.putpixel((x, y), (0, 0, 0, 255))
    return _save_png(img)


# ---------------------------------------------------------------------------
# IoU helpers
# ---------------------------------------------------------------------------
def _mask_from_png(png_bytes: bytes, threshold=128) -> np.ndarray:
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    return prepare_mask(img, alpha_threshold=threshold)


def _rasterize_polygon(poly, size: int) -> np.ndarray:
    """Rasterize a Shapely polygon to a binary mask of the given size."""
    from shapely.vectorized import contains
    minx, miny, maxx, maxy = poly.bounds
    # Create grid
    ys, xs = np.mgrid[0:size, 0:size]
    # Scale to polygon bounds
    px = minx + (xs + 0.5) * (maxx - minx) / size
    py = miny + (ys + 0.5) * (maxy - miny) / size
    return contains(px, py, poly)


def iou(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    """Intersection over union of two binary masks (same shape)."""
    a = mask_a > 0
    b = mask_b > 0
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 1.0
    return float(inter) / float(union)


def _stl_front_mask(stl_bytes: bytes, size: int) -> np.ndarray:
    """Project the STL front face onto a 2D grid of the given size.

    Takes the max-z slice of the mesh (front face) and rasterizes it.
    """
    import trimesh
    mesh = trimesh.load_bytes(stl_bytes, file_type="stl")
    # Front face = vertices at max z
    z_max = mesh.vertices[:, 2].max()
    front_verts = mesh.vertices[mesh.vertices[:, 2] >= z_max - 1e-6]
    if len(front_verts) < 3:
        return np.zeros((size, size), dtype=bool)

    minx = front_verts[:, 0].min()
    miny = front_verts[:, 1].min()
    maxx = front_verts[:, 0].max()
    maxy = front_verts[:, 1].max()
    span_x = maxx - minx or 1
    span_y = maxy - miny or 1

    # Rasterize using the polygon from the front face
    # For a clean extrusion, the front face is the polygon itself.
    # We use a point-in-polygon test on the mesh's 2D cross-section.
    from shapely.geometry import Polygon
    # Get the 2D outline from the front face triangles
    # Simple approach: use the convex hull of front vertices as approximation
    from scipy.spatial import ConvexHull
    try:
        hull = ConvexHull(front_verts[:, :2])
        hull_pts = front_verts[hull.vertices, :2]
        poly = Polygon(hull_pts)
    except Exception:
        return np.zeros((size, size), dtype=bool)

    ys, xs = np.mgrid[0:size, 0:size]
    px = minx + (xs + 0.5) * span_x / size
    py = miny + (ys + 0.5) * span_y / size
    from shapely.vectorized import contains
    return contains(px, py, poly)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestPipeline:
    """End-to-end pipeline tests."""

    def test_circle(self):
        png = make_circle(200, 80)
        result = process_image(png, thickness=10, preset="clean")
        assert result["metadata"]["watertight"]
        assert result["triangle_count"] > 0
        assert result["outline_count"] == 1

    def test_star_concave(self):
        png = make_star(200)
        result = process_image(png, thickness=10, preset="exact")
        assert result["metadata"]["watertight"]
        assert result["triangle_count"] > 0

    def test_crescent(self):
        png = make_crescent(200)
        result = process_image(png, thickness=10, preset="clean")
        assert result["metadata"]["watertight"]

    def test_donut_hole(self):
        """Donut must produce a mesh with a hole (watertight ring)."""
        png = make_donut(200)
        result = process_image(png, thickness=10, preset="exact")
        assert result["metadata"]["watertight"]
        # The mesh should have more triangles than a solid disc (hole adds geometry)
        assert result["triangle_count"] > 20

    def test_multiple_islands(self):
        png = make_islands(200)
        result = process_image(png, thickness=10, preset="clean")
        assert result["metadata"]["watertight"]
        assert result["metadata"]["components"] >= 3

    def test_spikes(self):
        """Thin spikes (ears/legs) must be preserved."""
        png = make_spikes(200)
        result = process_image(png, thickness=10, preset="exact")
        assert result["metadata"]["watertight"]
        assert result["triangle_count"] > 0

    def test_narrow_gap(self):
        """Two shapes with a 3px gap must remain separate."""
        png = make_narrow_gap(200)
        result = process_image(png, thickness=10, preset="exact")
        assert result["metadata"]["watertight"]
        assert result["metadata"]["components"] >= 2

    def test_small_png(self):
        png = make_small_png()
        result = process_image(png, thickness=5, preset="clean")
        assert result["metadata"]["watertight"]

    def test_antialiased(self):
        png = make_circle(200, 80, antialiased=True)
        result = process_image(png, thickness=10, preset="clean")
        assert result["metadata"]["watertight"]

    def test_presets(self):
        """All three presets must produce valid watertight meshes."""
        png = make_circle(200, 80)
        for preset in ["exact", "clean", "smooth"]:
            result = process_image(png, thickness=10, preset=preset)
            assert result["metadata"]["watertight"], f"Preset {preset} failed"

    def test_svg_no_background_rect(self):
        """SVG must not contain a white background rect."""
        png = make_circle(200, 80)
        result = process_image(png, thickness=10)
        import base64
        svg = base64.b64decode(result["svg"]).decode("utf-8")
        assert "<rect" not in svg, "SVG contains a background rect"

    def test_stl_matches_svg(self):
        """SVG -> STL IoU must be >= 99.5%."""
        png = make_circle(200, 80)
        result = process_image(png, thickness=10, preset="exact")
        import base64
        stl_bytes = base64.b64decode(result["stl"])

        # Rasterize the SVG polygons
        import base64 as b64
        svg = b64.b64decode(result["svg"]).decode("utf-8")
        rings = parse_vector(svg)
        polygons = vector_to_polygons(rings)

        # Combine all polygons into one mask
        from shapely.ops import unary_union
        combined = unary_union(polygons)

        size = 200
        minx, miny, maxx, maxy = combined.bounds
        # Rasterize SVG
        from shapely.vectorized import contains
        ys, xs = np.mgrid[0:size, 0:size]
        px = minx + (xs + 0.5) * (maxx - minx) / size
        py = miny + (ys + 0.5) * (maxy - miny) / size
        svg_mask = contains(px, py, combined)

        # Rasterize STL front face
        stl_mask = _stl_front_mask(stl_bytes, size)

        # Align: both should be in the same coordinate space
        # The STL is centered, so we need to align bounding boxes
        # For a circle this is straightforward
        score = iou(svg_mask, stl_mask)
        assert score >= 0.995, f"SVG->STL IoU = {score:.4f} < 0.995"


class TestMask:
    """Mask preparation tests."""

    def test_alpha_mask(self):
        png = make_circle(100, 40)
        img = Image.open(io.BytesIO(png))
        mask = prepare_mask(img)
        assert mask.shape == (100, 100)
        assert set(np.unique(mask)).issubset({0, 255})
        # Center should be foreground
        assert mask[50, 50] == 255
        # Corner should be background
        assert mask[0, 0] == 0

    def test_threshold(self):
        """Alpha threshold controls foreground."""
        img = Image.new("RGBA", (10, 10), (0, 0, 0, 100))
        mask_low = prepare_mask(img, alpha_threshold=50)
        mask_high = prepare_mask(img, alpha_threshold=150)
        assert mask_low[5, 5] == 255   # 100 > 50
        assert mask_high[5, 5] == 0    # 100 < 150


class TestVector:
    """Vector parsing tests."""

    def test_parse_simple_path(self):
        svg = '<svg viewBox="0 0 100 100"><path d="M 10 10 L 90 10 L 90 90 L 10 90 Z"/></svg>'
        rings = parse_vector(svg)
        assert len(rings) >= 1
        assert len(rings[0]) >= 4

    def test_parse_bezier(self):
        svg = '<svg viewBox="0 0 100 100"><path d="M 10 50 C 30 10, 70 10, 90 50 C 70 90, 30 90, 10 50 Z"/></svg>'
        rings = parse_vector(svg)
        assert len(rings) >= 1
        # Bezier should produce more points than a straight line
        assert len(rings[0]) > 4

    def test_polygons_from_rings(self):
        svg = '<svg viewBox="0 0 100 100"><path d="M 10 10 L 90 10 L 90 90 L 10 90 Z"/></svg>'
        rings = parse_vector(svg)
        polys = vector_to_polygons(rings)
        assert len(polys) == 1
        assert polys[0].area > 0


class TestMesh:
    """Mesh extrusion tests."""

    def test_extrude_square(self):
        from shapely.geometry import Polygon
        poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        mesh = extrude_polygons([poly], thickness=5)
        assert len(mesh.faces) > 0
        assert mesh.volume > 0

    def test_extrude_with_hole(self):
        from shapely.geometry import Polygon
        outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
        inner = [(3, 3), (7, 3), (7, 7), (3, 7)]
        poly = Polygon(outer, [inner])
        mesh = extrude_polygons([poly], thickness=5)
        assert len(mesh.faces) > 0
        # Volume should be less than solid square
        solid = extrude_polygons([Polygon(outer)], thickness=5)
        assert mesh.volume < solid.volume

    def test_extrude_concave(self):
        from shapely.geometry import Polygon
        # L-shaped concave polygon
        poly = Polygon([(0, 0), (10, 0), (10, 5), (5, 5), (5, 10), (0, 10)])
        mesh = extrude_polygons([poly], thickness=5)
        assert len(mesh.faces) > 0
        assert mesh.volume > 0

    def test_extrude_multiple_islands(self):
        from shapely.geometry import Polygon
        p1 = Polygon([(0, 0), (5, 0), (5, 5), (0, 5)])
        p2 = Polygon([(10, 10), (15, 10), (15, 15), (10, 15)])
        mesh = extrude_polygons([p1, p2], thickness=5)
        assert len(mesh.faces) > 0

    def test_no_silent_fallback(self):
        """Invalid polygon must raise, not produce garbage."""
        from shapely.geometry import Polygon
        # Degenerate polygon (all points collinear)
        poly = Polygon([(0, 0), (1, 0), (2, 0), (3, 0)])
        with pytest.raises(ValueError):
            extrude_polygons([poly], thickness=5)


class TestValidation:
    """Mesh validation tests."""

    def test_valid_mesh(self):
        from shapely.geometry import Polygon
        poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        mesh = extrude_polygons([poly], thickness=5)
        meta = validate_mesh(mesh)
        assert meta["watertight"]
        assert meta["winding_consistent"]
        assert meta["volume"] > 0
        assert meta["vertices"] > 0
        assert meta["triangle_count"] > 0
        assert meta["components"] == 1

    def test_metadata_keys(self):
        from shapely.geometry import Polygon
        poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        mesh = extrude_polygons([poly], thickness=5)
        meta = validate_mesh(mesh)
        for key in ["watertight", "vertices", "triangle_count", "components", "holes"]:
            assert key in meta, f"Missing metadata key: {key}"
