"""Rectangular box frame (marco) around the inverse / canvas.

Two laser-cut pieces forming a tray:

* **Fondo** — solid outer rectangle (the floor of the box).
* **Paredes** — outer − inner ring (the walls) that sits on the floor.

Parameters:

* ``padding_mm`` — gap between canvas / inverse edge and the inner opening
* ``wall_w_mm`` — wall thickness in plan, equal on all four sides
* ``wall_h_mm`` — wall height (Z extrusion of the four walls)
"""
from __future__ import annotations

import hashlib
import math
import uuid
from typing import Any

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from .transforms import TransformError, _finite

FRAME_LAYER_NAMES = frozenset({"Marco", "Marco fondo", "Marco paredes"})


def frame_dimensions(
    canvas_w: float,
    canvas_h: float,
    *,
    padding_mm: float,
    wall_w_mm: float,
    wall_h_mm: float,
) -> dict[str, float]:
    """Return inner/outer plan sizes + wall height for a frame around W×H."""
    W = _finite(canvas_w, "canvas_w", positive=True)
    H = _finite(canvas_h, "canvas_h", positive=True)
    pad = _finite(padding_mm, "padding_mm", nonnegative=True)
    ww = _finite(wall_w_mm, "wall_w_mm", positive=True)
    wh = _finite(wall_h_mm, "wall_h_mm", positive=True)
    if not all(math.isfinite(v) for v in (W, H, pad, ww, wh)):
        raise TransformError("INVALID_NUMBER", "frame dimensions must be finite")

    # Plan: walls expand equally on all four sides.
    inner_w = W + 2.0 * pad
    inner_h = H + 2.0 * pad
    outer_w = inner_w + 2.0 * ww
    outer_h = inner_h + 2.0 * ww
    return {
        "canvas_w": W,
        "canvas_h": H,
        "padding_mm": pad,
        "wall_w_mm": ww,
        "wall_h_mm": wh,  # Z height of walls (extrusion), not plan size
        "inner_w": inner_w,
        "inner_h": inner_h,
        "outer_w": outer_w,
        "outer_h": outer_h,
    }


def build_frame_floor(dims: dict[str, float]) -> BaseGeometry:
    """Solid outer rectangle in source units (top-left origin)."""
    ow, oh = dims["outer_w"], dims["outer_h"]
    floor = box(0.0, 0.0, ow, oh)
    if floor.is_empty or floor.area <= 0:
        raise TransformError("EMPTY_GEOMETRY", "frame floor has no area")
    return floor


def build_frame_ring(dims: dict[str, float]) -> BaseGeometry:
    """Outer − inner rectangle; wall thickness equal on all four sides."""
    ow, oh = dims["outer_w"], dims["outer_h"]
    iw, ih = dims["inner_w"], dims["inner_h"]
    ww = dims["wall_w_mm"]
    outer = box(0.0, 0.0, ow, oh)
    inner = box(ww, ww, ww + iw, ww + ih)
    ring = outer.difference(inner)
    if ring.is_empty or ring.area <= 0:
        raise TransformError("EMPTY_GEOMETRY", "frame ring has no area")
    return ring


def floor_svg(dims: dict[str, float]) -> str:
    """Solid rectangle SVG for the box floor."""
    ow, oh = dims["outer_w"], dims["outer_h"]
    d = f"M0,0 H{ow:g} V{oh:g} H0 Z"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{ow:g}mm" height="{oh:g}mm" '
        f'viewBox="0 0 {ow:g} {oh:g}">'
        f'<path d="{d}"/></svg>'
    )


def frame_svg(dims: dict[str, float]) -> str:
    """Even-odd SVG for the wall ring in source mm."""
    ow, oh = dims["outer_w"], dims["outer_h"]
    iw, ih = dims["inner_w"], dims["inner_h"]
    ww = dims["wall_w_mm"]
    x0 = y0 = ww
    x1, y1 = ww + iw, ww + ih
    d = (
        f"M0,0 H{ow:g} V{oh:g} H0 Z "
        f"M{x0:g},{y0:g} H{x1:g} V{y1:g} H{x0:g} Z"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{ow:g}mm" height="{oh:g}mm" '
        f'viewBox="0 0 {ow:g} {oh:g}">'
        f'<path fill-rule="evenodd" d="{d}"/></svg>'
    )


