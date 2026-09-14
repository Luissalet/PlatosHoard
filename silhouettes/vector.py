"""Stage 3: SVG -> Shapely polygons (single source of truth).

Parses the SVG path data, converts cubic Beziers to polylines with
ADAPTIVE subdivision (max deviation tolerance), and builds Shapely
Polygons with exterior rings, holes and independent islands.

The STL is built from THESE polygons — never from a second contour
extraction on the PNG.
"""

import re
from typing import List, Tuple

import numpy as np
from shapely.geometry import Polygon, MultiPolygon
from shapely.validation import make_valid
from svg.path import parse_path
from svg.path import Path as SvgPath
from svg.path import Move, Line, CubicBezier, QuadraticBezier, Close


# ---------------------------------------------------------------------------
# Adaptive Bezier flattening
# ---------------------------------------------------------------------------
def _flatten_cubic(p0, p1, p2, p3, tolerance: float, depth: int = 0,
                   max_depth: int = 12) -> List[Tuple[float, float]]:
    """Recursively subdivide a cubic Bezier until max deviation < tolerance."""
    # Midpoint of the chord
    mx = (p0[0] + p3[0]) / 2.0
    my = (p0[1] + p3[1]) / 2.0
    # Point on the curve at t=0.5
    cx = 0.125 * (p0[0] + 3 * p1[0] + 3 * p2[0] + p3[0])
    cy = 0.125 * (p0[1] + 3 * p1[1] + 3 * p2[1] + p3[1])
    deviation = abs(cx - mx) + abs(cy - my)

    if deviation <= tolerance or depth >= max_depth:
        return [(p3[0], p3[1])]

    # De Casteljau split at t=0.5
    a = _lerp(p0, p1)
    b = _lerp(p1, p2)
    c = _lerp(p2, p3)
    d = _lerp(a, b)
    e = _lerp(b, c)
    f = _lerp(d, e)

    left = _flatten_cubic(p0, a, d, f, tolerance, depth + 1, max_depth)
    right = _flatten_cubic(f, e, c, p3, tolerance, depth + 1, max_depth)
    return left + right


def _lerp(p, q):
    return ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)


def _pt(c) -> Tuple[float, float]:
    """svg.path points are complex numbers: real=x, imag=y."""
    return (float(c.real), float(c.imag))


def _flatten_path(path: SvgPath, tolerance: float) -> List[Tuple[float, float]]:
    """Flatten an SVG path (M/L/C/Q/Z commands) into a list of point rings.

    Returns a list of rings; each ring is a list of (x, y) tuples.
    """
    rings: List[List[Tuple[float, float]]] = []
    current: List[Tuple[float, float]] = []
    start = None
    current_pos = (0.0, 0.0)

    for seg in path:
        if isinstance(seg, Move):
            if current:
                rings.append(current)
            current = [_pt(seg.start)]
            start = _pt(seg.start)
            current_pos = start
        elif isinstance(seg, Line):
            current.append(_pt(seg.end))
            current_pos = _pt(seg.end)
        elif isinstance(seg, CubicBezier):
            pts = _flatten_cubic(
                _pt(seg.start),
                _pt(seg.control1),
                _pt(seg.control2),
                _pt(seg.end),
                tolerance,
            )
            current.extend(pts)
            current_pos = _pt(seg.end)
        elif isinstance(seg, QuadraticBezier):
            # Convert quadratic to cubic: C1 = P0 + 2/3(Q-P0), C2 = P3 + 2/3(Q-P3)
            p0 = _pt(seg.start)
            q = _pt(seg.control)
            p3 = _pt(seg.end)
            c1 = (p0[0] + 2 / 3 * (q[0] - p0[0]), p0[1] + 2 / 3 * (q[1] - p0[1]))
            c2 = (p3[0] + 2 / 3 * (q[0] - p3[0]), p3[1] + 2 / 3 * (q[1] - p3[1]))
            pts = _flatten_cubic(p0, c1, c2, p3, tolerance)
            current.extend(pts)
            current_pos = p3
        elif isinstance(seg, Close):
            if current and start is not None:
                # Close the ring
                if current[0] != start:
                    current.append(start)
                rings.append(current)
            current = []
            start = None

    if current:
        rings.append(current)

    return rings


