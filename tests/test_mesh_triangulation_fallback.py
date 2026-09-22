"""Regression: aligned hole bridges in 99GM's inverse export broke Earcut."""
from pathlib import Path
from unittest.mock import patch

import pytest
from shapely import from_wkb
from shapely.geometry import box

from silhouettes.mesh import extrude_polygons
from silhouettes.validation import top_cap_geometry, validate_mesh


def test_kingler_inverse_preserves_holes_boundary_and_volume():
    polygon = from_wkb((Path(__file__).parent / 'fixtures/kingler_inverse_earcut.wkb').read_bytes())
    before = polygon.wkb
    mesh = extrude_polygons([polygon], thickness=3)
    stats = validate_mesh(mesh, [polygon], 3)
    assert stats['watertight'] and stats['nonmanifold_edges'] == 0
    assert stats['holes'] == 4
    assert top_cap_geometry(mesh).symmetric_difference(polygon).area < 1e-8
    assert mesh.volume == pytest.approx(polygon.area * 3, rel=1e-12)
    assert polygon.wkb == before


def test_ordinary_polygon_keeps_fast_earcut_path():
    with patch('silhouettes.mesh.constrained_delaunay_triangles', side_effect=AssertionError('unexpected fallback')):
        assert extrude_polygons([box(0, 0, 5, 5)], 3).is_watertight


def test_fallback_still_requires_validated_mesh():
    with patch('silhouettes.mesh.validate_mesh', side_effect=ValueError('invalid test mesh')):
        with pytest.raises(ValueError, match='constrained triangulation: invalid test mesh'):
            extrude_polygons([box(0, 0, 5, 5)], 3)
