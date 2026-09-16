"""Fitting for the Silhouettes editor (tasks 12–14).

Responsibilities:

* ``contain_rect`` (task 12): exact max uniform bbox fit of a centred
  asset into an axis-aligned rectangle at a fixed angle.  This is a
  closed-form solution — no search.  It REJECTS irregular containers
  (``NOT_RECTANGLE``) and must not be used for them.
* ``fit_inside`` (task 13): bounded, reproducible best-found fitting for
  irregular containers (concave shapes, holes).  Integrated from
  ``reference/editor_ref/fitting.py`` with its non-monotonicity test.
  No binary search on scale feasibility.  ``optimality_proven`` is
  always ``False`` — best found is not a proof of global optimum.
* ``fit_subtree`` (task 14): top-down fitting of a subtree; siblings are
  fitted sequentially with locks as obstacles.  A failure of one child
  does NOT silently confirm the previous ones.

All poses are in **world mm**.  Convert with ``reparent_pose`` to store
the local pose.  Input assets must be centred on their material
bounding-box centre (``normalize_asset``).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .transforms import (
    Pose, TransformError, apply_pose, compose_pose, reparent_pose,
    world_pose,
)
from .constraints import (
    ConstraintError, fits, siblings_clear, polygon_parts, require_shape,
    inner_canvas, canvas_shape,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class FittingError(ValueError):
    """Structured fitting error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Task 12 — contain_rect (exact, closed-form)
# ---------------------------------------------------------------------------

def contain_rect(local_shape: BaseGeometry, rect: Polygon,
                 angle_deg: float = 0.0) -> Pose:
    """Exact max uniform bbox fit for a fixed angle into an axis-aligned rectangle.

    The asset must be centred at the origin (``normalize_asset`` output).
    Returns a world pose (translation + scale + angle) such that the
    rotated asset's bounding box fits ``rect`` with maximum uniform scale.

    Raises
    ------
    FittingError('NOT_RECTANGLE')
        If ``rect`` is not an axis-aligned rectangle.  Use ``fit_inside``
        for irregular containers.
    """
    require_shape(local_shape, "local_shape")
    require_shape(rect, "rect")
    if not rect.equals(box(*rect.bounds)):
        raise FittingError("NOT_RECTANGLE",
                           "contain_rect is not an irregular-shape solver")
    a = math.radians(angle_deg)
    if not math.isfinite(a):
        raise FittingError("INVALID_ANGLE", f"angle_deg must be finite, got {angle_deg!r}")

    rotated = apply_pose(local_shape, Pose(angle_deg=angle_deg))
    x0, y0, x1, y1 = rotated.bounds
    rw, rh = x1 - x0, y1 - y0
    if rw <= 0 or rh <= 0:
        raise FittingError("EMPTY_GEOMETRY", "rotated shape has zero extent")

    a_r, b_r, c_r, d_r = rect.bounds
    rect_w = c_r - a_r
    rect_h = d_r - b_r
    if rect_w <= 0 or rect_h <= 0:
        raise FittingError("EMPTY_CONTAINER", "rect has zero extent")

    s = min(rect_w / rw, rect_h / rh)
    # Centre the rotated shape in the rect
    cx = (a_r + c_r) / 2.0 - s * (x0 + x1) / 2.0
    cy = (b_r + d_r) / 2.0 - s * (y0 + y1) / 2.0
    return Pose(cx, cy, s, angle_deg)


def fit_to_canvas(doc: Mapping[str, Any], layer_id: str,
                  angle_deg: float = 0.0) -> dict:
    """Compute the world pose that fits a layer into the usable canvas U.

    Returns ``{"pose_world": Pose, "pose_local": Pose, "scale": float}``.
    If the layer has a parent, the world pose is converted to local via
    ``reparent_pose``.  The document is NOT modified.
    """
    layers = doc["layers"]
    assets = doc["assets"]
    if layer_id not in layers:
        raise FittingError("UNKNOWN_LAYER", f"layer {layer_id!r} does not exist")
    node = layers[layer_id]
    asset = assets[node["asset_id"]]
    geom = asset.get("_geometry")
    if geom is None:
        raise FittingError("MISSING_GEOMETRY",
                           f"asset {node['asset_id']!r} has no cached geometry")

    canvas = doc["canvas"]
    U = inner_canvas(canvas["width_mm"], canvas["height_mm"],
                     canvas.get("padding_mm", {"left": 0, "right": 0,
                                               "top": 0, "bottom": 0}))
    pose_world = contain_rect(geom, U, angle_deg=angle_deg)

    parent_id = node.get("parent_id")
    if parent_id is not None:
        parent_world = world_pose(parent_id, layers)
        pose_local = reparent_pose(pose_world, parent_world)
    else:
        pose_local = pose_world

    return {
        "pose_world": pose_world,
        "pose_local": pose_local,
        "scale": pose_world.scale,
    }


