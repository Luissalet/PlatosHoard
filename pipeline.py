"""
Silhouette pipeline: PNG -> black silhouette -> traced SVG -> extruded 3D mesh (STL).

Public API:
    process_image(png_bytes, thickness=10.0, simplify=1.0) -> dict

The returned dict contains base64-encoded artifacts for each stage so the web
UI can display them without extra round-trips:
    {
        "silhouette_png": "<base64>",   # fully black silhouette (RGBA)
        "svg":            "<base64>",   # vector trace of the silhouette
        "stl":            "<base64>",   # extruded 3D mesh
        "width":  int,                  # original image width (px)
        "height": int,                  # original image height (px)
        "outline_count": int,           # number of closed contours found
        "triangle_count": int,          # triangles in the STL mesh
    }
"""

import io
import base64
import numpy as np
from PIL import Image
import cv2
import trimesh


# ---------------------------------------------------------------------------
# Stage 1: PNG -> black silhouette
# ---------------------------------------------------------------------------
def make_silhouette(img: Image.Image) -> Image.Image:
    """Return a fully-black silhouette of the input image.

    If the image has an alpha channel, the alpha defines the shape.
    Otherwise we threshold on luminance (dark pixels become the shape).
    The result is pure black (0,0,0) with the original alpha preserved.
    """
    img = img.convert("RGBA")
    r, g, b, a = img.split()

    if img.mode == "RGBA":
        # Use alpha as the mask if it carries meaningful variation
        alpha_arr = np.array(a)
        if alpha_arr.min() < 250:  # has transparency
            mask = alpha_arr
        else:
            # No transparency: threshold on luminance
            lum = (0.299 * np.array(r) + 0.587 * np.array(g) + 0.114 * np.array(b))
            mask = np.where(lum < 128, 255, 0).astype(np.uint8)
    else:
        lum = (0.299 * np.array(r) + 0.587 * np.array(g) + 0.114 * np.array(b))
        mask = np.where(lum < 128, 255, 0).astype(np.uint8)

    # Build a pure-black image with the mask as alpha
    silhouette = Image.new("RGBA", img.size, (0, 0, 0, 0))
    silhouette.putalpha(Image.fromarray(mask, mode="L"))
    return silhouette


# ---------------------------------------------------------------------------
# Contour smoothing helpers
# ---------------------------------------------------------------------------
def _chaikin_smooth(pts: np.ndarray, iterations: int = 2) -> np.ndarray:
    """Smooth a closed polygon with the Chaikin corner-cutting algorithm.

    Each iteration replaces every edge (p_i, p_{i+1}) with four points at the
    25% and 75% positions, rounding corners while preserving the overall
    shape. `iterations` controls how round the result is (2 is a good default
    for pixel-traced contours).
    """
    pts = np.asarray(pts, dtype=np.float64)
    n = len(pts)
    if n < 3:
        return pts

    for _ in range(iterations):
        new_pts = []
        for i in range(n):
            p0 = pts[i]
            p1 = pts[(i + 1) % n]
            new_pts.append(p0 + 0.25 * (p1 - p0))
            new_pts.append(p0 + 0.75 * (p1 - p0))
        pts = np.array(new_pts, dtype=np.float64)
        n = len(pts)
    return pts


def _simplify_contour(contour: np.ndarray, epsilon: float) -> np.ndarray:
    """Reduce a raw pixel contour to a clean polygon.

    Applies Douglas-Peucker (approxPolyDP) to drop the staircase noise from
    pixel tracing. No Chaikin smoothing — that rounds corners too aggressively
    and loses the original shape.
    Returns an (M, 2) float array of (x, y) points.
    """
    # contour is (N, 1, 2) from cv2.findContours
    pts = contour.reshape(-1, 2).astype(np.float32)
    if len(pts) < 3:
        return pts

    # Douglas-Peucker: epsilon controls how aggressively corners are dropped.
    # A small fraction of the contour perimeter keeps the shape but removes
    # the pixel staircase.
    perimeter = cv2.arcLength(contour, closed=True)
    eps = max(epsilon, perimeter * 0.001)
    approx = cv2.approxPolyDP(contour, eps, closed=True)
    approx_pts = approx.reshape(-1, 2).astype(np.float64)

    if len(approx_pts) < 3:
        return approx_pts

    return approx_pts


# ---------------------------------------------------------------------------
# Stage 2: silhouette -> SVG (vector trace via OpenCV contour detection)
# ---------------------------------------------------------------------------
def silhouette_to_svg(silhouette: Image.Image, simplify: float = 1.0) -> str:
    """Trace the silhouette into an SVG string using OpenCV contour detection.

    `simplify` is the minimum contour area in px^2 to keep.
    Higher values remove small specks.

    Contours are simplified (Douglas-Peucker) and smoothed (Chaikin) so the
    SVG path is clean and round instead of a pixel staircase.
    """
    width, height = silhouette.size
    contours = _extract_contours(silhouette, min_area=int(simplify))

    # Build SVG path data from the smoothed contours
    path_parts = []
    for pts in contours:
        if len(pts) < 3:
            continue
        start = pts[0]
        path_parts.append(f"M {start[0]:.2f} {start[1]:.2f} ")
        for i in range(1, len(pts)):
            p = pts[i]
            path_parts.append(f"L {p[0]:.2f} {p[1]:.2f} ")
        path_parts.append("Z ")

    path_d = "".join(path_parts)

    # White background rect so the black silhouette is visible on any page
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">\n'
        f'  <rect x="0" y="0" width="{width}" height="{height}" fill="white"/>\n'
        f'  <path d="{path_d}" fill="black"/>\n'
        f'</svg>\n'
    )
    return svg


