"""Mesh adapter for the Silhouettes editor (task 17).

Responsibilities (spec §5.1, §14.3–14.4, task 17):

* ``to_manufacturing``: apply ``Y = canvas_height − Y`` **exactly once**
  (spec §5.1).  This is the single Y-flip between document world
  (Y down) and manufacturing 3D (Y up).  The legacy route may keep its
  own convention; this adapter is the only place the editor v2 flips.
* ``resolve_extrusion``: ``null`` inherits the document default;
  ``0`` or negative is rejected (spec §6.3: ``null`` means inherit,
  not zero thickness).
* ``stack_positions``: compute the Z base of each layer in the physical
  stack.  ``stack_rank`` 0 is the bottom piece.  ``z_i`` accumulates
  the thicknesses of all lower pieces plus the physical gap.
* ``extrude_part``: extrude a 2D geometry (mm) to a trimesh mesh using
  the existing engine (``silhouettes.mesh.extrude_polygons``).
* ``export_stl``: full pipeline — Y-flip, extrude, serialize to binary
  STL bytes.

Changing the extrusion from 3 to 5 mm does NOT change the XY area or
the SVG (spec §5.5).  XY and Z are controlled independently.
"""
from __future__ import annotations

import io
import math
from typing import Any, Mapping, Optional

import numpy as np
from shapely.affinity import affine_transform
from shapely.geometry.base import BaseGeometry

from .constraints import polygon_parts, require_shape


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class MeshAdapterError(ValueError):
    """Structured mesh adapter error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Y-flip (spec §5.1)
# ---------------------------------------------------------------------------

def to_manufacturing(geometry: BaseGeometry, canvas_height_mm: float) -> BaseGeometry:
    """Apply ``Y = canvas_height − Y`` exactly once.

    Document world uses Y-down (image convention).  Manufacturing 3D
    uses Y-up.  This is the single conversion point (spec §5.1).

    ``X = x_world``, ``Y = H_canvas − y_world``, ``Z = 0..thickness``.
    """
    h = _finite(canvas_height_mm, "canvas_height_mm", positive=True)
    require_shape(geometry, "geometry")
    # Shapely affine_transform order: [a, c, b, d, e, f]
    # Y' = -Y + h  →  d = -1, f = h
    return affine_transform(geometry, [1, 0, 0, -1, 0, h])


# ---------------------------------------------------------------------------
# Extrusion resolution (spec §6.3, task 17 step 1)
# ---------------------------------------------------------------------------

def resolve_extrusion(layer_node: Mapping[str, Any], doc_default: float) -> float:
    """Resolve the effective extrusion for a layer.

    ``extrusion_mm = null`` → inherit ``doc_default``.
    ``extrusion_mm = 0`` or negative → rejected.
    Booleans are rejected.
    """
    val = layer_node.get("extrusion_mm")
    if val is None:
        return _finite(doc_default, "default_extrusion_mm", positive=True)
    if isinstance(val, bool):
        raise MeshAdapterError("INVALID_EXTRUSION", "extrusion_mm cannot be a boolean")
    x = _finite(val, "extrusion_mm", positive=True)
    return x


# ---------------------------------------------------------------------------
# Stack positions (task 17 step 2)
# ---------------------------------------------------------------------------

def stack_positions(
    layers: Mapping[str, Mapping[str, Any]],
    doc: Mapping[str, Any],
) -> dict[str, float]:
    """Compute the Z base of each layer in the physical stack.

    ``stack_rank`` 0 is the bottom piece.  ``z_i`` is the sum of the
    thicknesses of all pieces with lower rank plus the physical gap
    between each pair.

    Returns ``{layer_id: z_base_mm}``.
    """
    default = _finite(doc.get("default_extrusion_mm", 3.0), "default_extrusion_mm", positive=True)
    gap = _finite(doc.get("stack_gap_mm", 0.0), "stack_gap_mm", nonnegative=True)

    ordered = sorted(layers.values(), key=lambda n: n.get("stack_rank", 0))
    result: dict[str, float] = {}
    z = 0.0
    for i, node in enumerate(ordered):
        result[node["id"]] = z
        h = resolve_extrusion(node, default)
        z += h
        if i < len(ordered) - 1:
            z += gap
    return result


# ---------------------------------------------------------------------------
# Extrusion (task 17 step 3)
# ---------------------------------------------------------------------------

def extrude_part(geometry: BaseGeometry, thickness_mm: float):
    """Extrude a 2D geometry (mm) to a trimesh mesh.

    Uses the existing engine (``silhouettes.mesh.extrude_polygons``)
    with Earcut.  All holes and components are preserved.
    """
    from silhouettes.mesh import extrude_polygons
    h = _finite(thickness_mm, "thickness_mm", positive=True)
    require_shape(geometry, "geometry")
    parts = polygon_parts(geometry)
    if not parts:
        raise MeshAdapterError("EMPTY_GEOMETRY", "No polygons to extrude")
    return extrude_polygons(parts, thickness=h)


# ---------------------------------------------------------------------------
# STL export (task 17, task 19)
# ---------------------------------------------------------------------------

def export_stl(
    geometry_doc_mm: BaseGeometry,
    extrusion_mm: float,
    canvas_height_mm: float,
) -> bytes:
    """Full STL export pipeline: Y-flip → extrude → binary STL bytes.

    The Y-flip is applied exactly once (spec §5.1).  The resulting mesh
    has ``Z = 0..extrusion_mm`` and ``Y = canvas_height − y_world``.
    """
    h = _finite(extrusion_mm, "extrusion_mm", positive=True)
    require_shape(geometry_doc_mm, "geometry")
    if geometry_doc_mm.is_empty or geometry_doc_mm.area <= 0:
        raise MeshAdapterError("EMPTY_GEOMETRY", "Cannot export empty geometry")

    mfg = to_manufacturing(geometry_doc_mm, canvas_height_mm)
    mesh = extrude_part(mfg, h)

    buf = io.BytesIO()
    mesh.export(buf, file_type="stl")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _finite(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise MeshAdapterError("INVALID_NUMBER", f"{name} cannot be a boolean")
    try:
        x = float(value)
    except (ValueError, TypeError) as exc:
        raise MeshAdapterError("INVALID_NUMBER", f"{name} must be numeric") from exc
    if not math.isfinite(x) or (positive and x <= 0) or (nonnegative and x < 0):
        raise MeshAdapterError("INVALID_NUMBER", f"{name}: invalid value {value!r}")
    return x
