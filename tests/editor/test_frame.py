"""Tests for procedural frame box (fondo + paredes)."""
from __future__ import annotations

import pytest
from shapely.geometry import Point

from silhouettes.editor.frame import (
    build_frame_box_assets_and_layers,
    build_frame_floor,
    build_frame_ring,
    frame_dimensions,
    is_frame_layer_name,
)
from silhouettes.editor.transforms import TransformError, apply_pose, Pose


def test_frame_dimensions_equal_sides():
    # wall_w expands all four sides; wall_h is Z-only and does not change plan size.
    d = frame_dimensions(200, 100, padding_mm=2, wall_w_mm=8, wall_h_mm=25)
    assert d["inner_w"] == pytest.approx(204)
    assert d["inner_h"] == pytest.approx(104)
    assert d["outer_w"] == pytest.approx(220)  # 204 + 2*8
    assert d["outer_h"] == pytest.approx(120)  # 104 + 2*8
    assert d["wall_h_mm"] == pytest.approx(25)


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


def test_frame_rejects_non_positive_walls():
    with pytest.raises(TransformError):
        frame_dimensions(200, 200, padding_mm=0, wall_w_mm=0, wall_h_mm=8)


def test_box_builds_fondo_and_paredes():
    assets, layers, dims = build_frame_box_assets_and_layers(
        200, 200, padding_mm=2, wall_w_mm=8, wall_h_mm=15,
    )
    assert [a["name"] for a in assets] == ["Marco fondo", "Marco paredes"]
    assert [l["name"] for l in layers] == ["Marco fondo", "Marco paredes"]
    assert dims["wall_h_mm"] == pytest.approx(15)
    assert "evenodd" not in assets[0]["canonical_svg"]
    assert "evenodd" in assets[1]["canonical_svg"]
    for layer in layers:
        assert layer["pose"]["tx"] == pytest.approx(100)
        assert layer["pose"]["ty"] == pytest.approx(100)
        assert is_frame_layer_name(layer["name"], layer["id"])


def test_walls_opening_aligns_with_padded_canvas():
    assets, layers, dims = build_frame_box_assets_and_layers(
        200, 200, padding_mm=2, wall_w_mm=8, wall_h_mm=10,
    )
    ring = build_frame_ring(dims)
    local = apply_pose(ring, Pose(-dims["outer_w"] / 2, -dims["outer_h"] / 2, 1.0))
    world = apply_pose(local, Pose.from_dict(layers[1]["pose"]))
    assert not world.contains(Point(0, 0))
    assert world.contains(Point(-8, -8))

    floor = build_frame_floor(dims)
    flocal = apply_pose(floor, Pose(-dims["outer_w"] / 2, -dims["outer_h"] / 2, 1.0))
    fworld = apply_pose(flocal, Pose.from_dict(layers[0]["pose"]))
    assert fworld.contains(Point(100, 100))
    assert fworld.contains(Point(0, 0))
