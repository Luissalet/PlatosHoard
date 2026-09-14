"""These tests exercise the fidelity guard, NOT VTracer or browser rendering."""
from pathlib import Path
import importlib.util
import numpy as np
import pytest
from shapely.geometry import Polygon, box, Point
from shapely.affinity import translate

spec = importlib.util.spec_from_file_location("edge_quality_standalone",
    Path(__file__).resolve().parents[1] / "silhouettes" / "edge_quality.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_mask_polarity_and_pixel_bounds():
    mask = np.zeros((10, 12), dtype=np.uint8)
    mask[2:7, 3:9] = 255
    region = m.mask_region(mask)
    assert region.bounds == (3, 2, 9, 7)
    assert region.area == 30


def test_donut_hole_is_preserved_by_reference():
    mask = np.ones((20, 20), dtype=np.uint8) * 255
    mask[7:13, 7:13] = 0
    assert m.topology(m.mask_region(mask)) == (1, 1)


def test_identical_geometry_passes():
    a = box(0, 0, 10, 10)
    report = m.compare_regions(a, a)
    assert report['passes_guard']
    assert report['boundary_upper_bound_px'] <= 0.076
    assert report['iou'] == 1


def test_small_translation_passes_without_rescaling_boxes():
    a = box(0, 0, 100, 100)
    b = translate(a, xoff=0.3)
    result = m.compare_regions(a, b)
    assert result['passes_guard']
    assert result['boundary_upper_bound_px'] >= 0.3


def test_wrong_translation_fails_even_with_same_shape():
    a = box(0, 0, 100, 100)
    assert not m.compare_regions(a, translate(a, xoff=2))['passes_guard']


def test_missing_hole_rejected_even_when_area_change_is_tiny():
    a = Polygon(box(0, 0, 100, 100).exterior.coords,
                [box(49.9, 49.9, 50.1, 50.1).exterior.coords])
    r = m.compare_regions(a, box(0, 0, 100, 100))
    assert not r['passes_guard']
    assert r['reference_holes'] == 1 and r['candidate_holes'] == 0


def test_short_thin_tip_loss_not_hidden_by_global_iou():
    # Removing this 4px long, 1px wide tip changes only about 0.04% of total area.
    body = box(0, 0, 100, 100)
    with_tip = body.union(box(49.5, 100, 50.5, 104))
    r = m.compare_regions(with_tip, body)
    assert r['iou'] > 0.999
    assert not r['passes_guard']
    assert r['boundary_upper_bound_px'] >= 4


def test_nearby_islands_cannot_be_joined():
    a = box(0, 0, 10, 10).union(box(10.2, 0, 20, 10))
    assert not m.compare_regions(a, box(0, 0, 20, 10))['passes_guard']


def test_smooth_circle_can_pass_against_pixel_steps():
    yy, xx = np.mgrid[0:128, 0:128]
    binary = ((xx + 0.5 - 64)**2 + (yy + 0.5 - 64)**2 <= 50**2).astype(np.uint8) * 255
    pixel_region = m.mask_region(binary)
    ideal = Point(64, 64).buffer(50, quad_segs=256)
    r = m.compare_regions(pixel_region, ideal)
    assert r['passes_guard'], r


@pytest.mark.parametrize('mask', [np.zeros((0, 5)), np.ones((2, 2)), np.zeros((4, 4))])
def test_invalid_or_empty_mask_rejected(mask):
    with pytest.raises(ValueError):
        m.mask_region(mask)


def test_guard_budget_exhaustion_is_explicit():
    with pytest.raises(ValueError, match='budget'):
        m.compare_regions(box(0, 0, 100, 100), box(0, 0, 100, 100), max_samples=5)
