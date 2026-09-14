"""Unit tests executable without VTracer/Earcut/svg.path.

These do NOT replace the mandatory real integration tests in test_pipeline.py.
"""
import io
import math
import sys
import types
import xml.etree.ElementTree as ET
import numpy as np
import pytest
import trimesh
from PIL import Image
from shapely import constrained_delaunay_triangles
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from silhouettes.mask import prepare_mask, remove_small_components
from silhouettes.trace import mask_to_rgba, _strip_background, trace_mask
from silhouettes.vector import (PathRings, ParsedVector, _flatten_cubic,
                                _transform, vector_to_polygons)
from silhouettes.validation import edge_diagnostics, validate_mesh, top_cap_geometry
from pipeline import _settings_from_params


def test_foreground_polarity():
    image = Image.new("RGBA", (5, 5), (0, 0, 0, 0))
    image.putpixel((2, 2), (255, 0, 0, 255))
    mask = prepare_mask(image)
    rgba = mask_to_rgba(mask, pad=2)
    assert mask[2, 2] == 255 and mask[0, 0] == 0
    assert rgba[4, 4].tolist() == [0, 0, 0, 255]
    assert rgba[2, 2].tolist() == [255, 255, 255, 255]
    assert np.all(rgba[:2] == 255)


@pytest.mark.parametrize("d", [
    "M32 32 l40 0 l0 40 l-40 0z",
    "M32 32h40v40H32z",
    "M32 32 c0 20 20 -20 20 0 s20 20 20 0z",
    "M3.2E1 32 Q40 90 72 32 T112 32Z",
])
def test_viewport_does_not_modify_path_data(d):
    svg = f'<svg width="132" height="132"><g transform="translate(3 4)"><path d="{d}"/></g></svg>'
    normalized = _strip_background(svg, 100, 100, 16)
    root = ET.fromstring(normalized)
    assert root.get("viewBox") == "16 16 100 100"
    assert root.find("g").get("transform") == "translate(3 4)"
    assert root.find("g/path").get("d") == d


def test_normalization_matches_independent_renderer():
    from tests.svg_renderer import render_svg_bytes
    d = "M32 32 l40 0 l0 40 l-40 0 z"
    raw = f'<svg xmlns="http://www.w3.org/2000/svg" width="132" height="132"><path d="{d}"/></svg>'
    cropped = _strip_background(raw, 100, 100, 16)
    a = np.asarray(Image.open(io.BytesIO(render_svg_bytes(raw))))
    b = np.asarray(Image.open(io.BytesIO(render_svg_bytes(cropped))))
    assert np.array_equal(a[16:116, 16:116], b)


def test_native_adapter_contract_only(monkeypatch):
    observed = {}
    class Config:
        def __init__(self, **kwargs):
            observed["config"] = kwargs
        def convert_pixels(self, raw, width, height):
            assert isinstance(raw, bytes)
            observed["pixels"] = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)
            return '<svg><path d="M16 16h1v1h-1z"/></svg>'
    monkeypatch.setitem(sys.modules, "vtracer", types.SimpleNamespace(Config=Config))
    trace_mask(np.full((1, 1), 255, dtype=np.uint8))
    assert observed["config"]["clustering"] == "bw"
    assert observed["config"]["optimize"] == 0
    assert observed["pixels"][16, 16].tolist() == [0, 0, 0, 255]


def test_s_curve_is_not_flattened_to_a_chord():
    points = _flatten_cubic((0, 0), (0, 100), (100, -100), (100, 0), 0.1)
    assert len(points) > 10
    assert max(abs(p[1]) for p in points) > 20


def test_straight_cubic_needs_one_segment():
    assert _flatten_cubic((0, 0), (1, 0), (2, 0), (3, 0), 0.1) == [(3, 0)]


def test_subdivision_budget_is_not_silent():
    with pytest.raises(ValueError, match="budget"):
        _flatten_cubic((0, 0), (0, 100), (100, -100), (100, 0), 0.1, max_depth=0)


def test_transform_order():
    q = _transform("translate(10,20) scale(2) rotate(90)") @ np.array([1, 0, 1.])
    assert np.allclose(q, [10, 22, 1])
    assert np.allclose(_transform("matrix(1 0 0 1 -16 -16)") @ [32, 32, 1], [16, 16, 1])


def test_transform_exponents():
    assert np.allclose(_transform("translate(+1.6E1,-1.6e+1)") @ [0, 0, 1], [16, -16, 1])


