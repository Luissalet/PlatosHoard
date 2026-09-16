"""Tests for task 11 — constraints (padding, inclusion, collisions)."""
import pytest
from shapely.geometry import box, Polygon

from silhouettes.editor.constraints import (
    ConstraintError, fits, siblings_clear, inner_canvas, canvas_shape,
    check_layer,
)


def make_doc(layers=None, canvas=None):
    canvas = canvas or {"width_mm": 200, "height_mm": 200,
                        "padding_mm": {"left": 10, "right": 10, "top": 10, "bottom": 10}}
    return {
        "schema_version": 2, "id": "doc_t", "name": "t", "revision": 1, "units": "mm",
        "canvas": canvas, "default_extrusion_mm": 3, "stack_gap_mm": 0,
        "assets": {"a1": {"id": "a1", "source_type": "svg",
                          "source_uri": "assets/a1/source.svg",
                          "canonical_svg_uri": "assets/a1/canonical.svg",
                          "source_sha256": "x" * 64, "source_viewbox": [0, 0, 100, 100],
                          "mm_per_source_unit": 1.0,
                          "normalization_pose": {"tx": -50, "ty": -50, "scale": 1, "angle_deg": 0},
                          "geometry_hash": "y" * 64, "trace_settings": {},
                          "curve_tolerance_source": 0.02}},
        "layers": layers or {},
    }


class TestFits:
    def test_padding_5_passes_at_distance_5(self):
        parent = box(0, 0, 100, 100)
        child = box(5, 5, 95, 95)
        assert fits(parent, child, 5.0) is True

    def test_padding_5_fails_at_4_99(self):
        parent = box(0, 0, 100, 100)
        # child edge at 4.99 → clearance 4.99 < 5.0 → must fail
        child = box(4.99, 4.99, 95.01, 95.01)
        assert fits(parent, child, 5.0) is False
        # sanity: the same child passes with a smaller required padding
        assert fits(parent, child, 4.9) is True

    def test_child_inside_hole_fails(self):
        parent = box(0, 0, 100, 100).difference(box(40, 40, 60, 60))
        child = box(42, 42, 58, 58)
        assert fits(parent, child, 0.0) is False

    def test_child_outside_fails(self):
        parent = box(0, 0, 100, 100)
        child = box(95, 95, 105, 105)
        assert fits(parent, child, 0.0) is False


class TestSiblingsClear:
    def test_overlap_fails(self):
        child = box(0, 0, 10, 10)
        other = box(5, 5, 15, 15)
        assert siblings_clear(child, [other], 0.0) is False

    def test_gap_2_distance_1_9_fails(self):
        child = box(0, 0, 10, 10)
        other = box(11.9, 0, 21.9, 10)
        assert siblings_clear(child, [other], 2.0) is False

    def test_gap_2_distance_2_1_passes(self):
        child = box(0, 0, 10, 10)
        other = box(12.1, 0, 22.1, 10)
        assert siblings_clear(child, [other], 2.0) is True


class TestInnerCanvas:
    def test_padding_consumes_canvas(self):
        with pytest.raises(ConstraintError) as exc:
            inner_canvas(100, 100, {"left": 50, "right": 50, "top": 0, "bottom": 0})
        assert exc.value.code == "EMPTY_CONTAINER"

    def test_valid(self):
        u = inner_canvas(200, 200, {"left": 10, "right": 10, "top": 10, "bottom": 10})
        assert u.bounds == (10, 10, 190, 190)


class TestCheckLayer:
    def test_padding_violation(self):
        doc = make_doc()
        doc["assets"]["a1"]["_geometry"] = box(-5, -5, 5, 5)
        doc["layers"]["L"] = {
            "id": "L", "asset_id": "a1", "name": "L", "parent_id": None,
            "order": 0, "stack_rank": 0,
            "pose": {"tx": 15, "ty": 15, "scale": 1, "angle_deg": 0},
            "visible": True, "locked": False, "export_enabled": True,
            "extrusion_mm": None,
            "fit": {"target": "canvas", "hole_id": None, "padding_mm": 5,
                    "avoid_siblings": False, "sibling_gap_mm": 0,
                    "auto_scale_while_dragging": False, "allow_rotation": False,
                    "rotation_half_range_deg": 20},
        }
        # child world = box(10,10,20,20); U = box(10,10,190,190) → distance 0 < 5
        v = check_layer(doc, "L")
        codes = [x["code"] for x in v]
        assert "PADDING_VIOLATION" in codes

    def test_outside_canvas(self):
        doc = make_doc()
        doc["assets"]["a1"]["_geometry"] = box(-5, -5, 5, 5)
        doc["layers"]["L"] = {
            "id": "L", "asset_id": "a1", "name": "L", "parent_id": None,
            "order": 0, "stack_rank": 0,
            "pose": {"tx": 210, "ty": 100, "scale": 1, "angle_deg": 0},
            "visible": True, "locked": False, "export_enabled": True,
            "extrusion_mm": None,
            "fit": {"target": "canvas", "hole_id": None, "padding_mm": 0,
                    "avoid_siblings": False, "sibling_gap_mm": 0,
                    "auto_scale_while_dragging": False, "allow_rotation": False,
                    "rotation_half_range_deg": 20},
        }
        v = check_layer(doc, "L")
        codes = [x["code"] for x in v]
        assert "OUTSIDE_CANVAS" in codes

    def test_valid_layer_no_violations(self):
        doc = make_doc()
        doc["assets"]["a1"]["_geometry"] = box(-5, -5, 5, 5)
        doc["layers"]["L"] = {
            "id": "L", "asset_id": "a1", "name": "L", "parent_id": None,
            "order": 0, "stack_rank": 0,
            "pose": {"tx": 100, "ty": 100, "scale": 1, "angle_deg": 0},
            "visible": True, "locked": False, "export_enabled": True,
            "extrusion_mm": None,
            "fit": {"target": "canvas", "hole_id": None, "padding_mm": 5,
                    "avoid_siblings": False, "sibling_gap_mm": 0,
                    "auto_scale_while_dragging": False, "allow_rotation": False,
                    "rotation_half_range_deg": 20},
        }
        v = check_layer(doc, "L")
        assert v == []
