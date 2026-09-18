"""Tests for task 10 — tree commands (set_parent, reorder, delete, duplicate, locks)."""
import pytest

from silhouettes.editor.commands import (
    CommandError, cmd_set_parent, cmd_reorder_siblings, cmd_set_stack_order,
    cmd_delete_subtree, cmd_duplicate_subtree, cmd_apply_fit_result, cmd_set_pose,
)
from silhouettes.editor.transforms import world_pose


def make_doc():
    """Minimal valid document with two root layers A and R."""
    return {
        "schema_version": 2,
        "id": "doc_test",
        "name": "test",
        "revision": 7,
        "units": "mm",
        "canvas": {"width_mm": 200, "height_mm": 200,
                   "padding_mm": {"left": 5, "right": 5, "top": 5, "bottom": 5}},
        "default_extrusion_mm": 3,
        "stack_gap_mm": 0,
        "assets": {
            "a1": {"id": "a1", "source_type": "svg", "source_uri": "assets/a1/source.svg",
                   "canonical_svg_uri": "assets/a1/canonical.svg",
                   "source_sha256": "x" * 64, "source_viewbox": [0, 0, 100, 100],
                   "mm_per_source_unit": 1.0,
                   "normalization_pose": {"tx": -50, "ty": -50, "scale": 1, "angle_deg": 0},
                   "geometry_hash": "y" * 64, "trace_settings": {}, "curve_tolerance_source": 0.02},
        },
        "layers": {
            "A": {"id": "A", "asset_id": "a1", "name": "A", "parent_id": None,
                  "order": 0, "stack_rank": 0,
                  "pose": {"tx": 100, "ty": 100, "scale": 2, "angle_deg": 0},
                  "visible": True, "locked": False, "export_enabled": True,
                  "extrusion_mm": None,
                  "fit": {"target": "canvas", "hole_id": None, "padding_mm": 0,
                          "avoid_siblings": True, "sibling_gap_mm": 2,
                          "auto_scale_while_dragging": False, "allow_rotation": False,
                          "rotation_half_range_deg": 20}},
            "R": {"id": "R", "asset_id": "a1", "name": "R", "parent_id": None,
                  "order": 1, "stack_rank": 1,
                  "pose": {"tx": 50, "ty": 50, "scale": 1, "angle_deg": 0},
                  "visible": True, "locked": False, "export_enabled": True,
                  "extrusion_mm": None,
                  "fit": {"target": "canvas", "hole_id": None, "padding_mm": 0,
                          "avoid_siblings": True, "sibling_gap_mm": 2,
                          "auto_scale_while_dragging": False, "allow_rotation": False,
                          "rotation_half_range_deg": 20}},
        },
    }


class TestSetParent:
    def test_preserves_world_position(self):
        doc = make_doc()
        before = world_pose("A", doc["layers"])
        cmd_set_parent(doc, {"layer_id": "A", "new_parent_id": "R"})
        after = world_pose("A", doc["layers"])
        assert abs(before.tx - after.tx) < 1e-9
        assert abs(before.ty - after.ty) < 1e-9
        assert abs(before.scale - after.scale) < 1e-9
        assert abs(before.angle_deg - after.angle_deg) < 1e-9
        assert doc["layers"]["A"]["parent_id"] == "R"

    def test_cycle_rejected(self):
        doc = make_doc()
        # A is root; make R a child of A, then try to make A a child of R
        cmd_set_parent(doc, {"layer_id": "R", "new_parent_id": "A"})
        with pytest.raises(CommandError) as exc:
            cmd_set_parent(doc, {"layer_id": "A", "new_parent_id": "R"})
        assert exc.value.code == "HIERARCHY_CYCLE"

    def test_self_parent_rejected(self):
        doc = make_doc()
        with pytest.raises(CommandError) as exc:
            cmd_set_parent(doc, {"layer_id": "A", "new_parent_id": "A"})
        assert exc.value.code == "HIERARCHY_CYCLE"

    def test_unknown_parent_rejected(self):
        doc = make_doc()
        with pytest.raises(CommandError) as exc:
            cmd_set_parent(doc, {"layer_id": "A", "new_parent_id": "NOPE"})
        assert exc.value.code == "UNKNOWN_LAYER"
        assert exc.value.status == 404

    def test_detach_to_root(self):
        doc = make_doc()
        cmd_set_parent(doc, {"layer_id": "A", "new_parent_id": "R"})
        cmd_set_parent(doc, {"layer_id": "A", "new_parent_id": None})
        assert doc["layers"]["A"]["parent_id"] is None


class TestReorderSiblings:
    def test_reassigns_order(self):
        doc = make_doc()
        cmd_reorder_siblings(doc, {"parent_id": None, "order": ["R", "A"]})
        assert doc["layers"]["R"]["order"] == 0
        assert doc["layers"]["A"]["order"] == 1

    def test_missing_sibling_rejected(self):
        doc = make_doc()
        with pytest.raises(CommandError) as exc:
            cmd_reorder_siblings(doc, {"parent_id": None, "order": ["A"]})
        assert exc.value.code == "INVALID_STRUCTURE"

    def test_non_sibling_rejected(self):
        doc = make_doc()
        with pytest.raises(CommandError) as exc:
            cmd_reorder_siblings(doc, {"parent_id": "A", "order": ["R"]})
        assert exc.value.code == "INVALID_STRUCTURE"


