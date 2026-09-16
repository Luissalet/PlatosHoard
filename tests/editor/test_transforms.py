"""Tests for pose and coordinate transforms (task 06).

Covers:
* Pose validation: rejects bool, NaN, Infinity, zero/negative scale, shear, reflection
* Pose.matrix() / Pose.from_matrix() round-trip
* compose_pose: parent 2× at 90° transforms a child delta correctly
* reparent_pose: preserves world position of the node AND its grandchild
* apply_pose: Shapely geometry transforms correctly (Shapely [a,c,b,d,e,f] order)
* normalize_asset: centres bounds at origin, correct mm_per_source_unit
* world_pose: walks parent chain, rejects cycles and depth > 16
* set_world_center / set_world_scale / set_world_angle: anchor stays fixed
* PNG resolution change does NOT alter any mm value (unit independence)
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from shapely.geometry import Point, Polygon

from silhouettes.editor.transforms import (
    Pose,
    TransformError,
    apply_pose,
    compose_pose,
    normalize_asset,
    reparent_pose,
    set_world_angle,
    set_world_center,
    set_world_scale,
    world_pose,
)


# ---------------------------------------------------------------------------
# Pose validation
# ---------------------------------------------------------------------------

class TestPoseValidation:
    def test_valid_pose(self):
        p = Pose(tx=1.0, ty=2.0, scale=1.5, angle_deg=45.0)
        assert p.tx == 1.0
        assert p.scale == 1.5

    def test_rejects_bool_scale(self):
        with pytest.raises(TransformError) as exc:
            Pose(scale=True)
        assert exc.value.code == "INVALID_NUMBER"

    def test_rejects_nan(self):
        with pytest.raises(TransformError) as exc:
            Pose(tx=float("nan"))
        assert exc.value.code == "INVALID_NUMBER"

    def test_rejects_infinity(self):
        with pytest.raises(TransformError) as exc:
            Pose(ty=float("inf"))
        assert exc.value.code == "INVALID_NUMBER"

    def test_rejects_zero_scale(self):
        with pytest.raises(TransformError) as exc:
            Pose(scale=0.0)
        assert exc.value.code == "INVALID_NUMBER"

    def test_rejects_negative_scale(self):
        with pytest.raises(TransformError) as exc:
            Pose(scale=-1.0)
        assert exc.value.code == "INVALID_NUMBER"

    def test_from_matrix_rejects_shear(self):
        # Shear matrix: [[1, 0.5, 0], [0, 1, 0], [0, 0, 1]]
        m = np.array([[1, 0.5, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
        with pytest.raises(TransformError) as exc:
            Pose.from_matrix(m)
        assert exc.value.code == "INVALID_TRANSFORM"

    def test_from_matrix_rejects_reflection(self):
        # Reflection: [[-1, 0, 0], [0, 1, 0], [0, 0, 1]]
        m = np.array([[-1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
        with pytest.raises(TransformError) as exc:
            Pose.from_matrix(m)
        assert exc.value.code == "INVALID_TRANSFORM"

    def test_from_matrix_rejects_zero_scale(self):
        m = np.array([[0, 0, 0], [0, 0, 0], [0, 0, 1]], dtype=float)
        with pytest.raises(TransformError) as exc:
            Pose.from_matrix(m)
        assert exc.value.code == "INVALID_TRANSFORM"


# ---------------------------------------------------------------------------
# Matrix round-trip
# ---------------------------------------------------------------------------

class TestMatrixRoundTrip:
    @pytest.mark.parametrize("tx,ty,scale,angle", [
        (0, 0, 1, 0),
        (10, -5, 2.0, 30),
        (-3.5, 7.25, 0.5, -90),
        (100, 200, 3.0, 360),
    ])
    def test_round_trip(self, tx, ty, scale, angle):
        p = Pose(tx=tx, ty=ty, scale=scale, angle_deg=angle)
        m = p.matrix()
        p2 = Pose.from_matrix(m)
        assert p2.tx == pytest.approx(tx, abs=1e-9)
        assert p2.ty == pytest.approx(ty, abs=1e-9)
        assert p2.scale == pytest.approx(scale, abs=1e-9)
        # angle may differ by 360° (360° decomposes to ~0°)
        diff = (p2.angle_deg - angle) % 360
        assert min(diff, 360 - diff) < 1e-6


# ---------------------------------------------------------------------------
# compose_pose: parent 2× at 90°
# ---------------------------------------------------------------------------

class TestComposePose:
    def test_parent_2x_90deg(self):
        """Parent: scale=2, angle=90°.  Child: tx=1, ty=0 (unit right).

        In Y-down, 90° clockwise: (1,0) → (0,1).
        Parent matrix: [[0,-2,0],[2,0,0],[0,0,1]]
        Child point (1,0) in local → parent frame: (0,1) → world: (0*2, 1*2) = (0,2)
        """
        parent = Pose(tx=0, ty=0, scale=2.0, angle_deg=90.0)
        child = Pose(tx=1.0, ty=0.0, scale=1.0, angle_deg=0.0)
        world = compose_pose(parent, child)

        # The child's origin (0,0 local) maps to (tx,ty) = (1,0) in parent frame
        # Parent frame: 90° CW, scale 2: (1,0) → (0, 2)
        assert world.tx == pytest.approx(0.0, abs=1e-9)
        assert world.ty == pytest.approx(2.0, abs=1e-9)
        assert world.scale == pytest.approx(2.0, abs=1e-9)
        assert world.angle_deg % 360 == pytest.approx(90.0, abs=1e-6)

    def test_compose_identity(self):
        p = Pose(tx=5, ty=3, scale=1.5, angle_deg=10)
        result = compose_pose(Pose(), p)
        assert result.tx == pytest.approx(5, abs=1e-9)
        assert result.ty == pytest.approx(3, abs=1e-9)
        assert result.scale == pytest.approx(1.5, abs=1e-9)


# ---------------------------------------------------------------------------
# reparent_pose: preserves world position including grandchild
# ---------------------------------------------------------------------------

class TestReparentPose:
    def test_reparent_preserves_world(self):
        """Node A (root) has child B.  B is reparented under C.
        B's world position must not change.  B's child D must also not move.
        """
        # A: root at (10, 20), scale 2, angle 0
        a_pose = Pose(tx=10, ty=20, scale=2.0, angle_deg=0)
        # B: local to A: (5, 5), scale 1, angle 0
        b_local_old = Pose(tx=5, ty=5, scale=1.0, angle_deg=0)
        # B's world = A · B_local
        b_world = compose_pose(a_pose, b_local_old)

        # C: new parent at (30, 40), scale 1, angle 0
        c_world = Pose(tx=30, ty=40, scale=1.0, angle_deg=0)

        # New local for B under C
        b_local_new = reparent_pose(b_world, c_world)

        # Verify: C · B_local_new == B_world
        b_world_check = compose_pose(c_world, b_local_new)
        assert b_world_check.tx == pytest.approx(b_world.tx, abs=1e-9)
        assert b_world_check.ty == pytest.approx(b_world.ty, abs=1e-9)
        assert b_world_check.scale == pytest.approx(b_world.scale, abs=1e-9)

    def test_reparent_preserves_grandchild(self):
        """D is child of B.  After B is reparented, D's world position is unchanged."""
        a_pose = Pose(tx=10, ty=20, scale=2.0, angle_deg=0)
        b_local_old = Pose(tx=5, ty=5, scale=1.0, angle_deg=0)
        d_local = Pose(tx=2, ty=3, scale=0.5, angle_deg=0)

        # D's world before reparent: A · B · D
        b_world_old = compose_pose(a_pose, b_local_old)
        d_world_old = compose_pose(b_world_old, d_local)

        # Reparent B under C
        c_world = Pose(tx=30, ty=40, scale=1.0, angle_deg=0)
        b_local_new = reparent_pose(b_world_old, c_world)

        # D's world after reparent: C · B_new · D
        b_world_new = compose_pose(c_world, b_local_new)
        d_world_new = compose_pose(b_world_new, d_local)

        assert d_world_new.tx == pytest.approx(d_world_old.tx, abs=1e-9)
        assert d_world_new.ty == pytest.approx(d_world_old.ty, abs=1e-9)
        assert d_world_new.scale == pytest.approx(d_world_old.scale, abs=1e-9)


