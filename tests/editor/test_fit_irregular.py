"""Tests for task 13 — fit_inside (irregular containers)."""
import pytest
from shapely.geometry import box, Polygon

from silhouettes.editor.fitting import fit_inside, FittingError
from silhouettes.editor.transforms import apply_pose
from silhouettes.editor.constraints import fits


class TestFitInside:
    def test_l_shape_concave(self):
        parent = box(0, 0, 100, 100).union(box(100, 0, 150, 50))
        child = box(-10, -10, 10, 10)
        result = fit_inside(child, parent, max_evaluations=256, seed=42)
        assert result.pose is not None
        assert result.status in ("best_found", "evaluation_budget")
        assert result.optimality_proven is False
        candidate = apply_pose(child, result.pose)
        assert fits(parent, candidate, 0.0)

    def test_donut_avoids_hole(self):
        parent = box(0, 0, 100, 100).difference(box(35, 35, 65, 65))
        child = box(-5, -5, 5, 5)
        result = fit_inside(child, parent, max_evaluations=256, seed=42)
        assert result.pose is not None
        candidate = apply_pose(child, result.pose)
        hole = box(35, 35, 65, 65)
        assert candidate.intersection(hole).area == pytest.approx(0.0)

    def test_reproducible(self):
        parent = box(0, 0, 100, 100)
        child = box(-10, -10, 10, 10)
        r1 = fit_inside(child, parent, max_evaluations=256, seed=42)
        r2 = fit_inside(child, parent, max_evaluations=256, seed=42)
        assert r1.pose.tx == r2.pose.tx
        assert r1.pose.ty == r2.pose.ty
        assert r1.pose.scale == r2.pose.scale
        assert r1.pose.angle_deg == r2.pose.angle_deg

    def test_no_feasible_candidate(self):
        # A child 100x the container's linear size cannot fit at ANY scale
        # (even a tiny one) while keeping clearance: the container is a
        # 10x10 box, the child is 200x200 centred.  With padding 5 the
        # usable region is empty → no candidate exists.
        parent = box(0, 0, 10, 10)
        child = box(-100, -100, 100, 100)
        result = fit_inside(child, parent, padding_mm=5.0,
                            max_evaluations=256, seed=42)
        assert result.pose is None
        assert result.status in ("empty_container", "no_feasible_candidate")

    def test_fixed_center(self):
        parent = box(0, 0, 100, 100)
        child = box(-10, -10, 10, 10)
        result = fit_inside(child, parent, fixed_center=(50, 50),
                            max_evaluations=256, seed=42)
        assert result.pose is not None
        assert result.pose.tx == pytest.approx(50.0)
        assert result.pose.ty == pytest.approx(50.0)

    def test_obstacles_avoided(self):
        parent = box(0, 0, 100, 100)
        child = box(-10, -10, 10, 10)
        obstacle = box(40, 40, 60, 60)
        result = fit_inside(child, parent, obstacles=[obstacle],
                            sibling_gap_mm=2.0, max_evaluations=256, seed=42)
        assert result.pose is not None
        candidate = apply_pose(child, result.pose)
        assert candidate.intersection(obstacle).area == pytest.approx(0.0)

    def test_uncentred_asset_rejected(self):
        parent = box(0, 0, 100, 100)
        off_centre = box(0, 0, 20, 20)
        with pytest.raises(FittingError) as exc:
            fit_inside(off_centre, parent, max_evaluations=256)
        assert exc.value.code == "UNCENTRED_ASSET"
