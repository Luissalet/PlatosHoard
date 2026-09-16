"""Contour constrain: child SVG polygon must stay inside parent."""
import pytest
from shapely.geometry import box

from silhouettes.editor.api import _constrain_pose_to_parent
from silhouettes.editor.constraints import fits
from silhouettes.editor.fitting import FittingError
from silhouettes.editor.transforms import Pose, apply_pose, compose_pose, world_pose


def _doc_with_nested_boxes():
    """Parent 100×100, child 40×40 (centred local), child nested under parent."""
    parent_local = box(-50, -50, 50, 50)
    child_local = box(-20, -20, 20, 20)
    return {
        "revision": 1,
        "layers": {
            "parent": {
                "id": "parent",
                "asset_id": "a_parent",
                "parent_id": None,
                "pose": {"tx": 100, "ty": 100, "scale": 1, "angle_deg": 0},
                "visible": True,
            },
            "child": {
                "id": "child",
                "asset_id": "a_child",
                "parent_id": "parent",
                "pose": {"tx": 0, "ty": 0, "scale": 1, "angle_deg": 0},
                "visible": True,
            },
        },
        "assets": {
            "a_parent": {"_geometry_local": parent_local, "mm_per_source_unit": 1.0},
            "a_child": {"_geometry_local": child_local, "mm_per_source_unit": 1.0},
        },
    }


def _assert_child_inside(doc, pose_local, padding=0.0):
    pw = world_pose("parent", doc["layers"])
    world = compose_pose(pw, pose_local)
    child = apply_pose(doc["assets"]["a_child"]["_geometry_local"], world)
    parent = apply_pose(doc["assets"]["a_parent"]["_geometry_local"], pw)
    assert fits(parent, child, padding)


def test_constrain_keeps_valid_pose():
    doc = _doc_with_nested_boxes()
    pose = Pose(tx=0, ty=0, scale=1.0, angle_deg=0)
    out = _constrain_pose_to_parent(doc, "child", pose, padding_mm=0)
    assert out.scale == pytest.approx(1.0)
    assert out.tx == pytest.approx(0.0)
    assert out.ty == pytest.approx(0.0)
    _assert_child_inside(doc, out)


def test_constrain_shrinks_when_child_crosses_parent():
    doc = _doc_with_nested_boxes()
    # scale=3 → child half-extent 60 > parent 50 → crosses
    pose = Pose(tx=0, ty=0, scale=3.0, angle_deg=0)
    out = _constrain_pose_to_parent(doc, "child", pose, padding_mm=0)
    assert out.scale < 3.0
    _assert_child_inside(doc, out)


def test_constrain_tiny_parent_still_inside():
    doc = _doc_with_nested_boxes()
    doc["assets"]["a_parent"]["_geometry_local"] = box(-1, -1, 1, 1)
    pose = Pose(tx=0, ty=0, scale=1.0, angle_deg=0)
    try:
        out = _constrain_pose_to_parent(doc, "child", pose, padding_mm=0)
    except FittingError:
        return  # acceptable if budget finds nothing
    assert out.scale < 0.1
    _assert_child_inside(doc, out)
