"""Silhouettes pipeline package.

Modules:
    mask        — PNG -> clean binary alpha mask
    trace       — binary mask -> SVG (VTracer backend)
    vector      — SVG -> Shapely polygons (single source of truth)
    mesh        — polygons -> extruded watertight mesh (trimesh + earcut)
    validation  — mesh validation and metadata
"""

from .mask import prepare_mask
from .trace import trace_mask, PRESETS, TraceSettings
from .vector import parse_vector, vector_to_polygons, polygons_to_flat
from .mesh import extrude_polygons
from .validation import validate_mesh

__all__ = [
    "prepare_mask",
    "trace_mask",
    "PRESETS",
    "TraceSettings",
    "parse_vector",
    "vector_to_polygons",
    "polygons_to_flat",
    "extrude_polygons",
    "validate_mesh",
]
