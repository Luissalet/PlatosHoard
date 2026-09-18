"""Pinch points must never reach the extruder (silhouettes/pinch.py).

A ring contact — two holes meeting at one vertex, or a hole touching the
outline — is a valid polygon for Shapely but an edge shared by four faces
once extruded, which fails the watertight check.  These tests cover the
three places the guard is applied: tracing, the recipes, and extrusion.
"""
import pytest
from shapely.geometry import Polygon, box

from silhouettes.mesh import extrude_polygons
from silhouettes.pinch import depinch, ring_contacts, separate_touching_rings
from silhouettes.validation import edge_diagnostics


def two_holes_touching():
    """Square plate whose two diamond holes share exactly one vertex."""
    outer = [(0, 0), (100, 0), (100, 100), (0, 100)]
    left = [(20, 50), (50, 20), (50, 50)]          # corner at (50, 50)
    right = [(50, 50), (50, 80), (80, 50)]         # same corner
    poly = Polygon(outer, [left, right])
    assert poly.is_valid, "Shapely accepts the pinch: that is the trap"
    return poly


def hole_touching_outline():
    outer = [(0, 0), (100, 0), (100, 100), (0, 100)]
    hole = [(50, 0), (70, 40), (30, 40)]           # apex sits on the bottom edge
    poly = Polygon(outer, [hole])
    assert poly.is_valid
    return poly


@pytest.mark.parametrize("make", [two_holes_touching, hole_touching_outline],
                         ids=["dos-huecos", "hueco-contra-contorno"])
def test_pinch_is_detected_and_opened(make):
    poly = make()
    assert ring_contacts(poly), "the contact must be detected"
    repaired = separate_touching_rings(poly)
    assert not ring_contacts(repaired), "and must be gone afterwards"
    assert repaired.is_valid and repaired.area > 0
    assert len(repaired.interiors) == len(poly.interiors), "no hole may be dropped"
    assert repaired.hausdorff_distance(poly) <= 3e-3, "the shape barely moves (microns)"


@pytest.mark.parametrize("make", [two_holes_touching, hole_touching_outline],
                         ids=["dos-huecos", "hueco-contra-contorno"])
def test_pinched_polygon_extrudes_watertight(make):
    mesh = extrude_polygons([make()], thickness=3.0)
    d = edge_diagnostics(mesh)
    assert d["boundary_edges"] == 0
    assert d["nonmanifold_edges"] == 0, "an extruded pinch is non-manifold"
    assert mesh.is_watertight


def test_clean_polygon_is_left_alone():
    """No silent perturbation of geometry that was never pinched."""
    clean = box(0, 0, 10, 20).difference(box(2, 2, 4, 4))
    assert separate_touching_rings(clean) is clean
    assert depinch(clean) is clean


def test_inverse_recipe_of_a_pinched_shape_is_printable():
    """The recipes create contacts of their own: canvas − shape, here touching."""
    from silhouettes.editor.composition import compose_part
    canvas = box(0, 0, 100, 100)
    # A triangle whose apex lands exactly on the canvas edge.
    shape = Polygon([(50, 0), (80, 60), (20, 60)])
    plate = compose_part(shape, canvas, "inverse")
    mesh = extrude_polygons([plate], thickness=2.0)
    assert edge_diagnostics(mesh)["nonmanifold_edges"] == 0
    assert mesh.is_watertight