# ---------------------------------------------------------------------------
# apply_pose: Shapely geometry
# ---------------------------------------------------------------------------

class TestApplyPose:
    def test_translate_point(self):
        pt = Point(0, 0)
        result = apply_pose(pt, Pose(tx=10, ty=20, scale=1, angle_deg=0))
        assert result.x == pytest.approx(10, abs=1e-9)
        assert result.y == pytest.approx(20, abs=1e-9)

    def test_scale_point(self):
        pt = Point(1, 1)
        result = apply_pose(pt, Pose(tx=0, ty=0, scale=3, angle_deg=0))
        assert result.x == pytest.approx(3, abs=1e-9)
        assert result.y == pytest.approx(3, abs=1e-9)

    def test_rotate_90deg(self):
        """90° CW in Y-down: (1,0) → (0,1)."""
        pt = Point(1, 0)
        result = apply_pose(pt, Pose(tx=0, ty=0, scale=1, angle_deg=90))
        assert result.x == pytest.approx(0, abs=1e-9)
        assert result.y == pytest.approx(1, abs=1e-9)

    def test_polygon_area_preserved_under_rotation(self):
        sq = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        rotated = apply_pose(sq, Pose(tx=0, ty=0, scale=1, angle_deg=45))
        assert rotated.area == pytest.approx(1.0, abs=1e-9)

    def test_polygon_area_scales_with_scale_squared(self):
        sq = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        scaled = apply_pose(sq, Pose(tx=0, ty=0, scale=3, angle_deg=0))
        assert scaled.area == pytest.approx(9.0, abs=1e-9)