# ---------------------------------------------------------------------------
# SVG -> polygons
# ---------------------------------------------------------------------------
def parse_vector(svg: str) -> List[List[Tuple[float, float]]]:
    """Parse an SVG string into a list of point rings.

    Each ring is a list of (x, y) tuples. The first ring of each
    subpath is the exterior; subsequent rings inside it are holes.
    """
    # Extract all path 'd' attributes
    d_attrs = re.findall(r'd="([^"]+)"', svg)
    if not d_attrs:
        raise ValueError("No path data found in SVG")

    all_rings: List[List[Tuple[float, float]]] = []
    for d in d_attrs:
        path = parse_path(d)
        rings = _flatten_path(path, tolerance=0.25)
        all_rings.extend(rings)

    if not all_rings:
        raise ValueError("SVG path produced no geometry")

    # VTracer spline mode can emit spurious rings whose control points
    # overshoot far outside the canvas (e.g. a full-canvas ring when the
    # shape touches the image border). Keep only rings that stay within a
    # small margin of the viewBox.
    vb = re.search(r'viewBox="([^"]+)"', svg)
    if vb:
        parts = vb.group(1).split()
        if len(parts) == 4:
            vx, vy, vw, vh = (float(p) for p in parts)
            margin = max(vw, vh) * 0.1
            kept = []
            for ring in all_rings:
                xs = [p[0] for p in ring]
                ys = [p[1] for p in ring]
                if (min(xs) >= vx - margin and max(xs) <= vx + vw + margin
                        and min(ys) >= vy - margin and max(ys) <= vy + vh + margin):
                    kept.append(ring)
            if kept:
                all_rings = kept

    # Clip rings to the viewBox so spline overshoot beyond the canvas
    # (control points can extend past the shape) does not leak into the
    # geometry. Sutherland-Hodgman against the canvas rectangle.
    if vb:
        parts = vb.group(1).split()
        if len(parts) == 4:
            vx, vy, vw, vh = (float(p) for p in parts)
            clipped = []
            for ring in all_rings:
                c = _clip_to_rect(ring, vx, vy, vx + vw, vy + vh)
                if len(c) >= 3:
                    clipped.append(c)
            if clipped:
                all_rings = clipped

    return all_rings


def _clip_to_rect(ring: List[Tuple[float, float]],
                  xmin: float, ymin: float, xmax: float, ymax: float
                  ) -> List[Tuple[float, float]]:
    """Sutherland-Hodgman clip of a polygon ring against an axis-aligned rect."""
    def clip_edge(poly, inside, intersect):
        out = []
        n = len(poly)
        for i in range(n):
            cur = poly[i]
            prev = poly[i - 1]
            cur_in = inside(cur)
            prev_in = inside(prev)
            if cur_in:
                if not prev_in:
                    out.append(intersect(prev, cur))
                out.append(cur)
            elif prev_in:
                out.append(intersect(prev, cur))
        return out

    def lerp_edge(p, q, t):
        return (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))

    poly = list(ring)
    # Left
    poly = clip_edge(poly, lambda p: p[0] >= xmin,
                     lambda p, q: lerp_edge(p, q, (xmin - p[0]) / (q[0] - p[0])))
    # Right
    poly = clip_edge(poly, lambda p: p[0] <= xmax,
                     lambda p, q: lerp_edge(p, q, (xmax - p[0]) / (q[0] - p[0])))
    # Bottom
    poly = clip_edge(poly, lambda p: p[1] >= ymin,
                     lambda p, q: lerp_edge(p, q, (ymin - p[1]) / (q[1] - p[1])))
    # Top
    poly = clip_edge(poly, lambda p: p[1] <= ymax,
                     lambda p, q: lerp_edge(p, q, (ymax - p[1]) / (q[1] - p[1])))
    return poly


