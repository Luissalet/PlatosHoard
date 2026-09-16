"""Tests for task 12 — exact auto-fit to canvas (contain_rect)."""
import pytest
from shapely.geometry import box, Polygon

from silhouettes.editor.fitting import contain_rect, fit_to_canvas, FittingError
from silhouettes.editor.transforms import apply_pose


class TestContainRect:
    def test_exact_numeric_result(self):
        # 100x50 centred asset into a 200x100 rect
        asset = box(-50, -25, 50, 25)
        rect = box(0, 0, 200, 100)
        pose = contain_rect(asset, rect)
        assert pose.scale == pytest.approx(2.0)
        assert pose.tx == pytest.approx(100.0)
        assert pose.ty == pytest.approx(50.0)
        assert pose.angle_deg == 0.0
        # Resulting bounds fill the rect exactly
        result = apply_pose(asset, pose)
        assert result.bounds == pytest.approx((0, 0, 200, 100))

    def test_asymmetric_margins(self):
        # canvas 200x100, padding left=10 right=10 top=0 bottom=0 → U = box(10,0,190,100)
        asset = box(-50, -25, 50, 25)
        U = box(10, 0, 190, 100)
        pose = contain_rect(asset, U)
        assert pose.scale == pytest.approx(1.8)
        assert pose.tx == pytest.approx(100.0)
        assert pose.ty == pytest.approx(50.0)

    def test_rejects_irregular_container(self):
        asset = box(-10, -10, 10, 10)
        triangle = Polygon([(0, 0), (100, 0), (0, 100)])
        with pytest.raises(FittingError) as exc:
            contain_rect(asset, triangle)
        assert exc.value.code == "NOT_RECTANGLE"

    def test_no_tip_clipping(self):
        # A shape with a spike must not be stretched to fill both axes:
        # the scale is uniform, so width/height ratio is preserved.
        spike = Polygon([(-50, 0), (50, 0), (0, 25)])
        rect = box(0, 0, 100, 100)
        pose = contain_rect(spike, rect)
        result = apply_pose(spike, pose)
        # spike is 100 wide x 25 tall → ratio 4:1 must survive the fit
        w = result.bounds[2] - result.bounds[0]
        h = result.bounds[3] - result.bounds[1]
        assert w / h == pytest.approx(4.0)
        # and the whole shape stays inside the rect (no clipping)
        assert result.bounds[0] >= 0 and result.bounds[1] >= 0
        assert result.bounds[2] <= 100 and result.bounds[3] <= 100


class TestFitToCanvas:
    def _doc(self):
        return {
            "schema_version": 2, "id": "d", "name": "d", "revision": 1, "units": "mm",
            "canvas": {"width_mm": 200, "height_mm": 200,
                       "padding_mm": {"left": 10, "right": 10, "top": 10, "bottom": 10}},
            "default_extrusion_mm": 3, "stack_gap_mm": 0,
            "assets": {"a1": {"id": "a1", "source_type": "svg",
                              "source_uri": "assets/a1/source.svg",
                              "canonical_svg_uri": "assets/a1/canonical.svg",
                              "source_sha256": "x" * 64, "source_viewbox": [0, 0, 100, 100],
                              "mm_per_source_unit": 1.0,
                              "normalization_pose": {"tx": -50, "ty": -50, "scale": 1, "angle_deg": 0},
                              "geometry_hash": "y" * 64, "trace_settings": {},
                              "curve_tolerance_source": 0.02,
                              "_geometry": box(-50, -50, 50, 50)}},
            "layers": {
                "P": {"id": "P", "asset_id": "a1", "name": "P", "parent_id": None,
                      "order": 0, "stack_rank": 0,
                      "pose": {"tx": 50, "ty": 50, "scale": 2, "angle_deg": 0},
                      "visible": True, "locked": False, "export_enabled": True,
                      "extrusion_mm": None,
                      "fit": {"target": "canvas", "hole_id": None, "padding_mm": 0,
                              "avoid_siblings": True, "sibling_gap_mm": 2,
                              "auto_scale_while_dragging": False, "allow_rotation": False,
                              "rotation_half_range_deg": 20}},
                "C": {"id": "C", "asset_id": "a1", "name": "C", "parent_id": "P",
                      "order": 0, "stack_rank": 1,
                      "pose": {"tx": 0, "ty": 0, "scale": 1, "angle_deg": 0},
                      "visible": True, "locked": False, "export_enabled": True,
                      "extrusion_mm": None,
                      "fit": {"target": "parent_shape", "hole_id": None, "padding_mm": 0,
                              "avoid_siblings": True, "sibling_gap_mm": 2,
                              "auto_scale_while_dragging": False, "allow_rotation": False,
                              "rotation_half_range_deg": 20}},
            },
        }

    def test_parented_layer_local_differs_from_world(self):
        doc = self._doc()
        result = fit_to_canvas(doc, "C")
        assert result["pose_local"].tx != result["pose_world"].tx or \
               result["pose_local"].ty != result["pose_world"].ty

    def test_pure_no_mutation(self):
        doc = self._doc()
        import copy
        before = copy.deepcopy(doc)
        r1 = fit_to_canvas(doc, "C")
        r2 = fit_to_canvas(doc, "C")
        assert doc == before
        assert r1["pose_world"].tx == r2["pose_world"].tx
        assert r1["pose_world"].scale == r2["pose_world"].scale