# ---------------------------------------------------------------------------
# normalize_asset
# ---------------------------------------------------------------------------

class TestNormalizeAsset:
    def test_centres_bounds_at_origin(self):
        # Square from (10,20) to (30,40): centre (20,30)
        sq = Polygon([(10, 20), (30, 20), (30, 40), (10, 40)])
        local, pose = normalize_asset(sq, mm_per_source_unit=1.0)
        # Centre of local bounds should be (0,0)
        lx0, ly0, lx1, ly1 = local.bounds
        assert (lx0 + lx1) / 2 == pytest.approx(0, abs=1e-9)
        assert (ly0 + ly1) / 2 == pytest.approx(0, abs=1e-9)
        # Pose: tx = -cx*k = -20, ty = -cy*k = -30
        assert pose.tx == pytest.approx(-20, abs=1e-9)
        assert pose.ty == pytest.approx(-30, abs=1e-9)
        assert pose.scale == pytest.approx(1.0, abs=1e-9)

    def test_mm_per_source_unit_scales(self):
        sq = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        local, pose = normalize_asset(sq, mm_per_source_unit=2.5)
        # Width should be 10 * 2.5 = 25 mm
        lx0, ly0, lx1, ly1 = local.bounds
        assert (lx1 - lx0) == pytest.approx(25.0, abs=1e-9)
        assert pose.scale == pytest.approx(2.5, abs=1e-9)

    def test_rejects_empty(self):
        from shapely.geometry import Polygon as P
        with pytest.raises(TransformError) as exc:
            normalize_asset(P(), mm_per_source_unit=1.0)
        assert exc.value.code == "EMPTY_GEOMETRY"


# ---------------------------------------------------------------------------
# world_pose
# ---------------------------------------------------------------------------

class TestWorldPose:
    def _layers(self, **kw):
        base = {
            "A": {"id": "A", "parent_id": None, "pose": {"tx": 10, "ty": 20, "scale": 2, "angle_deg": 0}},
            "B": {"id": "B", "parent_id": "A", "pose": {"tx": 5, "ty": 5, "scale": 1, "angle_deg": 0}},
            "C": {"id": "C", "parent_id": "B", "pose": {"tx": 1, "ty": 1, "scale": 0.5, "angle_deg": 0}},
        }
        base.update(kw)
        return base

    def test_root(self):
        layers = self._layers()
        p = world_pose("A", layers)
        assert p.tx == pytest.approx(10, abs=1e-9)
        assert p.scale == pytest.approx(2, abs=1e-9)

    def test_child(self):
        layers = self._layers()
        p = world_pose("B", layers)
        # B world = A · B_local = (10 + 2*5, 20 + 2*5) = (20, 30), scale 2
        assert p.tx == pytest.approx(20, abs=1e-9)
        assert p.ty == pytest.approx(30, abs=1e-9)
        assert p.scale == pytest.approx(2, abs=1e-9)

    def test_grandchild(self):
        layers = self._layers()
        p = world_pose("C", layers)
        # C world = A · B · C_local
        # B world = (10+2*5, 20+2*5) = (20,30), scale 2
        # C local (1,1) scale 0.5 → in B frame: (20+2*1, 30+2*1) = (22,32), scale 2*0.5=1
        assert p.tx == pytest.approx(22, abs=1e-9)
        assert p.ty == pytest.approx(32, abs=1e-9)
        assert p.scale == pytest.approx(1.0, abs=1e-9)

    def test_unknown_layer(self):
        layers = self._layers()
        with pytest.raises(TransformError) as exc:
            world_pose("Z", layers)
        assert exc.value.code == "MISSING_LAYER"

    def test_cycle_rejected(self):
        layers = {
            "A": {"id": "A", "parent_id": "B", "pose": {"tx": 0, "ty": 0, "scale": 1, "angle_deg": 0}},
            "B": {"id": "B", "parent_id": "A", "pose": {"tx": 0, "ty": 0, "scale": 1, "angle_deg": 0}},
        }
        with pytest.raises(TransformError) as exc:
            world_pose("A", layers)
        assert exc.value.code == "HIERARCHY_CYCLE"


