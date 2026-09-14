"""Mandatory parser regressions: require the REAL svg.path dependency."""
import numpy as np
import pytest
from shapely.ops import unary_union
from silhouettes.trace import _strip_background
from silhouettes.vector import parse_vector, vector_to_polygons


def geometry(svg):
    return unary_union(vector_to_polygons(parse_vector(svg, tolerance=0.05)))


@pytest.mark.parametrize("d", [
    "M32 32 L72 32 L72 72 L32 72 Z",
    "M32 32 l40 0 l0 40 l-40 0z",
    "M32 32h40v40H32z",
    "m3.2E1 32h4e1v40h-40z",
])
def test_relative_absolute_and_shorthand_agree(d):
    svg = _strip_background(f'<svg><path d="{d}"/></svg>', 100, 100, 16)
    p = geometry(svg)
    assert p.bounds == pytest.approx((16, 16, 56, 56))
    assert p.area == pytest.approx(1600)


def test_path_and_group_transforms():
    svg = '<svg viewBox="16 16 100 100"><g transform="translate(10 20)"><path transform="scale(2)" d="M3 3h10v10H3z"/></g></svg>'
    p = geometry(svg)
    assert p.bounds == pytest.approx((0, 10, 20, 30))
    assert p.area == pytest.approx(400)


def test_xml_ids_single_quotes_and_subpaths():
    svg = "<svg viewBox='0 0 100 100'><path id='do-not-parse-this-id' fill-rule='evenodd' d='M10 10h80v80H10Z M30 30h40v40H30z'/></svg>"
    p = geometry(svg)
    assert len(p.interiors) == 1
    assert p.area == pytest.approx(4800)


def test_s_curve_subdivision_survives_parser():
    svg = '<svg viewBox="0 0 150 150"><path d="M10 60 C10 160 110 -40 110 60 L110 120 L10 120Z"/></svg>'
    rings = parse_vector(svg, tolerance=0.05)
    assert len(rings[0]) > 10
    assert min(y for _, y in rings[0]) < 40


def test_distinct_overlapping_paths_are_not_holes():
    svg = '<svg viewBox="0 0 100 100"><path d="M0 0h60v60H0Z"/><path d="M30 0h60v60H30Z"/></svg>'
    assert geometry(svg).area == pytest.approx(5400)


def test_nonzero_same_winding_is_not_a_hole():
    svg = '<svg viewBox="0 0 100 100"><path d="M10 10h80v80H10Z M30 30h40v40H30Z"/></svg>'
    assert geometry(svg).area == pytest.approx(6400)


def test_unsupported_geometry_raises_instead_of_disappearing():
    with pytest.raises(ValueError, match="Unsupported"):
        geometry('<svg viewBox="0 0 100 100"><rect x="10" y="10" width="80" height="80"/></svg>')