# ---------------------------------------------------------------------------
# Stage 3: SVG contours -> extruded 3D mesh (STL)
# ---------------------------------------------------------------------------
def _extract_contours(silhouette: Image.Image, min_area: int = 50, epsilon: float = 1.5):
    """Extract closed, simplified and smoothed contours from the silhouette.

    Returns a list of contours, each an (M, 2) float array of (x, y) points.
    Contours with area below `min_area` px^2 are discarded.

    Each contour is reduced with Douglas-Peucker (epsilon) and rounded with
    Chaikin smoothing, so both the SVG and the extruded STL share the same
    clean geometry instead of a pixel staircase.
    """
    # Use the ALPHA channel as the mask (see silhouette_to_svg). Luminance
    # of a pure-black silhouette is all-zeros -> whole canvas = foreground.
    alpha = np.array(silhouette.convert("RGBA").split()[-1])
    binary = (alpha > 127).astype(np.uint8) * 255

    contours, _ = cv2.findContours(
        binary,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    result = []
    for contour in contours:
        if len(contour) < 3:
            continue
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        smoothed = _simplify_contour(contour, epsilon)
        if len(smoothed) >= 3:
            result.append(smoothed)
    return result


def extrude_contours(contours, thickness: float = 10.0) -> trimesh.Trimesh:
    """Extrude a list of 2D contours into a 3D mesh using trimesh.

    Each contour is a list of (x, y) points. The result is a solid slab of
    the given `thickness` (in the same units as the contour coordinates).
    """
    if not contours:
        raise ValueError("No contours to extrude")

    # We'll extrude each contour and merge them
    meshes = []
    for contour in contours:
        # Use manual extrusion for maximum fidelity to the input polygon
        pts = np.array(contour, dtype=np.float64)
        mesh = _manual_extrude(pts, thickness)
        if mesh is not None:
            meshes.append(mesh)

    if not meshes:
        raise ValueError("Extrusion produced no geometry")

    if len(meshes) == 1:
        result = meshes[0]
    else:
        result = trimesh.util.concatenate(meshes)

    # Center the mesh at origin
    result.apply_translation(-result.centroid)
    return result


def _manual_extrude(pts: np.ndarray, thickness: float):
    """Manual extrusion: create top/bottom caps and side walls.

    Uses correct winding so the STL renders as a solid, not a deformed mesh.
    """
    n = len(pts)
    if n < 3:
        return None

    # Build vertices: bottom ring (z=0) + top ring (z=thickness)
    verts = np.zeros((2 * n, 3))
    verts[:n, 0] = pts[:, 0]
    verts[:n, 1] = pts[:, 1]
    verts[:n, 2] = 0.0
    verts[n:, 0] = pts[:, 0]
    verts[n:, 1] = pts[:, 1]
    verts[n:, 2] = thickness

    # Build faces: bottom cap, top cap, side walls
    faces = []

    # Bottom cap (winding: clockwise when viewed from below, so normal points down)
    for i in range(1, n - 1):
        faces.append([0, i + 1, i])

    # Top cap (winding: counter-clockwise when viewed from above, so normal points up)
    for i in range(1, n - 1):
        faces.append([n, n + i, n + i + 1])

    # Side walls (winding: outward-facing normals)
    for i in range(n):
        j = (i + 1) % n
        # Quad: bottom_i, bottom_j, top_j, top_i -> two triangles
        faces.append([i, j, n + j])
        faces.append([i, n + j, n + i])

    faces = np.array(faces, dtype=np.int64)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    return mesh


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------
def process_image(png_bytes: bytes, thickness: float = 10.0, simplify: float = 1.0) -> dict:
    """Run the full pipeline: PNG -> silhouette -> SVG -> STL.

    Args:
        png_bytes:  Raw PNG file bytes.
        thickness:  Extrusion depth in pixels (same units as image coords).
        simplify:   Minimum contour area in px^2 (higher = fewer specks).

    Returns:
        dict with base64-encoded artifacts and metadata (see module docstring).
    """
    # Load image
    img = Image.open(io.BytesIO(png_bytes))
    width, height = img.size

    # Stage 1: black silhouette
    silhouette = make_silhouette(img)
    sil_buf = io.BytesIO()
    silhouette.save(sil_buf, format="PNG")
    silhouette_b64 = base64.b64encode(sil_buf.getvalue()).decode("ascii")

    # Stage 2: SVG trace
    svg_str = silhouette_to_svg(silhouette, simplify=simplify)
    svg_b64 = base64.b64encode(svg_str.encode("utf-8")).decode("ascii")

    # Stage 3: extrude to 3D
    contours = _extract_contours(silhouette, min_area=int(simplify))
    mesh = extrude_contours(contours, thickness=thickness)

    stl_bytes = mesh.export(file_type="stl")
    stl_b64 = base64.b64encode(stl_bytes).decode("ascii")

    return {
        "silhouette_png": silhouette_b64,
        "svg": svg_b64,
        "stl": stl_b64,
        "width": width,
        "height": height,
        "outline_count": len(contours),
        "triangle_count": len(mesh.faces),
    }