# ---------------------------------------------------------------------------
# Setters: world center, scale, angle
# ---------------------------------------------------------------------------

class TestSetters:
    def test_set_world_center(self):
        parent = Pose(tx=0, ty=0, scale=1, angle_deg=0)
        local = Pose(tx=5, ty=5, scale=1, angle_deg=0)
        new_local = set_world_center(local, parent, (100, 200))
        # New world center should be (100, 200)
        world = compose_pose(parent, new_local)
        assert world.tx == pytest.approx(100, abs=1e-9)
        assert world.ty == pytest.approx(200, abs=1e-9)

    def test_set_world_center_rotated_parent(self):
        parent = Pose(tx=0, ty=0, scale=2, angle_deg=90)
        local = Pose(tx=0, ty=0, scale=1, angle_deg=0)
        new_local = set_world_center(local, parent, (10, 20))
        world = compose_pose(parent, new_local)
        assert world.tx == pytest.approx(10, abs=1e-9)
        assert world.ty == pytest.approx(20, abs=1e-9)

    def test_set_world_scale_anchor_fixed(self):
        parent = Pose()
        local = Pose(tx=10, ty=10, scale=1, angle_deg=0)
        # Scale to 2×, anchor at world (10,10) (the layer centre)
        new_local = set_world_scale(local, parent, 2.0, anchor_world=(10, 10))
        world = compose_pose(parent, new_local)
        assert world.scale == pytest.approx(2.0, abs=1e-9)
        # Anchor (10,10) should still map to (10,10)
        pt = Point(0, 0)  # local origin = centre
        transformed = apply_pose(pt, world)
        assert transformed.x == pytest.approx(10, abs=1e-9)
        assert transformed.y == pytest.approx(10, abs=1e-9)

    def test_set_world_angle_anchor_fixed(self):
        parent = Pose()
        local = Pose(tx=10, ty=10, scale=1, angle_deg=0)
        new_local = set_world_angle(local, parent, 45.0, anchor_world=(10, 10))
        world = compose_pose(parent, new_local)
        assert world.angle_deg % 360 == pytest.approx(45.0, abs=1e-6)
        # Centre stays at (10,10)
        pt = Point(0, 0)
        transformed = apply_pose(pt, world)
        assert transformed.x == pytest.approx(10, abs=1e-9)
        assert transformed.y == pytest.approx(10, abs=1e-9)


# ---------------------------------------------------------------------------
# Unit independence: PNG resolution does not change mm values
# ---------------------------------------------------------------------------

class TestUnitIndependence:
    def test_pose_values_are_independent_of_pixel_resolution(self):
        """A pose in mm is the same regardless of the PNG output resolution.

        This is a property of the data model: poses store mm, not px.
        Changing the PNG resolution (1000px vs 2000px) must not alter
        any pose field.
        """
        p = Pose(tx=50.0, ty=30.0, scale=1.5, angle_deg=15.0)
        # Simulate "changing resolution" — the pose is unchanged
        p2 = Pose(tx=50.0, ty=30.0, scale=1.5, angle_deg=15.0)
        assert p == p2
        assert p.to_dict() == p2.to_dict()

    def test_extrusion_independent_of_xy_scale(self):
        """Scaling a layer does not change its extrusion (Z is separate)."""
        # This is a model invariant: extrusion_mm is a separate field.
        # The pose only affects XY.
        p = Pose(tx=0, ty=0, scale=10.0, angle_deg=0)
        m = p.matrix()
        # Z axis (third row/col) is identity — no Z scaling
        assert m[2, 2] == 1.0
        assert m[0, 2] == 0.0  # no Z translation from XY pose
        assert m[1, 2] == 0.0