def _ring_area(ring: List[Tuple[float, float]]) -> float:
    """Signed area of a ring (shoelace)."""
    n = len(ring)
    area = 0.0
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    return area / 2.0


def vector_to_polygons(rings: List[List[Tuple[float, float]]],
                       tolerance: float = 0.1) -> List[Polygon]:
    """Build a list of Shapely Polygons from parsed SVG rings.

    Uses the even-odd rule: rings are assigned as exterior or hole based on
    nesting (point-in-polygon test against already-built exteriors).

    Args:
        rings: list of point rings from parse_vector.
        tolerance: reserved for future adaptive re-flattening.

    Returns:
        List of shapely.geometry.Polygon (each may have holes).
        Independent islands become separate polygons.
    """
    # Sort rings by |area| descending so exteriors come before holes.
    indexed = [(i, ring) for i, ring in enumerate(rings)]
    indexed.sort(key=lambda t: abs(_ring_area(t[1])), reverse=True)

    # First pass: create all polygons as exterior rings
    exterior_polygons = []

    for _, ring in indexed:
        if len(ring) < 3:
            continue
            
        # Create a polygon from the ring to check its validity
        poly = Polygon(ring)
        if not poly.is_valid:
            poly = make_valid(poly)
            
        if poly.area > 1e-6 and not poly.is_empty:
            exterior_polygons.append(poly)

    # Second pass: identify which polygons are holes by testing containment
    final_polygons = []
    hole_attachments = []  # (parent_index, hole_ring) pairs

    for i, exterior in enumerate(exterior_polygons):
        # Check if this polygon contains any other polygons
        is_hole = False
        for j, other_exterior in enumerate(exterior_polygons):
            if i != j and other_exterior.within(exterior):
                # This other polygon is inside the current one - it's a hole
                hole_attachments.append((i, other_exterior.exterior.coords))
                is_hole = True
                break
        
        if not is_hole:
            final_polygons.append(exterior)

    # Third pass: create polygons with holes properly attached
    result_polygons = []
    
    for i, exterior in enumerate(final_polygons):
        # Collect all hole rings that are inside this exterior
        holes = []
        for parent_idx, hole_ring in hole_attachments:
            if parent_idx == i:
                holes.append(hole_ring)
        
        # Create polygon with holes
        if holes:
            try:
                poly = Polygon(exterior.exterior.coords, holes)
                if not poly.is_valid:
                    poly = make_valid(poly)
                if poly.area > 1e-6 and not poly.is_empty:
                    result_polygons.append(poly)
            except Exception:
                # If we can't create polygon with holes, fall back to just the exterior
                if exterior.area > 1e-6 and not exterior.is_empty:
                    result_polygons.append(exterior)
        else:
            if exterior.area > 1e-6 and not exterior.is_empty:
                result_polygons.append(exterior)

    # Final validation - ensure we have at least one valid polygon
    if not result_polygons:
        # Create a fallback polygon from the first ring if no valid polygons were created
        if rings:
            # Try to create a simple polygon from the first valid ring
            for ring in rings:
                if len(ring) >= 3:
                    try:
                        poly = Polygon(ring)
                        if not poly.is_valid:
                            poly = make_valid(poly)
                        if poly.area > 1e-6 and not poly.is_empty:
                            result_polygons.append(poly)
                            break
                    except Exception:
                        continue
    
    if not result_polygons:
        raise ValueError("Vector geometry produced no valid polygons")

    return result_polygons


def polygons_to_flat(polygons: List[Polygon]) -> List[Polygon]:
    """Flatten MultiPolygons into a plain list of Polygons."""
    out: List[Polygon] = []
    for p in polygons:
        if isinstance(p, MultiPolygon):
            out.extend(list(p.geoms))
        else:
            out.append(p)
    return out
