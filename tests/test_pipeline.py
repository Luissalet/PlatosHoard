"""Real end-to-end acceptance tests. Failures propagate; no mocks or skip."""
import base64
import io
import math
import numpy as np
import pytest
import trimesh
from PIL import Image, ImageDraw
from shapely import contains_xy
from shapely.affinity import scale
from shapely.ops import unary_union
from pipeline import process_image
from silhouettes.mask import prepare_mask
from silhouettes.trace import trace_mask, PRESETS
from silhouettes.vector import parse_vector, vector_to_polygons
from silhouettes.validation import top_cap_geometry, edge_diagnostics


def png_fixture(kind, size=200):
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    ink = (0, 0, 0, 255)
    if kind in ("circle", "donut"):
        draw.ellipse((20, 20, size - 20, size - 20), fill=ink)
        if kind == "donut":
            draw.ellipse((70, 70, 130, 130), fill=(0, 0, 0, 0))
    elif kind == "star":
        points = [(100 + (85 if i % 2 == 0 else 40) * math.cos(i * math.pi / 5 - math.pi / 2),
                   100 + (85 if i % 2 == 0 else 40) * math.sin(i * math.pi / 5 - math.pi / 2))
                  for i in range(10)]
        draw.polygon(points, fill=ink)
    elif kind == "islands":
        draw.ellipse((20, 20, 65, 65), fill=ink)
        draw.ellipse((120, 30, 170, 80), fill=ink)
        draw.ellipse((75, 120, 135, 180), fill=ink)
    elif kind == "asymmetric":
        draw.polygon([(20, 20), (55, 20), (55, 135), (175, 135),
                      (175, 175), (20, 175)], fill=ink)
    elif kind == "edge":
        draw.rectangle((0, 40, 90, 150), fill=ink)
    else:
        raise ValueError(kind)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return image, buf.getvalue()


def rendered_svg_mask(svg, width, height):
    from tests.svg_renderer import render_svg_bytes
    data = render_svg_bytes(svg, width, height)
    rgba = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
    return rgba[:, :, 3] >= 128


def polygon_mask(polygon, width, height):
    ys, xs = np.mgrid[0:height, 0:width]
    # Fixed IMAGE coordinates, not each shape's independent bounding box.
    return contains_xy(polygon, xs + 0.5, ys + 0.5)


def iou(a, b):
    union = np.count_nonzero(a | b)
    return np.count_nonzero(a & b) / union if union else 1.0


@pytest.mark.parametrize("kind", ["circle", "star", "donut", "islands", "asymmetric", "edge"])
def test_real_end_to_end(kind, tmp_path):
    image, png = png_fixture(kind)
    (tmp_path / "input.png").write_bytes(png)
    # First, test raster -> SVG independently from every downstream parser.
    raw_svg = trace_mask(prepare_mask(image), PRESETS["exact"])
    (tmp_path / "trace.svg").write_text(raw_svg, encoding="utf-8")
    shape = prepare_mask(image) != 0
    rendered = rendered_svg_mask(raw_svg, 200, 200)
    assert iou(shape, rendered) >= 0.98, "Mask -> SVG changed the shape or polarity"
    result = process_image(png, preset="exact", detail=None, speckle_area=0, thickness=5)
    svg = base64.b64decode(result["svg"]).decode()
    stl = base64.b64decode(result["stl"])
    (tmp_path / "result.svg").write_text(svg, encoding="utf-8")
    (tmp_path / "result.stl").write_bytes(stl)
    # Reload the FILE, not only the in-memory pre-export object.
    mesh = trimesh.load(io.BytesIO(stl), file_type="stl", process=True)
    stats = edge_diagnostics(mesh)
    assert mesh.is_watertight and mesh.is_winding_consistent
    assert stats["boundary_edges"] == stats["nonmanifold_edges"] == 0
    # Actual top-cap TRIANGLES, not a convex hull.
    cap_image_space = scale(top_cap_geometry(mesh), yfact=-1, origin=(0, 0))
    rendered = rendered_svg_mask(svg, 200, 200)
    projected = polygon_mask(cap_image_space, 200, 200)
    assert iou(rendered, projected) >= 0.995, "Rendered SVG and STL cap disagree"
    vector_material = unary_union(vector_to_polygons(parse_vector(svg, tolerance=0.1)))
    assert cap_image_space.symmetric_difference(vector_material).area / vector_material.area < 1e-4
    assert mesh.volume == pytest.approx(vector_material.area * 5, rel=1e-5)
    if kind == "donut":
        assert result["metadata"]["holes"] == 1
        assert not bool(contains_xy(cap_image_space, 100, 100))
    if kind == "islands":
        assert result["metadata"]["components"] == 3
    if kind == "asymmetric":
        assert not bool(contains_xy(cap_image_space, 100, 80))


@pytest.mark.parametrize("preset", ["exact", "clean", "smooth"])
def test_presets_native(preset):
    _, png = png_fixture("circle")
    result = process_image(png, preset=preset, detail=None, speckle_area=0)
    assert result["metadata"]["watertight"]
