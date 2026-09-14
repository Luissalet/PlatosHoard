"""Smoke test with real dependencies. Never return False to conceal a failure."""
import numpy as np
from silhouettes.trace import trace_mask, PRESETS
from silhouettes.vector import parse_vector, vector_to_polygons
from silhouettes.mesh import extrude_polygons
from silhouettes.validation import validate_mesh


def test_vtracer_integration():
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[25:75, 25:75] = 255
    svg = trace_mask(mask, PRESETS["exact"])
    polygons = vector_to_polygons(parse_vector(svg))
    mesh = extrude_polygons(polygons, thickness=10)
    assert len(polygons) == 1
    assert 2300 < polygons[0].area < 2700
    metadata = validate_mesh(mesh, polygons, 10)
    assert metadata["watertight"]
    assert metadata["boundary_edges"] == metadata["nonmanifold_edges"] == 0