class TestSetStackOrder:
    def test_reassigns_rank(self):
        doc = make_doc()
        cmd_set_stack_order(doc, {"order": ["R", "A"]})
        assert doc["layers"]["R"]["stack_rank"] == 0
        assert doc["layers"]["A"]["stack_rank"] == 1

    def test_missing_id_rejected(self):
        doc = make_doc()
        with pytest.raises(CommandError) as exc:
            cmd_set_stack_order(doc, {"order": ["A"]})
        assert exc.value.code == "INVALID_STRUCTURE"


class TestDeleteSubtree:
    def test_wrong_confirm_rejected(self):
        doc = make_doc()
        with pytest.raises(CommandError) as exc:
            cmd_delete_subtree(doc, {"layer_id": "A", "confirm_descendants": 99})
        assert exc.value.code == "CONFIRMATION_MISMATCH"

    def test_delete_root(self):
        doc = make_doc()
        cmd_delete_subtree(doc, {"layer_id": "A", "confirm_descendants": 0})
        assert "A" not in doc["layers"]
        assert "a1" in doc["assets"]  # asset remains

    def test_delete_with_descendants(self):
        doc = make_doc()
        cmd_set_parent(doc, {"layer_id": "R", "new_parent_id": "A"})
        cmd_delete_subtree(doc, {"layer_id": "A", "confirm_descendants": 1})
        assert "A" not in doc["layers"]
        assert "R" not in doc["layers"]
        assert "a1" in doc["assets"]


class TestDuplicateSubtree:
    def test_no_id_collision_assets_shared(self):
        doc = make_doc()
        cmd_set_parent(doc, {"layer_id": "R", "new_parent_id": "A"})
        cmd_duplicate_subtree(doc, {"layer_id": "A"})
        new_ids = [lid for lid in doc["layers"] if lid not in ("A", "R")]
        assert len(new_ids) == 2
        # Assets shared
        for lid in new_ids:
            assert doc["layers"][lid]["asset_id"] == "a1"
        # Stack ranks unique
        ranks = [doc["layers"][lid]["stack_rank"] for lid in doc["layers"]]
        assert len(ranks) == len(set(ranks))

    def test_offset_applied_to_root_only(self):
        doc = make_doc()
        cmd_set_parent(doc, {"layer_id": "R", "new_parent_id": "A"})
        cmd_duplicate_subtree(doc, {"layer_id": "A", "offset_mm": {"dx": 10, "dy": 20}})
        new_root = [lid for lid in doc["layers"] if lid.startswith("A_copy")][0]
        assert doc["layers"][new_root]["pose"]["tx"] == 110
        assert doc["layers"][new_root]["pose"]["ty"] == 120


class TestLocks:
    def test_set_pose_locked_rejected(self):
        doc = make_doc()
        doc["layers"]["A"]["locked"] = True
        with pytest.raises(CommandError) as exc:
            cmd_set_pose(doc, {"layer_id": "A",
                               "pose": {"tx": 1, "ty": 2, "scale": 1, "angle_deg": 0}})
        assert exc.value.code == "LOCKED"
        assert exc.value.status == 423

    def test_set_parent_locked_descendant_rejected(self):
        doc = make_doc()
        cmd_set_parent(doc, {"layer_id": "R", "new_parent_id": "A"})
        doc["layers"]["R"]["locked"] = True
        with pytest.raises(CommandError) as exc:
            cmd_set_parent(doc, {"layer_id": "A", "new_parent_id": None})
        assert exc.value.code == "LOCKED"
        assert "R" in exc.value.details["locked_ids"]


class TestApplyFitResult:
    def test_stale_revision_rejected(self):
        doc = make_doc()
        with pytest.raises(CommandError) as exc:
            cmd_apply_fit_result(doc, {
                "layer_id": "A",
                "pose": {"tx": 1, "ty": 2, "scale": 1, "angle_deg": 0},
                "base_revision": 6,
            })
        assert exc.value.code == "REVISION_CONFLICT"
        assert exc.value.status == 409

    def test_current_revision_applies(self):
        doc = make_doc()
        cmd_apply_fit_result(doc, {
            "layer_id": "A",
            "pose": {"tx": 11, "ty": 22, "scale": 1.5, "angle_deg": 5},
            "base_revision": 7,
        })
        assert doc["layers"]["A"]["pose"]["tx"] == 11
        assert doc["layers"]["A"]["pose"]["scale"] == 1.5

    def test_pose_metadata_keys_ignored(self):
        """Client may attach metadata; only the four pose fields are stored."""
        doc = make_doc()
        cmd_apply_fit_result(doc, {
            "layer_id": "A",
            "pose": {
                "tx": 3, "ty": 4, "scale": 2, "angle_deg": 0,
                "_used_padding_mm": 1.5,
            },
            "base_revision": 7,
        })
        assert doc["layers"]["A"]["pose"] == {
            "tx": 3, "ty": 4, "scale": 2, "angle_deg": 0,
        }
