"""Extrude valid filled polygons with Earcut; never delete holes or smooth."""
import math
import trimesh
from shapely.geometry import Polygon, MultiPolygon
from shapely.geometry.polygon import orient
from .validation import validate_mesh


def _extrude_one(poly, thickness, index):
    if not isinstance(poly, Polygon) or poly.is_empty or not poly.is_valid or poly.area <= 0:
        raise ValueError(f"Polygon #{index} is not valid positive-area material")
    # Normalize winding, not geometry. All interior rings remain present.
    poly = orient(poly, sign=1.0)
    try:
        mesh = trimesh.creation.extrude_polygon(poly, height=thickness, engine="earcut")
    except Exception as exc:
        raise ValueError(f"Earcut extrusion failed for polygon #{index}: {exc}") from exc
    validate_mesh(mesh, [poly], thickness)
    return mesh


def extrude_polygons(polygons, thickness=10.0):
    if not math.isfinite(thickness) or thickness <= 0:
        raise ValueError("Thickness must be positive and finite")
    flat = []
    for poly in polygons:
        flat.extend(poly.geoms if isinstance(poly, MultiPolygon) else [poly])
    if not flat:
        raise ValueError("No polygons to extrude")
    meshes = [_extrude_one(poly, thickness, i) for i, poly in enumerate(flat)]
    # Concatenation preserves the topology of disconnected components.
    mesh = meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes)
    validate_mesh(mesh, flat, thickness)
    return mesh
