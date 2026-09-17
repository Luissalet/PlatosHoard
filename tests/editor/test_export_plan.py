"""Export plan: 4 silhouette families + marco once as normal."""
from __future__ import annotations

from shapely.geometry import box

from silhouettes.editor.exports import build_export_plan, pack_bundle
from silhouettes.editor.frame import (
    build_frame_box_assets_and_layers,
    build_frame_floor,
    build_frame_ring,
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
    ring_g = build_frame_ring(dims)
    ow, oh = dims["outer_w"], dims["outer_h"]
    floor_local = apply_pose(floor_g, Pose(-ow / 2, -oh / 2, 1.0))
    ring_local = apply_pose(ring_g, Pose(-ow / 2, -oh / 2, 1.0))

    for a, geom in zip(frame_assets, (floor_local, ring_local)):
        a["_geometry"] = geom
        assets[a["id"]] = a
    for i, layer in enumerate(frame_layers):
        layer = {
            **layer,
            "parent_id": None,
            "visible": True,
            "export_enabled": True,
            "stack_rank": -2 + i,
            "extrusion_mm": 3.0 if i == 0 else 12.0,
        }
        layers[layer["id"]] = layer
    return assets, layers, dims


def test_export_rehydrates_geometry_from_canonical_svg():
    """Persisted docs strip ``_geometry``; export must parse canonical SVG."""
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


def test_marco_exports_once_under_marco_folder_not_per_family():
    assets, layers, _ = _doc_with_silhouette_and_marco()
    marco_ids = [lid for lid, n in layers.items() if str(lid).startswith("layer_marco_")]
    assert len(marco_ids) == 2

    plan = build_export_plan(
        layers, assets, ["L_bulb", *marco_ids], 200, 200,
        {"top": 5, "right": 5, "bottom": 5, "left": 5},
        families=BATCH_FAMILIES,
    )
    silhouette = [i for i in plan if i.layer_id == "L_bulb"]
    marco = [i for i in plan if i.layer_id in marco_ids]

    assert len(silhouette) == 4
    assert len(marco) == 2
    assert all(i.stem.startswith("marco/") for i in marco)
    assert all(i.family == "marco" for i in marco)
    assert all(not i.geometry.is_empty for i in marco)


def test_default_families_include_inverse_fullframe():
    assets, layers, _ = _doc_with_silhouette_and_marco()
    plan = build_export_plan(
        layers, assets, ["L_bulb"], 200, 200,
        {"top": 5, "right": 5, "bottom": 5, "left": 5},
    )
    assert "inverse_fullframe" in {i.family for i in plan}
    assert len(plan) == 4


def test_pack_bundle_accepts_marco_outside_canvas():
    assets, layers, _ = _doc_with_silhouette_and_marco()
    marco_ids = [lid for lid in layers if str(lid).startswith("layer_marco_")]
    plan = build_export_plan(
        layers, assets, marco_ids, 200, 200,
        {"top": 5, "right": 5, "bottom": 5, "left": 5},
        families=BATCH_FAMILIES,
    )
    assert len(plan) == 2
    blob = pack_bundle(plan, 200, 200, formats=("svg",))
    assert len(blob) > 100
