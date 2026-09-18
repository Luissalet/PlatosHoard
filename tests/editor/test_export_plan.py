"""Export plan: 4 silhouette families + single marco tray."""
from __future__ import annotations

import pytest
from shapely.geometry import box

from silhouettes.editor.exports import build_export_plan, pack_bundle
from silhouettes.editor.frame import (
    build_frame_box_assets_and_layers,
    build_frame_floor,
)
from silhouettes.editor.transforms import Pose, apply_pose


BATCH_FAMILIES = (
    "normal_registered",
    "inverse_registered",
    "normal_fullframe",
    "inverse_fullframe",
)


def _square_layer(lid: str, aid: str, name: str, *, tx=100.0, ty=100.0, scale=0.4):
    return {
        "id": lid,
        "asset_id": aid,
        "name": name,
        "parent_id": None,
        "visible": True,
        "export_enabled": True,
        "stack_rank": 1,
        "pose": {"tx": tx, "ty": ty, "scale": scale, "angle_deg": 0.0},
        "extrusion_mm": 3.0,
    }


def _doc_with_silhouette_and_marco():
    assets = {
        "a_bulb": {
            "id": "a_bulb",
            "name": "Bulbasaur",
            "_geometry": box(-40, -40, 40, 40),
        },
    }
    layers = {
        "L_bulb": _square_layer("L_bulb", "a_bulb", "Bulbasaur", scale=0.5),
    }
    frame_assets, frame_layers, dims = build_frame_box_assets_and_layers(
        200, 200, padding_mm=1, wall_w_mm=12, wall_h_mm=12,
    )
    floor_g = build_frame_floor(dims)
    ow, oh = dims["outer_w"], dims["outer_h"]
    floor_local = apply_pose(floor_g, Pose(-ow / 2, -oh / 2, 1.0))

    for a in frame_assets:
        a["_geometry"] = floor_local
        assets[a["id"]] = a
    for i, layer in enumerate(frame_layers):
        layer = {
            **layer,
            "parent_id": None,
            "visible": True,
            "export_enabled": True,
            "stack_rank": -1,
            "extrusion_mm": float(dims["wall_h_mm"]),
        }
        layers[layer["id"]] = layer
    return assets, layers, dims


def test_export_rehydrates_geometry_from_canonical_svg():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" '
        'viewBox="0 0 10 10"><path d="M0,0 H10 V10 H0 Z"/></svg>'
    )
    assets = {
        "a1": {
            "id": "a1",
            "name": "Square",
            "canonical_svg": svg,
            "mm_per_source_unit": 1.0,
        },
    }
    layers = {
        "L1": {
            "id": "L1",
            "asset_id": "a1",
            "name": "Square",
            "parent_id": None,
            "visible": True,
            "export_enabled": True,
            "stack_rank": 0,
            "pose": {"tx": 100.0, "ty": 100.0, "scale": 5.0, "angle_deg": 0.0},
            "extrusion_mm": 3.0,
        },
    }
    plan = build_export_plan(
        layers, assets, ["L1"], 200, 200,
        {"top": 5, "right": 5, "bottom": 5, "left": 5},
        families=("normal_registered",),
    )
    assert len(plan) == 1
    assert not plan[0].geometry.is_empty
    assert assets["a1"].get("_geometry_local") is not None


def test_batch_silhouettes_get_four_families():
    assets, layers, _ = _doc_with_silhouette_and_marco()
    plan = build_export_plan(
        layers, assets, ["L_bulb"], 200, 200,
        {"top": 5, "right": 5, "bottom": 5, "left": 5},
        families=BATCH_FAMILIES,
    )
    fams = sorted(item.family for item in plan)
    assert fams == sorted(BATCH_FAMILIES)
    assert all(item.stem.startswith(item.family + "/") for item in plan)


