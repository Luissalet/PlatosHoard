"""Stage 4: Shapely polygons -> extruded watertight mesh (trimesh + earcut).

Uses trimesh.creation.extrude_polygon with the earcut engine.
No manual extrusion fallback: if triangulation fails, an explicit error
is raised.
"""

from typing import List

import numpy as np
import trimesh
from shapely.geometry import Polygon, MultiPolygon


def extrude_polygons(polygons: List[Polygon], thickness: float = 10.0) -> trimesh.Trimesh:
    """Extrude a list of 2D polygons into a single watertight 3D mesh.

    Args:
        polygons: list of shapely Polygons (may include holes, concave
                  shapes, and independent islands).
        thickness: extrusion depth in the same units as polygon coords.

    Returns:
        A trimesh.Trimesh with all components merged.

    Raises:
        ValueError: if no polygons are given or triangulation fails.
    """
    if not polygons:
        raise ValueError("No polygons to extrude")

    meshes = []
    for i, poly in enumerate(polygons):
        # Flatten MultiPolygon just in case
        if isinstance(poly, MultiPolygon):
            for geom in poly.geoms:
                meshes.append(_extrude_one(geom, thickness, i))
        else:
            meshes.append(_extrude_one(poly, thickness, i))

    if not meshes:
        raise ValueError("Extrusion produced no geometry")

    if len(meshes) == 1:
        result = meshes[0]
    else:
        result = trimesh.util.concatenate(meshes)

    return result


def _extrude_one(poly: Polygon, thickness: float, index: int) -> trimesh.Trimesh:
    """Extrude a single polygon. Raises on failure (no silent fallback)."""
    # Filter out degenerate holes (area < 1.0) that break watertightness
    valid_holes = [h for h in poly.interiors if abs(h.area) >= 1.0]
    
    # If all holes are degenerate, extrude the exterior only
    if len(valid_holes) < len(poly.interiors):
        poly = Polygon(poly.exterior.coords, valid_holes)
    
    try:
        mesh = trimesh.creation.extrude_polygon(poly, height=thickness, engine="earcut")
    except Exception as e:
        raise ValueError(
            f"Triangulation failed for polygon #{index} "
            f"(exterior pts={len(poly.exterior.coords)}, "
            f"holes={len(poly.interiors)}): {e}"
        ) from e

    if mesh is None or len(mesh.faces) == 0:
        raise ValueError(f"Triangulation produced empty mesh for polygon #{index}")

    return mesh