def square(a, b):
    return [(a, a), (b, a), (b, b), (a, b)]


@pytest.mark.parametrize("rule,inner_reversed,expected", [
    ("evenodd", False, 84), ("evenodd", True, 84),
    ("nonzero", False, 100), ("nonzero", True, 84),
])
def test_fill_rules(rule, inner_reversed, expected):
    inner = square(3, 7)
    if inner_reversed:
        inner = list(reversed(inner))
    vector = ParsedVector([PathRings([square(0, 10), inner], rule)])
    material = unary_union(vector_to_polygons(vector))
    assert material.area == expected


def test_nested_island():
    vector = ParsedVector([PathRings([square(0, 10), square(2, 8), square(4, 6)], "evenodd")])
    polygons = vector_to_polygons(vector)
    assert len(polygons) == 2
    assert sum(p.area for p in polygons) == 68
    assert sum(len(p.interiors) for p in polygons) == 1


def test_paths_union_instead_of_global_xor():
    a, b = square(0, 10), [(5, 0), (15, 0), (15, 10), (5, 10)]
    vector = ParsedVector([PathRings([a], "evenodd"), PathRings([b], "evenodd")])
    assert sum(p.area for p in vector_to_polygons(vector)) == 150


def test_small_hole_not_deleted():
    outer, hole = square(0, 10), square(4.8, 5.2)
    vector = ParsedVector([PathRings([outer, hole], "evenodd")])
    polygon = vector_to_polygons(vector)[0]
    assert len(polygon.interiors) == 1
    assert Polygon(polygon.interiors[0]).area == pytest.approx(0.16)
    assert polygon.area == pytest.approx(99.84)


def test_viewport_clips_filled_geometry_not_individual_rings():
    vector = ParsedVector([PathRings([square(-5, 15)], "nonzero")], 10, 10)
    assert vector_to_polygons(vector)[0].equals(box(0, 0, 10, 10))


def test_speckle_removal_does_not_fill_holes():
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[2:12, 2:12] = 255
    mask[6, 6] = 0
    mask[17, 17] = 255
    cleaned = remove_small_components(mask, 2)
    assert cleaned[17, 17] == 0
    assert cleaned[6, 6] == 0
    assert np.count_nonzero(cleaned) == 99


def test_detail_and_speckle_are_independent():
    a = _settings_from_params("clean", 0.8, 1)
    b = _settings_from_params("clean", 0.8, 100)
    assert a.simplify == b.simplify == 0.8
    assert a.length_threshold == b.length_threshold == 4.0
    assert _settings_from_params("exact", None, 1).simplify == 0.1


def test_real_edge_counts_closed_box():
    mesh = trimesh.creation.box()
    stats = edge_diagnostics(mesh)
    assert stats == {"edge_occurrences": 36, "unique_edges": 18,
                     "boundary_edges": 0, "nonmanifold_edges": 0}
    # These old values are always equal, NOT evidence of unpaired edges.
    assert len(mesh.edges) == len(mesh.edges_unique_inverse) == 36
    assert validate_mesh(mesh)["watertight"]


def test_real_edge_counts_missing_face():
    mesh = trimesh.creation.box()
    mesh.update_faces(np.arange(len(mesh.faces)) != 0)
    assert edge_diagnostics(mesh)["boundary_edges"] == 3
    with pytest.raises(ValueError, match="boundary_edges=3"):
        validate_mesh(mesh)


def _geos_test_mesh(poly, height):
    """Independent TEST FIXTURE ONLY. Production still uses Earcut exclusively."""
    triangles = constrained_delaunay_triangles(poly)
    raw = np.concatenate([np.array(t.exterior.coords)[:3] for t in triangles.geoms])
    vertices, indices = np.unique(raw, axis=0, return_inverse=True)
    return trimesh.creation.extrude_triangulation(vertices, indices.reshape(-1, 3), height)


@pytest.mark.parametrize("polygon", [
    Polygon([(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)]),
    Polygon(square(0, 10), [square(3, 7)]),
    Polygon(square(0, 10), [square(4.8, 5.2)]),
])
def test_cap_validation_with_independent_mesh_fixture(polygon):
    mesh = _geos_test_mesh(polygon, 5)
    metadata = validate_mesh(mesh, [polygon], 5)
    assert metadata["holes"] == len(polygon.interiors)
    assert top_cap_geometry(mesh).symmetric_difference(polygon).area < 1e-9
    assert mesh.volume == pytest.approx(polygon.area * 5)
