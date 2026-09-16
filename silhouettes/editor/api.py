"""Flask Blueprint for the editor v2 API (tasks 03, 13, 18, 20, 21).

Routes (spec §8.1):

    POST /api/v2/documents
    GET  /api/v2/documents/<doc_id>
    POST /api/v2/documents/<doc_id>/commands
    POST /api/v2/documents/<doc_id>/assets
    GET  /api/v2/documents/<doc_id>/assets/<asset_id>/preview
    POST /api/v2/documents/<doc_id>/fit
    POST /api/v2/documents/<doc_id>/preview
    POST /api/v2/documents/<doc_id>/exports
    GET  /api/v2/jobs/<job_id>
    POST /api/v2/jobs/<job_id>/cancel
    GET  /api/v2/jobs/<job_id>/download
    GET  /api/v2/documents/<doc_id>/package
    POST /api/v2/documents/import

Error envelope (spec §8.4):
    {"error": {"code", "message", "layer_id", "details", "recoverable"}, "revision": N}
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from flask import Blueprint, Flask, current_app, jsonify, request, send_file

from .asset_adapter import AssetImportError, import_batch
from .commands import CommandError
from .composition import compose_document, recipe_summary
from .constraints import canvas_shape, fits, inner_canvas
from .document_store import DocumentStore, StoreError
from .exports import (
    ExportError, build_export_plan, geometry_png, geometry_svg,
    pack_bundle, resolve_export_selection, safe_slug,
)
from .fitting import FittingError, contain_rect, fit_inside
from .jobs import JobScheduler
from .mesh_adapter import MeshAdapterError, export_stl
from .models import DocumentError, validate_document
from .project_io import ProjectIOError, load_package, save_package
from .transforms import Pose, apply_pose, compose_pose, normalize_asset, reparent_pose, world_pose

log = logging.getLogger("silhouettes.editor.api")

editor_bp = Blueprint("editor_v2", __name__, url_prefix="/api/v2")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _get_store() -> DocumentStore:
    store = current_app.extensions.get("editor_store")
    if store is None:
        store = current_app.config.get("EDITOR_STORE")
    if store is None:
        raise StoreError("INTERNAL", "editor store is not configured on this app", status=500)
    return store


def _get_scheduler() -> JobScheduler:
    sched = current_app.extensions.get("job_scheduler")
    if sched is None:
        sched = current_app.config.get("JOB_SCHEDULER")
    if sched is None:
        raise StoreError("INTERNAL", "job scheduler is not configured on this app", status=500)
    return sched


def _error_payload(code: str, message: str, status: int, layer_id: Optional[str] = None,
                   details: Optional[dict] = None, revision: Optional[int] = None) -> tuple:
    body = {"error": {"code": code, "message": message, "layer_id": layer_id,
                      "details": details or {}, "recoverable": status in (400, 404, 409, 422)}}
    if revision is not None:
        body["revision"] = revision
    return jsonify(body), status


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise CommandError("INVALID_STRUCTURE", "request body must be a JSON object")
    return data


def _load_doc_or_404(doc_id: str) -> dict:
    store = _get_store()
    try:
        return store.load_document(doc_id)
    except StoreError as exc:
        raise _http_error(exc)


def _http_error(exc: Exception) -> Exception:
    """Re-raise as an HTTPException-like object the errorhandler can catch."""
    return exc


def _asset_geometry(doc: dict, asset_id: str):
    """Return Shapely geometry for an asset in **local mm**, centred at origin.

    Parses the canonical SVG (source units) and applies ``normalize_asset``.
    """
    asset = doc["assets"].get(asset_id)
    if asset is None:
        raise CommandError("UNKNOWN_ASSET", f"asset {asset_id!r} not found", status=404)
    geom = asset.get("_geometry_local")
    if geom is not None:
        return geom
    from silhouettes.vector import parse_vector, vector_to_polygons
    from shapely.ops import unary_union
    svg = asset.get("canonical_svg")
    if svg is None:
        raise CommandError("MISSING_GEOMETRY", f"asset {asset_id!r} has no geometry", status=404)
    parsed = parse_vector(svg)
    polys = vector_to_polygons(parsed)
    if not polys:
        raise CommandError("EMPTY_GEOMETRY", f"asset {asset_id!r} has no polygons", status=422)
    raw = unary_union(polys) if len(polys) > 1 else polys[0]
    k = float(asset.get("mm_per_source_unit") or 1.0)
    local, _n = normalize_asset(raw, k)
    doc["assets"][asset_id]["_geometry_local"] = local
    return local


def _layer_world_geom(doc: dict, layer_id: str):
    """World geometry of a layer (local mm geometry under world pose)."""
    node = doc["layers"].get(layer_id)
    if node is None:
        raise CommandError("UNKNOWN_LAYER", f"layer {layer_id!r} not found", status=404)
    geom = _asset_geometry(doc, node["asset_id"])
    wp = world_pose(layer_id, doc["layers"])
    return apply_pose(geom, wp)


def _pose_to_dict(pose: Pose) -> dict:
    return {
        "tx": float(pose.tx),
        "ty": float(pose.ty),
        "scale": float(pose.scale),
        "angle_deg": float(pose.angle_deg),
    }


def _run_fit_sync(doc: dict, body: dict) -> dict:
    """Synchronous fit: returns pose_local ready for ``apply_fit_result``."""
    layer_id = body.get("layer_id")
    if not layer_id or layer_id not in doc["layers"]:
        raise CommandError("UNKNOWN_LAYER", f"layer {layer_id!r} not found", status=404)

    target = body.get("target", "canvas")
    padding = float(body.get("padding_mm", 2.0))
    angles = body.get("angles_deg", [0.0])
    if not isinstance(angles, list) or not angles:
        angles = [0.0]
    max_eval = int(body.get("max_evaluations", 4000))
    seed = int(body.get("seed", 42))
    mode = body.get("mode", "best")  # "best" | "at_position"

    node = doc["layers"][layer_id]
    local_geom = _asset_geometry(doc, node["asset_id"])

    if target == "canvas":
        canvas = doc["canvas"]
        container = inner_canvas(canvas["width_mm"], canvas["height_mm"], canvas["padding_mm"])
        if mode == "at_position":
            # Keep centre, only grow/shrink to fit usable canvas.
            wp = world_pose(layer_id, doc["layers"])
            fr = fit_inside(
                local_geom, container,
                padding_mm=padding,
                fixed_center=(wp.tx, wp.ty),
                angles_deg=angles,
                max_evaluations=max(256, max_eval // 4),
                seed=seed,
            )
            if fr.pose is None:
                raise FittingError(fr.status, f"fit_at_position: {fr.status}")
            pose_world = fr.pose
        else:
            # Exact bbox fit for rectangular canvas
            angle = float(angles[0])
            pose_world = contain_rect(local_geom, container, angle_deg=angle)
            # Shrink by padding via a second contain on the padded rect
            if padding > 0:
                padded = inner_canvas(
                    canvas["width_mm"], canvas["height_mm"],
                    {
                        "left": canvas["padding_mm"]["left"] + padding,
                        "right": canvas["padding_mm"]["right"] + padding,
                        "top": canvas["padding_mm"]["top"] + padding,
                        "bottom": canvas["padding_mm"]["bottom"] + padding,
                    },
                )
                pose_world = contain_rect(local_geom, padded, angle_deg=angle)
    elif target in ("parent_shape", "parent_hole"):
        parent_id = node.get("parent_id")
        if parent_id is None:
            raise CommandError("NO_PARENT",
                               "layer has no parent — nest it first (modo matrioska)", status=422)
        parent_geom = _layer_world_geom(doc, parent_id)
        if target == "parent_hole":
            holes = []
            if hasattr(parent_geom, "interiors") and parent_geom.interiors:
                from shapely.geometry import Polygon
                holes = [Polygon(ring) for ring in parent_geom.interiors]
            if not holes and hasattr(parent_geom, "geoms"):
                parts = list(parent_geom.geoms)
                if len(parts) > 1:
                    holes = sorted(parts, key=lambda p: p.area)[:-1]
            if not holes:
                raise CommandError("NO_HOLES", "parent has no holes to fit into", status=422)
            container = max(holes, key=lambda h: h.area)
        else:
            container = parent_geom

        fixed = None
        if mode == "at_position":
            wp = world_pose(layer_id, doc["layers"])
            fixed = (wp.tx, wp.ty)

        # Sibling obstacles (other children of the same parent)
        obstacles = []
        for lid, other in doc["layers"].items():
            if lid == layer_id:
                continue
            if other.get("parent_id") != parent_id:
                continue
            if not other.get("visible", True):
                continue
            try:
                obstacles.append(_layer_world_geom(doc, lid))
            except Exception:
                pass

        fr = fit_inside(
            local_geom, container,
            padding_mm=padding,
            fixed_center=fixed,
            angles_deg=angles,
            obstacles=obstacles,
            sibling_gap_mm=float((node.get("fit") or {}).get("sibling_gap_mm") or 2.0),
            max_evaluations=max_eval,
            seed=seed,
        )
        if fr.pose is None:
            raise FittingError(fr.status, f"no feasible pose ({fr.status})")
        pose_world = fr.pose
    else:
        raise CommandError("UNKNOWN_TARGET", f"unknown fit target {target!r}", status=400)

    parent_id = node.get("parent_id")
    if parent_id is not None:
        pose_local = reparent_pose(pose_world, world_pose(parent_id, doc["layers"]))
    else:
        pose_local = pose_world

    return {
        "status": "completed",
        "layer_id": layer_id,
        "target": target,
        "mode": mode,
        "pose": _pose_to_dict(pose_local),
        "pose_local": _pose_to_dict(pose_local),
        "pose_world": _pose_to_dict(pose_world),
        "project_revision": doc["revision"],
        "request_seq": body.get("request_seq"),
    }


def _effective_visible(layers: dict, layer_id: str) -> bool:
    current = layer_id
    seen = set()
    while current is not None:
        if current in seen:
            return False
        seen.add(current)
        node = layers.get(current)
        if node is None:
            return False
        if not node.get("visible", True):
            return False
        current = node.get("parent_id")
    return True


# ---------------------------------------------------------------------------
# document routes
# ---------------------------------------------------------------------------

@editor_bp.post("/documents")
def create_document() -> Any:
    body = _json_body()
    name = body.get("name", "Nuevo documento")
    canvas = body.get("canvas")
    store = _get_store()
    try:
        doc = store.create_document(name=name, canvas=canvas)
    except (CommandError, StoreError, DocumentError) as exc:
        code = getattr(exc, "code", "INVALID_STRUCTURE")
        status = getattr(exc, "status", 400)
        return _error_payload(code, str(exc), status)
    return jsonify(doc), 201


@editor_bp.get("/documents/<doc_id>")
def get_document(doc_id: str) -> Any:
    doc = _load_doc_or_404(doc_id)
    return jsonify(doc), 200


@editor_bp.get("/documents")
def list_documents() -> Any:
    """List stored documents (id, name, revision) — used by the editor
    boot sequence to restore the most recent document (task 07/21)."""
    store = _get_store()
    return jsonify({"documents": store.list_documents()}), 200


@editor_bp.post("/documents/<doc_id>/commands")
def post_command(doc_id: str) -> Any:
    body = _json_body()
    store = _get_store()
    try:
        result = store.commit_command(doc_id, body)
    except StoreError as exc:
        return _error_payload(exc.code, str(exc), exc.status, revision=exc.revision)
    except CommandError as exc:
        return _error_payload(exc.code, str(exc), exc.status, layer_id=exc.layer_id, details=exc.details)
    except Exception:
        log.exception("unexpected error in command %s", body.get("command_id"))
        return _error_payload("INTERNAL", "unexpected server error", 500)
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# asset routes
# ---------------------------------------------------------------------------

@editor_bp.post("/documents/<doc_id>/assets")
def post_assets(doc_id: str) -> Any:
    store = _get_store()
    doc = _load_doc_or_404(doc_id)

    base_revision = request.form.get("base_revision")
    if base_revision is not None:
        try:
            base_revision = int(base_revision)
        except (ValueError, TypeError):
            return _error_payload("INVALID_STRUCTURE", "base_revision must be an integer", 400)

    files = request.files.getlist("files[]") or request.files.getlist("files")
    if not files:
        return _error_payload("INVALID_STRUCTURE", "no files provided in 'files[]'", 400)

    file_list = [(f.filename, f.read()) for f in files if f.filename]
    if not file_list:
        return _error_payload("INVALID_STRUCTURE", "no valid files in upload", 400)

    try:
        assets, errors = import_batch(file_list)
    except AssetImportError as exc:
        return _error_payload(exc.code, exc.message, 400)

    if not assets:
        return _error_payload("IMPORT_FAILED", "all files failed to import", 422,
                              details={"errors": errors})

    asset_dicts = [a.to_document_asset() for a in assets]
    canvas = doc.get("canvas") or {}
    cw = float(canvas.get("width_mm") or 200)
    ch = float(canvas.get("height_mm") or 200)
    pad = canvas.get("padding_mm") or {}
    pl = float(pad.get("left") or 0)
    pr = float(pad.get("right") or 0)
    pt = float(pad.get("top") or 0)
    pb = float(pad.get("bottom") or 0)
    usable_w = max(1e-6, cw - pl - pr)
    usable_h = max(1e-6, ch - pt - pb)
    cx = pl + usable_w / 2.0
    cy = pt + usable_h / 2.0

    layer_specs = []
    for i, a in enumerate(assets):
        # Name the layer after the source file (without extension) so the
        # tree shows "mountain.png" → "mountain", not an opaque asset id.
        stem = a.source_filename
        if stem:
            stem = Path(stem).stem or stem.rsplit(".", 1)[0]
        asset_dict = asset_dicts[i]
        lb = asset_dict.get("local_bounds") or [-50, -50, 50, 50]
        local_w = max(1e-6, float(lb[2]) - float(lb[0]))
        local_h = max(1e-6, float(lb[3]) - float(lb[1]))
        # Fit into the usable canvas so imports are not giant / off-sheet.
        scale = min(usable_w / local_w, usable_h / local_h)
        layer_specs.append({
            "id": f"layer_{a.asset_id[6:]}_{i}",
            "asset_id": a.asset_id,
            "name": stem or a.asset_id,
            "pose": {"tx": cx, "ty": cy, "scale": scale, "angle_deg": 0},
        })
    # The 2D viewport renders the canonical SVG paths, so the document must
    # carry the canonical SVG text for each imported asset (task 07).
    for a, d in zip(assets, asset_dicts):
        d["canonical_svg"] = a.canonical_svg
        d["source_filename"] = a.source_filename
        d["name"] = Path(a.source_filename).stem if a.source_filename else a.asset_id

    command = {
        "command_id": f"import_{doc_id}_{uuid.uuid4().hex[:8]}",
        "base_revision": base_revision if base_revision is not None else doc["revision"],
        "type": "add_layers",
        "payload": {"assets": asset_dicts, "layers": layer_specs},
    }

    try:
        result = store.commit_command(doc_id, command)
    except StoreError as exc:
        return _error_payload(exc.code, str(exc), exc.status, revision=exc.revision)
    except CommandError as exc:
        return _error_payload(exc.code, str(exc), exc.status, details=exc.details)

    # commit_command already returns {"document": ..., "revision": ...}.
    # Do NOT wrap it again — the client expects the bare document.
    return jsonify({
        "document": result["document"],
        "revision": result["revision"],
        "imported": len(assets),
        "errors": errors,
    }), 200


@editor_bp.get("/documents/<doc_id>/assets/<asset_id>/preview")
def asset_preview(doc_id: str, asset_id: str) -> Any:
    """Return the canonical SVG geometry as an SVG image (read-only preview)."""
    doc = _load_doc_or_404(doc_id)
    asset = doc["assets"].get(asset_id)
    if asset is None:
        return _error_payload("UNKNOWN_ASSET", f"asset {asset_id!r} not found", 404)

    svg = asset.get("canonical_svg")
    if svg is None:
        return _error_payload("MISSING_GEOMETRY", f"asset {asset_id!r} has no canonical SVG", 404)

    from flask import Response
    return Response(svg, mimetype="image/svg+xml")


# ---------------------------------------------------------------------------
# fit routes (task 13)
# ---------------------------------------------------------------------------

@editor_bp.post("/documents/<doc_id>/fit")
def post_fit(doc_id: str) -> Any:
    """Fit a layer into canvas or parent (synchronous by default).

    Body: {layer_id, target: "canvas"|"parent_shape"|"parent_hole",
           mode: "best"|"at_position",
           padding_mm, angles_deg, max_evaluations, seed, request_seq,
           async: false}

    Returns 200 with {pose, pose_local, pose_world, ...} ready to apply.
    """
    body = _json_body()
    doc = _load_doc_or_404(doc_id)

    try:
        result = _run_fit_sync(doc, body)
    except FittingError as exc:
        return _error_payload(exc.code if hasattr(exc, "code") else "FIT_FAILED",
                              str(exc), 422)
    except CommandError as exc:
        return _error_payload(exc.code, str(exc), exc.status, details=getattr(exc, "details", None))
    except Exception as exc:
        log.exception("fit failed")
        return _error_payload("FIT_FAILED", str(exc), 500)

    return jsonify(result), 200


def _constrain_pose_to_parent(doc: dict, layer_id: str, pose_local: Pose,
                              padding_mm: float = 1.0) -> Pose:
    """Return a parent-local pose whose SVG contour stays inside the parent.

    Uses Shapely ``covers`` on the exact polygonised SVG contours.  If the
    proposed pose already fits, it is returned unchanged; otherwise scale is
    searched at the same centre, then a full ``fit_inside`` as fallback.
    """
    node = doc["layers"][layer_id]
    parent_id = node.get("parent_id")
    if parent_id is None:
        return pose_local

    local_geom = _asset_geometry(doc, node["asset_id"])
    parent_world = _layer_world_geom(doc, parent_id)
    pw = world_pose(parent_id, doc["layers"])
    pose_world = compose_pose(pw, pose_local)
    candidate = apply_pose(local_geom, pose_world)
    if fits(parent_world, candidate, padding_mm):
        return pose_local

    fr = fit_inside(
        local_geom, parent_world,
        padding_mm=padding_mm,
        fixed_center=(pose_world.tx, pose_world.ty),
        angles_deg=[pose_world.angle_deg],
        max_evaluations=3000,
        seed=42,
    )
    if fr.pose is None:
        fr = fit_inside(
            local_geom, parent_world,
            padding_mm=padding_mm,
            angles_deg=[pose_world.angle_deg],
            max_evaluations=8000,
            seed=42,
        )
    if fr.pose is None:
        raise FittingError(fr.status, f"constrain: child contour crosses parent ({fr.status})")
    return reparent_pose(fr.pose, pw)


@editor_bp.post("/documents/<doc_id>/constrain")
def post_constrain(doc_id: str) -> Any:
    """Clamp a proposed pose so the child SVG contour stays inside the parent.

    Body: {layer_id, pose: {tx,ty,scale,angle_deg}, padding_mm?}
    """
    body = _json_body()
    doc = _load_doc_or_404(doc_id)
    layer_id = body.get("layer_id")
    if not layer_id or layer_id not in doc["layers"]:
        return _error_payload("UNKNOWN_LAYER", f"layer {layer_id!r} not found", 404)
    raw = body.get("pose") or {}
    try:
        pose = Pose(
            tx=float(raw.get("tx", 0)),
            ty=float(raw.get("ty", 0)),
            scale=float(raw.get("scale", 1)),
            angle_deg=float(raw.get("angle_deg", 0)),
        )
        if pose.scale <= 0:
            raise ValueError("scale must be > 0")
        padding = float(body.get("padding_mm", 1.0))
        out = _constrain_pose_to_parent(doc, layer_id, pose, padding_mm=padding)
    except FittingError as exc:
        return _error_payload(exc.code if hasattr(exc, "code") else "CONSTRAIN_FAILED",
                              str(exc), 422)
    except CommandError as exc:
        return _error_payload(exc.code, str(exc), exc.status, details=getattr(exc, "details", None))
    except Exception as exc:
        log.exception("constrain failed")
        return _error_payload("CONSTRAIN_FAILED", str(exc), 500)

    return jsonify({
        "layer_id": layer_id,
        "pose": _pose_to_dict(out),
        "pose_local": _pose_to_dict(out),
        "project_revision": doc["revision"],
    }), 200


# ---------------------------------------------------------------------------
# preview route (task 15)
# ---------------------------------------------------------------------------

@editor_bp.post("/documents/<doc_id>/preview")
def post_preview(doc_id: str) -> Any:
    """Compute recipe geometry for selected layers (read-only, no revision change).

    Body: {mode: "normal"|"inverse"|"shell", layer_ids: [...], request_seq}
    Returns: {layer_id: {svg, summary}} for each layer.
    """
    body = _json_body()
    doc = _load_doc_or_404(doc_id)

    mode = body.get("mode", "normal")
    layer_ids = body.get("layer_ids", list(doc["layers"].keys()))
    request_seq = body.get("request_seq")

    if mode not in ("normal", "inverse", "shell"):
        return _error_payload("UNKNOWN_MODE", f"unknown recipe mode {mode!r}", 400)

    canvas = doc["canvas"]
    C = canvas_shape(canvas["width_mm"], canvas["height_mm"])

    try:
        geometries = compose_document(doc, layer_ids, mode, lenient=True)
    except Exception as exc:
        code = getattr(exc, "code", "COMPOSITION_FAILED")
        return _error_payload(code, str(exc), 422)

    results = {}
    for lid, geom in geometries.items():
        try:
            svg = geometry_svg(geom, canvas["width_mm"], canvas["height_mm"])
        except ExportError:
            svg = ""
        summary = recipe_summary(geom)
        results[lid] = {"svg": svg, "summary": summary}

    return jsonify({
        "mode": mode,
        "request_seq": request_seq,
        "project_revision": doc["revision"],
        "layers": results,
    }), 200


# ---------------------------------------------------------------------------
# export routes (tasks 18, 19, 20)
# ---------------------------------------------------------------------------

@editor_bp.post("/documents/<doc_id>/exports")
def post_exports(doc_id: str) -> Any:
    """Submit an export job. Returns 202 with job descriptor.

    Body: {layer_ids, families, formats, png_width_px, request_seq}
    """
    body = _json_body()
    doc = _load_doc_or_404(doc_id)
    sched = _get_scheduler()

    layer_ids = body.get("layer_ids", list(doc["layers"].keys()))
    families = body.get("families", ["normal_registered", "inverse_registered", "normal_fullframe"])
    formats = body.get("formats", ["svg", "png", "stl"])
    png_width = int(body.get("png_width_px", 1000))
    request_seq = body.get("request_seq")

    canvas = doc["canvas"]
    w, h = canvas["width_mm"], canvas["height_mm"]
    padding = canvas["padding_mm"]

    # Resolve selection (visibility + export_enabled)
    try:
        selected = resolve_export_selection(doc["layers"], layer_ids)
    except Exception as exc:
        code = getattr(exc, "code", "SELECTION_ERROR")
        return _error_payload(code, str(exc), 422)

    if not selected:
        return _error_payload("EMPTY_SELECTION", "no layers selected for export", 422)

    # Build the export plan
    try:
        plan = build_export_plan(
            doc["layers"], doc["assets"], selected, w, h, padding,
            families=families,
            default_extrusion_mm=doc.get("default_extrusion_mm", 3.0),
        )
    except ExportError as exc:
        return _error_payload(exc.code, str(exc), 422)

    # Serialize plan for the worker (WKB hex)
    import base64
    from shapely.wkb import dumps as wkb_dumps

    plan_serialized = []
    for item in plan:
        plan_serialized.append({
            "layer_id": item.layer_id,
            "family": item.family,
            "stem": item.stem,
            "extrusion_mm": item.extrusion_mm,
            "geom_hex": base64.b64encode(wkb_dumps(item.geometry)).decode("ascii"),
            "pose": asdict(item.pose) if hasattr(item, 'pose') else None,
        })

    payload = {
        "task": "export",
        "plan": plan_serialized,
        "width_mm": w,
        "height_mm": h,
        "formats": formats,
        "png_width_px": png_width,
        "project_revision": doc["revision"],
    }

    rec = sched.submit(
        job_type="export",
        payload=payload,
        document_id=doc_id,
        source_revision=doc["revision"],
        request_seq=request_seq,
    )
    return jsonify(rec.to_dict()), 202


# ---------------------------------------------------------------------------
# job routes
# ---------------------------------------------------------------------------

@editor_bp.get("/jobs/<job_id>")
def get_job(job_id: str) -> Any:
    sched = _get_scheduler()
    rec = sched.get(job_id)
    if rec is None:
        return _error_payload("NOT_FOUND", "unknown job id", 404)
    return jsonify(rec.to_dict()), 200


@editor_bp.post("/jobs/<job_id>/cancel")
def cancel_job(job_id: str) -> Any:
    sched = _get_scheduler()
    rec = sched.get(job_id)
    if rec is None:
        return _error_payload("NOT_FOUND", "unknown job id", 404)
    if rec.state in ("completed", "failed", "cancelled"):
        return _error_payload("ALREADY_TERMINAL", "job is already in a terminal state", 409)
    sched.cancel(job_id)
    return jsonify({"id": job_id, "state": "cancelling"}), 200


@editor_bp.get("/jobs/<job_id>/download")
def download_job(job_id: str) -> Any:
    sched = _get_scheduler()
    path = sched.download_path(job_id)
    if path is None:
        rec = sched.get(job_id)
        if rec is None:
            return _error_payload("NOT_FOUND", "unknown job id", 404)
        if rec.state != "completed":
            return _error_payload("NOT_COMPLETED", "job has not completed", 409)
        return _error_payload("NO_DOWNLOAD", "no downloadable file for this job", 404)
    return send_file(str(path), as_attachment=True, download_name=path.name)


# ---------------------------------------------------------------------------
# project package routes (task 21)
# ---------------------------------------------------------------------------

@editor_bp.get("/documents/<doc_id>/package")
def download_package(doc_id: str) -> Any:
    """Download a portable .silhouettes ZIP package for this document."""
    doc = _load_doc_or_404(doc_id)
    store = _get_store()

    # Collect asset source bytes from the store's asset directory
    assets_bytes: dict[str, bytes] = {}
    assets_dir = store.documents_dir.parent / "assets"
    for asset_id, asset in doc.get("assets", {}).items():
        src = assets_dir / asset_id / f"source.{asset.get('source_type', 'bin')}"
        if src.exists():
            assets_bytes[asset_id] = src.read_bytes()
        # Also try the canonical SVG
        canon = assets_dir / asset_id / "canonical.svg"
        if canon.exists():
            asset["canonical_svg"] = canon.read_text(encoding="utf-8")

    out_path = store.documents_dir.parent / f"{doc_id}.silhouettes"
    try:
        save_package(doc, assets_bytes, out_path)
    except ProjectIOError as exc:
        return _error_payload(exc.code, str(exc), 500)

    return send_file(str(out_path), as_attachment=True,
                     download_name=f"{safe_slug(doc.get('name', doc_id))}.silhouettes")


@editor_bp.post("/documents/import")
def import_package() -> Any:
    """Import a .silhouettes package as a NEW document (never overwrites)."""
    if "file" not in request.files:
        return _error_payload("INVALID_STRUCTURE", "multipart field 'file' is required", 400)
    f = request.files["file"]
    if not f.filename:
        return _error_payload("INVALID_STRUCTURE", "no file provided", 400)

    store = _get_store()
    staging = store.documents_dir.parent / "staging" / uuid.uuid4().hex[:8]

    # Save upload to a temp file
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".silhouettes", delete=False) as tmp:
        f.save(tmp.name)
        tmp_path = Path(tmp.name)

    try:
        doc, assets_bytes = load_package(tmp_path, staging)
    except ProjectIOError as exc:
        return _error_payload(exc.code, str(exc), 400)
    finally:
        tmp_path.unlink(missing_ok=True)

    # Assign a new document id (never reuse the old one)
    new_id = f"doc_{uuid.uuid4().hex[:12]}"
    doc["id"] = new_id
    doc["revision"] = 0

    # Store the document
    try:
        store.create_document(name=doc.get("name", "Imported"), canvas=None)
        # Overwrite with the imported content
        import copy
        envelope = {
            "envelope_version": 1,
            "document": doc,
            "command_results": {},
        }
        store._write_envelope(new_id, envelope)
        store._cache[new_id] = envelope
    except Exception as exc:
        return _error_payload("IMPORT_FAILED", str(exc), 500)

    return jsonify(doc), 201


# ---------------------------------------------------------------------------
# app factory
# ---------------------------------------------------------------------------

def create_editor_app(
    store: Optional[DocumentStore] = None,
    scheduler: Optional[JobScheduler] = None,
) -> Flask:
    """Build a Flask app with the editor blueprint (used by tests)."""
    app = Flask(__name__)
    app.extensions["editor_store"] = store
    if scheduler is not None:
        app.extensions["job_scheduler"] = scheduler
    app.register_blueprint(editor_bp)

    @app.errorhandler(StoreError)
    def _store_error(exc: StoreError):
        return _error_payload(exc.code, str(exc), exc.status, revision=exc.revision)

    @app.errorhandler(CommandError)
    def _command_error(exc: CommandError):
        return _error_payload(exc.code, str(exc), exc.status, layer_id=exc.layer_id, details=exc.details)

    @app.errorhandler(DocumentError)
    def _doc_error(exc: DocumentError):
        return _error_payload(exc.code, str(exc), getattr(exc, "status", 400))

    return app