# ---------------------------------------------------------------------------
# Task 13 — fit_inside (bounded search, irregular containers)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FitResult:
    """Result of a ``fit_inside`` search.

    ``optimality_proven`` is ALWAYS ``False``: best-found is not a proof
    of global optimum (spec §12.3, task 13 step 5).
    """
    pose: Optional[Pose]
    status: str
    evaluations: int
    optimality_proven: bool = False


class _BudgetReached(Exception):
    pass


def fit_inside(
    local_shape: BaseGeometry,
    parent: BaseGeometry,
    *,
    padding_mm: float = 0.0,
    fixed_center: Optional[tuple[float, float]] = None,
    angles_deg: Sequence[float] = (0.0,),
    obstacles: Sequence[BaseGeometry] = (),
    sibling_gap_mm: float = 0.0,
    max_evaluations: int = 6000,
    seed: int = 42,
    time_limit_s: Optional[float] = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> FitResult:
    """Search translation and positive uniform scale for explicit angle candidates.

    Supports concave containers and holes.  No binary search on scale
    feasibility (spec §1.3: monotonicity is NOT assumed for irregular
    parents).  Final validation is vector-based: ``covers`` + ``distance``
    + sibling collision checks.

    ``no_feasible_candidate`` is NOT a declaration that the problem is
    impossible — only that no valid candidate was found within budget.

    Parameters
    ----------
    local_shape:
        Centred asset geometry (``normalize_asset`` output).
    parent:
        Container geometry (world mm).  May be concave, may have holes.
    padding_mm:
        Required clearance to the container boundary.
    fixed_center:
        If set, the asset centre is fixed at this world point; only scale
        and angle are searched.
    angles_deg:
        Explicit angle candidates to try.
    obstacles:
        Sibling geometries to avoid.
    sibling_gap_mm:
        Minimum distance to each obstacle.
    max_evaluations:
        Budget of objective evaluations (256 / 6000 / 24000 per quality).
    seed:
        RNG seed for reproducibility (default 42).
    time_limit_s:
        Optional wall-clock limit.
    cancelled:
        Cooperative cancellation callback.

    Returns
    -------
    FitResult with ``pose`` (world mm), ``status``, ``evaluations``,
    and ``optimality_proven=False``.
    """
    require_shape(local_shape, "local_shape")
    require_shape(parent, "parent")

    padding_mm = _finite(padding_mm, "padding_mm", nonnegative=True)
    sibling_gap_mm = _finite(sibling_gap_mm, "sibling_gap_mm", nonnegative=True)

    if not isinstance(max_evaluations, int) or max_evaluations < 32:
        raise FittingError("INVALID_BUDGET", "max_evaluations must be >= 32")
    if not angles_deg:
        raise FittingError("INVALID_ANGLES", "At least one orientation is required")
    angles = tuple(_finite(a, "angle_deg") for a in angles_deg)

    if time_limit_s is not None:
        time_limit_s = _finite(time_limit_s, "time_limit_s", positive=True)

    if fixed_center is not None:
        fixed_center = tuple(_finite(v, "fixed_center") for v in fixed_center)
        if len(fixed_center) != 2:
            raise FittingError("INVALID_CENTER", "Expected (x, y)")

    for o in obstacles:
        require_shape(o, "obstacle")

    # Asset must be centred
    x0, y0, x1, y1 = local_shape.bounds
    if abs(x0 + x1) > 1e-6 or abs(y0 + y1) > 1e-6:
        raise FittingError("UNCENTRED_ASSET",
                           "Call normalize_asset before fitting")

    # Offset only accelerates scoring/seeding.  It does NOT replace final
    # clearance validation.
    usable = parent.buffer(-padding_mm, quad_segs=24) if padding_mm else parent
    if usable.is_empty:
        return FitResult(None, "empty_container", 0)

    left, top, right, bottom = usable.bounds
    start = time.monotonic()
    best: Optional[Pose] = None
    count = 0
    reason = "best_found"
    radius = math.hypot(max(abs(x0), abs(x1)), max(abs(y0), abs(y1)))

    def check_budget(angle_end: int) -> None:
        nonlocal reason
        if cancelled():
            reason = "cancelled"
            raise _BudgetReached
        if time_limit_s is not None and time.monotonic() - start >= time_limit_s:
            reason = "time_budget"
            raise _BudgetReached
        if count >= max_evaluations:
            reason = "evaluation_budget"
            raise _BudgetReached
        if count >= angle_end:
            raise _BudgetReached

    for angle_index, angle in enumerate(angles):
        angle_end = min(max_evaluations, ((angle_index + 1) * max_evaluations) // len(angles))
        rng = np.random.default_rng(seed + angle_index)
        rotated = apply_pose(local_shape, Pose(angle_deg=angle))
        rx0, ry0, rx1, ry1 = rotated.bounds
        upper = min(
            math.sqrt(usable.area / local_shape.area),
            (right - left) / (rx1 - rx0),
            (bottom - top) / (ry1 - ry0),
        )
        if upper <= 0:
            continue
        lower = max(upper * 1e-6, 1e-12)
        loglo, loghi = math.log(lower), math.log(upper)
        bx = (left, right) if fixed_center is None else (fixed_center[0], fixed_center[0])
        by = (top, bottom) if fixed_center is None else (fixed_center[1], fixed_center[1])
        bounds = [bx, by, (loglo, loghi)]

        def objective(v):
            nonlocal count, best
            check_budget(angle_end)
            count += 1
            pose = Pose(float(v[0]), float(v[1]), math.exp(float(v[2])), angle)
            candidate = apply_pose(local_shape, pose)
            valid = (fits(parent, candidate, padding_mm)
                     and siblings_clear(candidate, obstacles, sibling_gap_mm))
            if valid:
                if best is None or pose.scale > best.scale + 1e-12:
                    best = pose
                return -pose.scale / upper
            outside = candidate.difference(usable).area / candidate.area
            gap_error = 0.0
            for o in obstacles:
                gap_error += candidate.intersection(o).area / candidate.area
                gap_error += max(0.0, sibling_gap_mm - candidate.distance(o)) / max(sibling_gap_mm, 1.0)
            return 1.0 + outside + gap_error

        # Seeds
        seeds = []
        components = polygon_parts(usable)
        centres = [g.representative_point() for g in components]
        if fixed_center is not None:
            centres = []
            for s in np.geomspace(upper, lower, 64):
                seeds.append([fixed_center[0], fixed_center[1], math.log(float(s))])
        else:
            for p in centres:
                clearance = p.distance(usable.boundary)
                safe = min(upper, 0.8 * clearance / radius)
                for s in (safe, upper * .25, upper * .5, upper * .75, upper * (1 - 1e-10)):
                    if lower <= s <= upper:
                        seeds.append([p.x, p.y, math.log(s)])
            for x in np.linspace(left, right, 5):
                for y in np.linspace(top, bottom, 5):
                    seeds.append([float(x), float(y), math.log(upper * .5)])

        population = np.empty((96, 3))
        population[:, 0] = rng.uniform(*bx, 96) if bx[0] != bx[1] else bx[0]
        population[:, 1] = rng.uniform(*by, 96) if by[0] != by[1] else by[0]
        population[:, 2] = rng.uniform(math.log(max(lower, upper * .02)), loghi, 96)
        for i, v in enumerate(seeds[:96]):
            population[i] = v

        try:
            for v in seeds:
                objective(v)
            from scipy.optimize import differential_evolution
            differential_evolution(
                objective, bounds, rng=rng, init=population,
                maxiter=100000, tol=0, atol=0, polish=False,
                workers=1, updating="immediate",
            )
        except _BudgetReached:
            if reason in ("cancelled", "time_budget") or count >= max_evaluations:
                break

    if best is None:
        return FitResult(None, reason if reason != "best_found" else "no_feasible_candidate", count)

    # Final vector-based validation (spec §12.3 step 3)
    candidate = apply_pose(local_shape, best)
    if not fits(parent, candidate, padding_mm) or not siblings_clear(candidate, obstacles, sibling_gap_mm):
        raise RuntimeError("INTERNAL_ERROR: unvalidated fit must never be returned")

    return FitResult(best, reason, count)


# ---------------------------------------------------------------------------
# Task 14 — fit_subtree (top-down, sequential siblings)
# ---------------------------------------------------------------------------

def fit_subtree(
    doc: Mapping[str, Any],
    root_id: str,
    *,
    geometries: Mapping[str, BaseGeometry],
    padding_mm: float = 0.0,
    sibling_gap_mm: float = 2.0,
    max_evaluations: int = 6000,
    seed: int = 42,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict:
    """Fit a subtree top-down: parent first, then children sequentially.

    Locks are treated as obstacles.  A failure of one child does NOT
    silently confirm the previous ones — the result carries a ``failed``
    list and the caller must decide whether to apply.

    Returns
    -------
    dict with:
        ``proposals``: {layer_id: {"pose_world": Pose, "pose_local": Pose,
                                   "scale": float, "status": str}}
        ``failed``:    {layer_id: str}  (error code per failed child)
        ``evaluations``: total evaluations across all children
    """
    layers = doc["layers"]
    if root_id not in layers:
        raise FittingError("UNKNOWN_LAYER", f"layer {root_id!r} does not exist")

    # Collect the subtree (root + all descendants)
    subtree: list[str] = []
    def _collect(lid: str) -> None:
        subtree.append(lid)
        for child_id, child in layers.items():
            if child.get("parent_id") == lid:
                _collect(child_id)
    _collect(root_id)

    # Determine the container for the root
    root_node = layers[root_id]
    parent_id = root_node.get("parent_id")
    if parent_id is not None:
        container = geometries.get(parent_id)
        if container is None:
            raise FittingError("MISSING_GEOMETRY",
                               f"parent {parent_id!r} has no geometry")
    else:
        canvas = doc["canvas"]
        container = inner_canvas(canvas["width_mm"], canvas["height_mm"],
                                 canvas.get("padding_mm", {"left": 0, "right": 0,
                                                           "top": 0, "bottom": 0}))

    proposals: dict[str, dict] = {}
    failed: dict[str, str] = {}
    total_evals = 0

    # Fit the root first
    root_geom = geometries.get(root_id)
    if root_geom is None:
        raise FittingError("MISSING_GEOMETRY", f"layer {root_id!r} has no geometry")

    # Obstacles: locked siblings of the root
    obstacles = []
    if parent_id is not None:
        for sib_id, sib in layers.items():
            if sib_id != root_id and sib.get("parent_id") == parent_id and sib.get("locked", False):
                if sib_id in geometries:
                    obstacles.append(geometries[sib_id])

    result = fit_inside(
        root_geom, container,
        padding_mm=padding_mm,
        obstacles=obstacles,
        sibling_gap_mm=sibling_gap_mm,
        max_evaluations=max_evaluations,
        seed=seed,
        cancelled=cancelled,
    )
    total_evals += result.evaluations

    if result.pose is None:
        failed[root_id] = result.status
        return {"proposals": proposals, "failed": failed, "evaluations": total_evals}

    # Convert to local
    if parent_id is not None:
        parent_world = world_pose(parent_id, layers)
        pose_local = reparent_pose(result.pose, parent_world)
    else:
        pose_local = result.pose

    proposals[root_id] = {
        "pose_world": result.pose,
        "pose_local": pose_local,
        "scale": result.pose.scale,
        "status": result.status,
    }

    # Fit children sequentially (top-down)
    children = [lid for lid in subtree[1:] if layers[lid].get("parent_id") == root_id]
    for child_id in children:
        if cancelled():
            failed[child_id] = "cancelled"
            continue
        child_geom = geometries.get(child_id)
        if child_geom is None:
            failed[child_id] = "missing_geometry"
            continue

        # Container for the child: the root's (proposed) world geometry
        # scaled by the root's proposed scale
        root_proposed = apply_pose(root_geom, result.pose)
        child_obstacles = []
        for sib_id, sib in layers.items():
            if sib_id != child_id and sib.get("parent_id") == root_id:
                if sib.get("locked", False) and sib_id in geometries:
                    child_obstacles.append(geometries[sib_id])

        child_result = fit_inside(
            child_geom, root_proposed,
            padding_mm=padding_mm,
            obstacles=child_obstacles,
            sibling_gap_mm=sibling_gap_mm,
            max_evaluations=max_evaluations,
            seed=seed + hash(child_id) % 1000,
            cancelled=cancelled,
        )
        total_evals += child_result.evaluations

        if child_result.pose is None:
            failed[child_id] = child_result.status
            continue

        # Child's world pose is relative to the root's proposed world frame
        child_world = compose_pose(result.pose, child_result.pose)
        proposals[child_id] = {
            "pose_world": child_world,
            "pose_local": child_result.pose,
            "scale": child_result.pose.scale,
            "status": child_result.status,
        }

    return {"proposals": proposals, "failed": failed, "evaluations": total_evals}


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _finite(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise FittingError("INVALID_NUMBER", f"{name} cannot be a boolean")
    try:
        x = float(value)
    except (ValueError, TypeError) as exc:
        raise FittingError("INVALID_NUMBER", f"{name} must be numeric") from exc
    if not math.isfinite(x) or (positive and x <= 0) or (nonnegative and x < 0):
        raise FittingError("INVALID_NUMBER", f"{name}: invalid value {value!r}")
    return x