def _asset_dict(
    *,
    aid: str,
    name: str,
    filename: str,
    svg: str,
    dims: dict[str, float],
    kind: str,
) -> dict[str, Any]:
    ow, oh = dims["outer_w"], dims["outer_h"]
    sha = hashlib.sha256(svg.encode("utf-8")).hexdigest()
    return {
        "id": aid,
        "name": name,
        "source_filename": filename,
        "source_type": "svg",
        "source_uri": f"assets/{aid}/source.svg",
        "canonical_svg_uri": f"assets/{aid}/canonical.svg",
        "source_sha256": sha,
        "source_viewbox": [0.0, 0.0, ow, oh],
        "mm_per_source_unit": 1.0,
        "normalization_pose": {
            "tx": -ow / 2.0,
            "ty": -oh / 2.0,
            "scale": 1.0,
            "angle_deg": 0.0,
        },
        "geometry_hash": sha,
        "trace_settings": {"kind": kind},
        "curve_tolerance_source": 0.02,
        "local_bounds": [-ow / 2.0, -oh / 2.0, ow / 2.0, oh / 2.0],
        "canonical_svg": svg,
    }


def _centred_layer(lid: str, aid: str, name: str, dims: dict[str, float]) -> dict[str, Any]:
    return {
        "id": lid,
        "asset_id": aid,
        "name": name,
        "pose": {
            "tx": float(dims["canvas_w"]) / 2.0,
            "ty": float(dims["canvas_h"]) / 2.0,
            "scale": 1.0,
            "angle_deg": 0.0,
        },
    }


def build_frame_box_assets_and_layers(
    canvas_w: float,
    canvas_h: float,
    *,
    padding_mm: float = 1.0,
    wall_w_mm: float = 12.0,
    wall_h_mm: float = 12.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build fondo + paredes assets/layers for ``add_layers``.

    ``wall_w_mm`` = plan thickness (equal on 4 sides).
    ``wall_h_mm`` = Z height of paredes (set as ``extrusion_mm`` by the caller).
    """
    dims = frame_dimensions(
        canvas_w, canvas_h,
        padding_mm=padding_mm,
        wall_w_mm=wall_w_mm,
        wall_h_mm=wall_h_mm,
    )
    svg_floor = floor_svg(dims)
    svg_walls = frame_svg(dims)
    uid = uuid.uuid4().hex[:10]
    aid_floor = f"asset_marco_fondo_{hashlib.sha256(svg_floor.encode()).hexdigest()[:10]}"
    aid_walls = f"asset_marco_paredes_{hashlib.sha256(svg_walls.encode()).hexdigest()[:10]}"
    lid_floor = f"layer_marco_fondo_{uid}"
    lid_walls = f"layer_marco_paredes_{uid}"

    assets = [
        _asset_dict(
            aid=aid_floor, name="Marco fondo", filename="marco_fondo.svg",
            svg=svg_floor, dims=dims, kind="procedural_frame_floor",
        ),
        _asset_dict(
            aid=aid_walls, name="Marco paredes", filename="marco_paredes.svg",
            svg=svg_walls, dims=dims, kind="procedural_frame_walls",
        ),
    ]
    layers = [
        _centred_layer(lid_floor, aid_floor, "Marco fondo", dims),
        _centred_layer(lid_walls, aid_walls, "Marco paredes", dims),
    ]
    return assets, layers, dims


def is_frame_layer_name(name: str | None, layer_id: str | None = None) -> bool:
    """True for procedural marco box layers (legacy ring included)."""
    if (name or "") in FRAME_LAYER_NAMES:
        return True
    return str(layer_id or "").startswith("layer_marco_")


def build_frame_asset_and_layer(
    canvas_w: float,
    canvas_h: float,
    *,
    padding_mm: float = 1.0,
    wall_w_mm: float = 12.0,
    wall_h_mm: float = 12.0,
    layer_id: str | None = None,
    asset_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    assets, layers, _dims = build_frame_box_assets_and_layers(
        canvas_w, canvas_h,
        padding_mm=padding_mm,
        wall_w_mm=wall_w_mm,
        wall_h_mm=wall_h_mm,
    )
    walls_a, walls_l = assets[1], layers[1]
    if asset_id:
        walls_a = {**walls_a, "id": asset_id}
        walls_l = {**walls_l, "asset_id": asset_id}
    if layer_id:
        walls_l = {**walls_l, "id": layer_id}
    return walls_a, walls_l