def test_marco_exports_once_as_single_tray():
    assets, layers, _ = _doc_with_silhouette_and_marco()
    marco_ids = [lid for lid, n in layers.items() if str(lid).startswith("layer_marco_")]
    assert len(marco_ids) == 1

    plan = build_export_plan(
        layers, assets, ["L_bulb", *marco_ids], 200, 200,
        {"top": 5, "right": 5, "bottom": 5, "left": 5},
        families=BATCH_FAMILIES,
    )
    silhouette = [i for i in plan if i.layer_id == "L_bulb"]
    marco = [i for i in plan if i.layer_id in marco_ids]

    assert len(silhouette) == 4
    assert len(marco) == 1
    assert marco[0].stem.startswith("marco/")
    assert marco[0].family == "marco"
    assert marco[0].tray_wall_w_mm == pytest.approx(12)
    assert not marco[0].geometry.is_empty


def test_default_families_include_inverse_fullframe():
    assets, layers, _ = _doc_with_silhouette_and_marco()
    plan = build_export_plan(
        layers, assets, ["L_bulb"], 200, 200,
        {"top": 5, "right": 5, "bottom": 5, "left": 5},
    )
    assert "inverse_fullframe" in {i.family for i in plan}
    assert len(plan) == 4


def test_pack_bundle_accepts_marco_outside_canvas():
    from silhouettes.editor.mesh_adapter import export_stl

    assets, layers, _ = _doc_with_silhouette_and_marco()
    marco_ids = [lid for lid in layers if str(lid).startswith("layer_marco_")]
    plan = build_export_plan(
        layers, assets, marco_ids, 200, 200,
        {"top": 5, "right": 5, "bottom": 5, "left": 5},
        families=BATCH_FAMILIES,
    )
    assert len(plan) == 1
    blob = pack_bundle(plan, 200, 200, formats=("svg", "stl"), stl_exporter=export_stl)
    assert len(blob) > 100


def test_coplanar_siblings_share_one_inverse_plate():
    """Two layers at the same level → ONE inverse STL with both holes."""
    assets = {
        "a_l": {"id": "a_l", "name": "Left", "_geometry": box(-20, -20, 20, 20)},
        "a_r": {"id": "a_r", "name": "Right", "_geometry": box(-20, -20, 20, 20)},
    }
    layers = {
        "L_left": _square_layer("L_left", "a_l", "Left", tx=60.0, ty=100.0, scale=1.0),
        "L_right": _square_layer("L_right", "a_r", "Right", tx=140.0, ty=100.0, scale=1.0),
    }
    layers["L_right"]["stack_rank"] = 2

    plan = build_export_plan(
        layers, assets, ["L_left", "L_right"], 200.0, 200.0,
        {"top": 1, "right": 1, "bottom": 1, "left": 1},
        families=BATCH_FAMILIES,
    )

    inverses = [i for i in plan if i.family == "inverse_registered"]
    assert len(inverses) == 1
    item = inverses[0]
    assert item.stem.startswith("inverse_registered/nivel_00_")
    # One plate, two holes.
    poly = item.geometry
    assert poly.geom_type == "Polygon"
    assert len(poly.interiors) == 2
    assert poly.area == pytest.approx(200 * 200 - 2 * 40 * 40, rel=1e-6)

    # The other families are still one piece per layer.
    for family in ("normal_registered", "normal_fullframe", "inverse_fullframe"):
        assert len([i for i in plan if i.family == family]) == 2


def test_nested_levels_are_not_merged():
    """A child sits at another level → its own inverse plate."""
    assets = {
        "a_p": {"id": "a_p", "name": "Parent", "_geometry": box(-60, -60, 60, 60)},
        "a_c": {"id": "a_c", "name": "Child", "_geometry": box(-20, -20, 20, 20)},
    }
    layers = {
        "L_p": _square_layer("L_p", "a_p", "Parent", tx=100.0, ty=100.0, scale=1.0),
        "L_c": _square_layer("L_c", "a_c", "Child", tx=0.0, ty=0.0, scale=0.5),
    }
    layers["L_c"]["parent_id"] = "L_p"
    layers["L_c"]["stack_rank"] = 2

    plan = build_export_plan(
        layers, assets, ["L_p", "L_c"], 200.0, 200.0,
        {"top": 1, "right": 1, "bottom": 1, "left": 1},
        families=("inverse_registered",),
    )
    inverses = [i for i in plan if i.family == "inverse_registered"]
    assert len(inverses) == 2
    assert not any("nivel_" in i.stem for i in inverses)
