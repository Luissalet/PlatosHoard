"""Tests for procedural frame tray (single solid piece)."""
from __future__ import annotations

import io

import pytest
from shapely.geometry import Point

from silhouettes.editor.frame import (
    build_frame_box_assets_and_layers,
    build_frame_floor,
    build_frame_ring,
    build_frame_tray,
    frame_dimensions,
    is_frame_layer_name,
)
from silhouettes.editor.transforms import TransformError, apply_pose, Pose


def test_frame_dimensions_equal_sides():
    d = frame_dimensions(200, 100, padding_mm=2, wall_w_mm=8, wall_h_mm=25)
    assert d["inner_w"] == pytest.approx(204)
    assert d["inner_h"] == pytest.approx(104)
    assert d["outer_w"] == pytest.approx(220)
    assert d["outer_h"] == pytest.approx(120)
    assert d["wall_h_mm"] == pytest.approx(25)
    assert d["floor_h_mm"] == pytest.approx(3.0)


def test_floor_is_solid_outer():
    d = frame_dimensions(200, 200, padding_mm=2, wall_w_mm=10, wall_h_mm=12)
    floor = build_frame_floor(d)
    assert floor.area == pytest.approx(d["outer_w"] * d["outer_h"])
    assert floor.contains(Point(d["outer_w"] / 2, d["outer_h"] / 2))


def test_frame_ring_has_hole_and_material():
    d = frame_dimensions(200, 200, padding_mm=2, wall_w_mm=10, wall_h_mm=12)
    ring = build_frame_ring(d)
    assert ring.area == pytest.approx(d["outer_w"] * d["outer_h"] - d["inner_w"] * d["inner_h"])
    cx, cy = d["outer_w"] / 2, d["outer_h"] / 2
    assert not ring.contains(Point(cx, cy))
    assert ring.contains(Point(1, 1))


def test_tray_plan_is_solid_outer():
    d = frame_dimensions(200, 200, padding_mm=2, wall_w_mm=10, wall_h_mm=12)
    tray = build_frame_tray(d)
    assert tray.area == pytest.approx(d["outer_w"] * d["outer_h"])


def test_frame_rejects_non_positive_walls():
    with pytest.raises(TransformError):
        frame_dimensions(200, 200, padding_mm=0, wall_w_mm=0, wall_h_mm=8)


def test_box_builds_single_marco_layer():
    assets, layers, dims = build_frame_box_assets_and_layers(
        200, 200, padding_mm=2, wall_w_mm=8, wall_h_mm=15,
    )
    assert len(assets) == 1
    assert len(layers) == 1
    assert assets[0]["name"] == "Marco"
    assert layers[0]["name"] == "Marco"
    assert dims["wall_h_mm"] == pytest.approx(15)
    assert assets[0]["trace_settings"]["kind"] == "procedural_frame_tray"
    assert assets[0]["trace_settings"]["wall_w_mm"] == pytest.approx(8)
    assert assets[0]["trace_settings"]["floor_h_mm"] == pytest.approx(3)
    assert "evenodd" not in assets[0]["canonical_svg"]
    assert layers[0]["pose"]["tx"] == pytest.approx(100)
    assert layers[0]["pose"]["ty"] == pytest.approx(100)
    assert is_frame_layer_name(layers[0]["name"], layers[0]["id"])
    assert str(layers[0]["id"]).startswith("layer_marco_")


def test_tray_centred_on_canvas():
    assets, layers, dims = build_frame_box_assets_and_layers(
        200, 200, padding_mm=2, wall_w_mm=8, wall_h_mm=10,
    )
    floor = build_frame_floor(dims)
    flocal = apply_pose(floor, Pose(-dims["outer_w"] / 2, -dims["outer_h"] / 2, 1.0))
    fworld = apply_pose(flocal, Pose.from_dict(layers[0]["pose"]))
    assert fworld.contains(Point(100, 100))
    assert fworld.contains(Point(0, 0))


def test_generate_frame_locks_marco():
    from silhouettes.editor.commands import CommandError, cmd_generate_frame, cmd_set_pose

    doc = {
        "schema_version": 2,
        "id": "doc_test",
        "name": "test",
        "revision": 1,
        "units": "mm",
        "canvas": {
            "width_mm": 200,
            "height_mm": 200,
            "padding_mm": {"left": 5, "right": 5, "top": 5, "bottom": 5},
        },
        "default_extrusion_mm": 3,
        "stack_gap_mm": 0,
        "assets": {},
        "layers": {},
    }
    doc = cmd_generate_frame(doc, {"wall_w_mm": 8, "wall_h_mm": 12, "padding_mm": 1})
    marco = next(n for n in doc["layers"].values() if n["name"] == "Marco")
    assert marco["locked"] is True
    with pytest.raises(CommandError) as exc:
        cmd_set_pose(
            doc,
            {
                "layer_id": marco["id"],
                "pose": {"tx": 0, "ty": 0, "scale": 1, "angle_deg": 0},
            },
        )
    assert exc.value.code == "LOCKED"


def test_open_rect_tray_mesh_manifold_volume():
    from silhouettes.editor.mesh_adapter import open_rect_tray_mesh

    mesh = open_rect_tray_mesh(0, 0, 100, 80, wall_w=10, wall_h=12, floor_h=3)
    expected = 100 * 80 * 12 - 80 * 60 * 9
    assert mesh.is_watertight
    assert float(mesh.volume) == pytest.approx(expected, rel=1e-6)
    # Floor∥ring concatenate would add internal faces; manifold tray stays lean.
    assert len(mesh.faces) == 28


def test_export_tray_stl_bytes():
    from shapely.geometry import box as shapely_box
    from silhouettes.editor.mesh_adapter import export_tray_stl
    import trimesh

    stl = export_tray_stl(
        shapely_box(0, 0, 100, 80),
        wall_w_mm=10,
        wall_h_mm=12,
        floor_h_mm=3,
        canvas_height_mm=200,
    )
    assert isinstance(stl, (bytes, bytearray)) and len(stl) > 80
    mesh = trimesh.load(io.BytesIO(stl), file_type="stl")
    assert mesh.is_watertight
    assert float(mesh.volume) == pytest.approx(52800.0, rel=1e-5)
