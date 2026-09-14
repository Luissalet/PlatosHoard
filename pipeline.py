"""
Silhouette pipeline: PNG -> black silhouette -> traced SVG -> extruded 3D mesh (STL).

Public API:
    process_image(png_bytes, thickness=10.0, preset="clean",
                  detail=0.5, speckle_area=10, alpha_threshold=128) -> dict

The returned dict contains base64-encoded artifacts for each stage so the web
UI can display them without extra round-trips:
    {
        "silhouette_png": "<base64>",   # fully black silhouette (RGBA)
        "svg":            "<base64>",   # vector trace of the silhouette
        "stl":            "<base64>",   # extruded 3D mesh
        "width":  int,                  # original image width (px)
        "height": int,                  # original image height (px)
        "outline_count": int,           # number of polygons (islands)
        "triangle_count": int,          # triangles in the STL mesh
        "metadata": {                   # mesh validation metadata
            "watertight": bool,
            "vertices": int,
            "triangle_count": int,
            "components": int,
            "holes": int,
        }
    }

Pipeline (single source of truth):
    PNG
     -> prepare_mask (binary alpha mask)
     -> trace_mask (VTracer -> SVG)
     -> parse_vector + vector_to_polygons (SVG -> Shapely polygons)
     -> extrude_polygons (trimesh + earcut)
     -> validate_mesh
"""

import io
import base64

import numpy as np
from PIL import Image

from silhouettes.mask import prepare_mask, mask_to_silhouette
from silhouettes.trace import trace_mask, PRESETS, TraceSettings
from silhouettes.vector import parse_vector, vector_to_polygons
from silhouettes.mesh import extrude_polygons
from silhouettes.validation import validate_mesh


def _settings_from_params(preset: str, detail: float, speckle_area: float) -> TraceSettings:
    """Build TraceSettings from UI parameters.

    Args:
        preset: one of 'exact', 'clean', 'smooth'.
        detail: trace tolerance in px (0.1 - 3.0). Lower = more faithful.
                Mapped to VTracer simplify (0..1) and path_precision.
        speckle_area: minimum region area in px^2 to keep.
    """
    base = PRESETS.get(preset, PRESETS["clean"])
    settings = TraceSettings(
        mode=base.mode,
        simplify=base.simplify,
        path_precision=base.path_precision,
        color_precision=base.color_precision,
        layer_difference=base.layer_difference,
        corner_threshold=base.corner_threshold,
        length_threshold=base.length_threshold,
        max_iterations=base.max_iterations,
        splice_threshold=base.splice_threshold,
    )

    # Detail slider (0.1-3.0 px) modulates VTracer simplify:
    # detail 0.1 -> simplify ~0.05 (very faithful)
    # detail 3.0 -> simplify ~0.9 (very simplified)
    if detail is not None:
        settings.simplify = min(0.95, max(0.02, detail / 3.0))

    # Speckle removal: VTracer filter_speckle is in px (area-ish).
    # We pass it through the length_threshold as a proxy for small feature removal.
    if speckle_area is not None and speckle_area > 0:
        settings.length_threshold = max(1.0, float(speckle_area) ** 0.5)

    return settings


def process_image(
    png_bytes: bytes,
    thickness: float = 10.0,
    preset: str = "clean",
    detail: float = 0.5,
    speckle_area: float = 10.0,
    alpha_threshold: int = 128,
) -> dict:
    """Run the full pipeline: PNG -> silhouette -> SVG -> polygons -> STL.

    Args:
        png_bytes:       Raw PNG file bytes.
        thickness:       Extrusion depth in pixels (same units as image coords).
        preset:          'exact' | 'clean' | 'smooth'
        detail:          Trace tolerance in px (0.1 - 3.0, default 0.5).
        speckle_area:    Minimum region area in px^2 to keep (default 10).
        alpha_threshold: Alpha threshold for mask (default 128).

    Returns:
        dict with base64-encoded artifacts and metadata (see module docstring).

    Raises:
        ValueError: on any stage failure (explicit, no silent fallback).
    """
    # Load image
    img = Image.open(io.BytesIO(png_bytes))
    width, height = img.size

    # Stage 1: binary mask + black silhouette
    mask = prepare_mask(img, alpha_threshold=alpha_threshold)
    silhouette = mask_to_silhouette(mask)
    sil_buf = io.BytesIO()
    silhouette.save(sil_buf, format="PNG")
    silhouette_b64 = base64.b64encode(sil_buf.getvalue()).decode("ascii")

    # Stage 2: SVG trace (VTracer)
    settings = _settings_from_params(preset, detail, speckle_area)
    svg_str = trace_mask(mask, settings)
    svg_b64 = base64.b64encode(svg_str.encode("utf-8")).decode("ascii")

    # Stage 3: SVG -> polygons (single source of truth for the STL)
    rings = parse_vector(svg_str)
    polygons = vector_to_polygons(rings)

    # Stage 4: extrude
    mesh = extrude_polygons(polygons, thickness=thickness)

    # Stage 5: validate
    metadata = validate_mesh(mesh)

    stl_bytes = mesh.export(file_type="stl")
    stl_b64 = base64.b64encode(stl_bytes).decode("ascii")

    return {
        "silhouette_png": silhouette_b64,
        "svg": svg_b64,
        "stl": stl_b64,
        "width": width,
        "height": height,
        "outline_count": len(polygons),
        "triangle_count": metadata["triangle_count"],
        "metadata": metadata,
    }
