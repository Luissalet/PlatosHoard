"""Command handlers for the Silhouettes editor (task 03).

Each handler receives a **copy** of the document and a payload dict,
returns the modified copy, or raises ``CommandError`` with a stable code.

Only ``set_pose``, ``set_canvas`` and ``set_layer_properties`` are
implemented in this task.  All other command types are rejected with
``UNKNOWN_COMMAND`` (HTTP 422) until their own task implements them —
never simulated as success.
"""
from __future__ import annotations

import copy
import math
import uuid
from typing import Any, Mapping

from .transforms import Pose, world_pose, reparent_pose

# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class CommandError(ValueError):
    """Structured command-level error.

    ``code`` is one of the uniform error codes from spec §8.4.
    ``status`` is the HTTP status the API layer should return.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        layer_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.layer_id = layer_id
        self.details = details or {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _require_layer(doc: Mapping[str, Any], layer_id: Any) -> dict:
    if not isinstance(layer_id, str) or not layer_id:
        raise CommandError("INVALID_STRUCTURE", "layer_id must be a non-empty string")
    layers = doc.get("layers", {})
    if layer_id not in layers:
        raise CommandError(
            "UNKNOWN_LAYER",
            f"layer {layer_id!r} does not exist in this document",
            status=404,
            layer_id=layer_id,
        )
    return layers[layer_id]


def _validate_pose(pose: Any) -> None:
    if not isinstance(pose, Mapping):
        raise CommandError("INVALID_STRUCTURE", "pose must be an object")
    required = ("tx", "ty", "scale", "angle_deg")
    for key in required:
        if key not in pose:
            raise CommandError("INVALID_STRUCTURE", f"pose.{key} is required")
        if isinstance(pose[key], bool) or not _finite_number(pose[key]):
            raise CommandError(
                "INVALID_NUMBER",
                f"pose.{key} must be a finite number, got {pose[key]!r}",
            )
    if pose["scale"] <= 0:
        raise CommandError(
            "INVALID_NUMBER",
            f"pose.scale must be > 0, got {pose['scale']!r}",
        )
    if set(pose.keys()) - set(required):
        raise CommandError(
            "INVALID_STRUCTURE",
            f"pose has unauthorized fields: {sorted(set(pose.keys()) - set(required))}",
        )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def cmd_set_pose(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Move / scale / rotate one layer (one gesture)."""
    layer_id = payload.get("layer_id")
    layer = _require_layer(doc, layer_id)
    if layer.get("locked", False):
        raise CommandError("LOCKED", f"layer {layer_id!r} is locked", status=423,
                           layer_id=layer_id, details={"locked_ids": [layer_id]})
    pose = payload.get("pose")
    _validate_pose(pose)
    doc["layers"][layer_id]["pose"] = {
        "tx": pose["tx"],
        "ty": pose["ty"],
        "scale": pose["scale"],
        "angle_deg": pose["angle_deg"],
    }
    return doc


def cmd_set_canvas(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Resize canvas and/or padding.  Never crops assets."""
    canvas = doc.get("canvas", {})
    width = payload.get("width_mm", canvas.get("width_mm"))
    height = payload.get("height_mm", canvas.get("height_mm"))

    if width is not None and (isinstance(width, bool) or not _finite_number(width) or width <= 0):
        raise CommandError("INVALID_NUMBER", f"width_mm must be > 0, got {width!r}")
    if height is not None and (isinstance(height, bool) or not _finite_number(height) or height <= 0):
        raise CommandError("INVALID_NUMBER", f"height_mm must be > 0, got {height!r}")
    if width is not None and width > 5000:
        raise CommandError("INVALID_NUMBER", f"width_mm must be ≤ 5000, got {width!r}")
    if height is not None and height > 5000:
        raise CommandError("INVALID_NUMBER", f"height_mm must be ≤ 5000, got {height!r}")

    padding = dict(canvas.get("padding_mm", {"top": 0, "right": 0, "bottom": 0, "left": 0}))
    if "padding_mm" in payload:
        new_pad = payload["padding_mm"]
        if not isinstance(new_pad, Mapping):
            raise CommandError("INVALID_STRUCTURE", "padding_mm must be an object")
        for key in ("top", "right", "bottom", "left"):
            if key in new_pad:
                val = new_pad[key]
                if isinstance(val, bool) or not _finite_number(val) or val < 0:
                    raise CommandError(
                        "INVALID_NUMBER",
                        f"padding_mm.{key} must be ≥ 0, got {val!r}",
                    )
                padding[key] = val

    if padding["left"] + padding["right"] >= width:
        raise CommandError(
            "INVALID_STRUCTURE",
            f"left+right padding ({padding['left'] + padding['right']}) must be < width ({width})",
        )
    if padding["top"] + padding["bottom"] >= height:
        raise CommandError(
            "INVALID_STRUCTURE",
            f"top+bottom padding ({padding['top'] + padding['bottom']}) must be < height ({height})",
        )

    doc["canvas"]["width_mm"] = width
    doc["canvas"]["height_mm"] = height
    doc["canvas"]["padding_mm"] = padding
    return doc


# Whitelist of fields that set_layer_properties may touch.
# id, asset_id, parent_id, order, stack_rank, pose, revision are NOT here.
_LAYER_PROPERTY_FIELDS = frozenset(
    {"name", "visible", "locked", "export_enabled", "extrusion_mm", "fit"}
)


def cmd_set_layer_properties(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Update a whitelist of layer fields.  Rejects everything else."""
    layer_id = payload.get("layer_id")
    layer = _require_layer(doc, layer_id)

    # Accept either a nested ``fields`` object or flat keys (the client sends
    # flat keys, e.g. {layer_id, name, visible}).  Both are whitelisted below.
    fields = payload.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        fields = {k: v for k, v in payload.items() if k != "layer_id"}
    if not fields:
        raise CommandError("INVALID_STRUCTURE", "no layer properties provided")

    unauthorized = set(fields.keys()) - _LAYER_PROPERTY_FIELDS
    if unauthorized:
        raise CommandError(
            "UNAUTHORIZED_FIELD",
            f"fields {sorted(unauthorized)} are not modifiable via set_layer_properties",
            layer_id=layer_id,
        )

    for key, value in fields.items():
        if key == "name":
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise CommandError("INVALID_STRUCTURE", "name must be 1–200 non-whitespace chars")
            layer["name"] = value.strip()
        elif key in ("visible", "locked", "export_enabled"):
            if not isinstance(value, bool):
                raise CommandError("INVALID_STRUCTURE", f"{key} must be a boolean")
            layer[key] = value
        elif key == "extrusion_mm":
            if value is not None:
                if isinstance(value, bool) or not _finite_number(value) or value <= 0:
                    raise CommandError(
                        "INVALID_NUMBER",
                        f"extrusion_mm must be null or > 0, got {value!r}",
                    )
            layer["extrusion_mm"] = value
        elif key == "fit":
            if not isinstance(value, Mapping):
                raise CommandError("INVALID_STRUCTURE", "fit must be an object")
            layer["fit"] = dict(value)

    return doc


def cmd_add_layers(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Add one or more layers as roots, each referencing an existing asset.

    Payload:
        ``assets``: list of asset dicts (from ``ImportedAsset.to_document_asset()``)
        ``layers``: list of layer specs, each with ``asset_id``, ``name``,
                    and optional ``pose``.  Each layer becomes a root
                    (``parent_id = null``).

    The assets are inserted into ``doc["assets"]`` and the layers into
    ``doc["layers"]`` in a single transaction.  Duplicate asset hashes are
    deduplicated: if an asset with the same ``source_sha256`` already exists,
    the new layer references the existing asset_id instead of creating a
    duplicate.
    """
    assets_in = payload.get("assets")
    layers_in = payload.get("layers")

    if not isinstance(assets_in, list) or not assets_in:
        raise CommandError("INVALID_STRUCTURE", "assets must be a non-empty list")
    if not isinstance(layers_in, list) or not layers_in:
        raise CommandError("INVALID_STRUCTURE", "layers must be a non-empty list")

    doc_assets = doc.setdefault("assets", {})
    doc_layers = doc.setdefault("layers", {})

    # Index existing assets by source_sha256 for dedup
    existing_by_hash: dict[str, str] = {}
    for aid, a in doc_assets.items():
        h = a.get("source_sha256")
        if h:
            existing_by_hash[h] = aid

    # Insert assets (dedup by hash)
    asset_id_map: dict[str, str] = {}  # incoming asset_id → final asset_id
    for a in assets_in:
        if not isinstance(a, Mapping):
            raise CommandError("INVALID_STRUCTURE", "each asset must be an object")
        aid = a.get("id")
        if not isinstance(aid, str) or not aid:
            raise CommandError("INVALID_STRUCTURE", "asset.id is required")
        h = a.get("source_sha256")
        if h and h in existing_by_hash:
            # Reuse existing asset
            asset_id_map[aid] = existing_by_hash[h]
        else:
            doc_assets[aid] = dict(a)
            asset_id_map[aid] = aid
            if h:
                existing_by_hash[h] = aid

    # Determine next order/stack_rank for roots
    root_orders = [
        l.get("order", 0) for l in doc_layers.values()
        if l.get("parent_id") is None
    ]
    next_order = max(root_orders) + 1 if root_orders else 0
    stack_ranks = [l.get("stack_rank", 0) for l in doc_layers.values()]
    next_rank = max(stack_ranks) + 1 if stack_ranks else 0

    # Insert layers
    for spec in layers_in:
        if not isinstance(spec, Mapping):
            raise CommandError("INVALID_STRUCTURE", "each layer spec must be an object")
        layer_id = spec.get("id")
        if not isinstance(layer_id, str) or not layer_id:
            raise CommandError("INVALID_STRUCTURE", "layer.id is required")
        if layer_id in doc_layers:
            raise CommandError("DUPLICATE_ID", f"layer {layer_id!r} already exists", layer_id=layer_id)

        incoming_aid = spec.get("asset_id")
        if not isinstance(incoming_aid, str) or incoming_aid not in asset_id_map:
            raise CommandError(
                "UNKNOWN_ASSET",
                f"layer {layer_id!r} references asset {incoming_aid!r} which was not provided",
                layer_id=layer_id,
            )
        final_aid = asset_id_map[incoming_aid]

        pose = spec.get("pose", {"tx": 0, "ty": 0, "scale": 1, "angle_deg": 0})
        _validate_pose(pose)

        doc_layers[layer_id] = {
            "id": layer_id,
            "asset_id": final_aid,
            "name": spec.get("name", layer_id),
            "parent_id": None,
            "order": spec.get("order", next_order),
            "stack_rank": spec.get("stack_rank", next_rank),
            "pose": {
                "tx": pose["tx"],
                "ty": pose["ty"],
                "scale": pose["scale"],
                "angle_deg": pose["angle_deg"],
            },
            "visible": True,
            "locked": False,
            "export_enabled": True,
            "extrusion_mm": None,
            "fit": {
                "target": "canvas",
                "hole_id": None,
                "padding_mm": 0,
                "avoid_siblings": True,
                "sibling_gap_mm": 2,
                "auto_scale_while_dragging": False,
                "allow_rotation": False,
                "rotation_half_range_deg": 20,
            },
        }
        next_order += 1
        next_rank += 1

    return doc


def _collect_subtree(doc: Mapping[str, Any], root_id: str) -> list[str]:
    """Return root_id + all its descendants (BFS)."""
    layers = doc["layers"]
    if root_id not in layers:
        raise CommandError("UNKNOWN_LAYER", f"layer {root_id!r} does not exist",
                           status=404, layer_id=root_id)
    out = [root_id]
    queue = [root_id]
    while queue:
        current = queue.pop(0)
        for lid, node in layers.items():
            if node.get("parent_id") == current and lid not in out:
                out.append(lid)
                queue.append(lid)
    return out


def _locked_in_subtree(doc: Mapping[str, Any], subtree: list[str]) -> list[str]:
    layers = doc["layers"]
    return [lid for lid in subtree if layers[lid].get("locked", False)]


def cmd_set_parent(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Reparent a layer, preserving its world position (spec §5.4).

    ``L_new = inverse(W_new_parent) · W_old``.  Descendants keep their
    local poses.  No hidden fit is executed (task 10 step 6).
    """
    layer_id = payload.get("layer_id")
    layer = _require_layer(doc, layer_id)
    new_parent_id = payload.get("new_parent_id")

    if new_parent_id is not None:
        if new_parent_id not in doc["layers"]:
            raise CommandError("UNKNOWN_LAYER",
                               f"new parent {new_parent_id!r} does not exist",
                               status=404, layer_id=new_parent_id)
        if new_parent_id == layer_id:
            raise CommandError("HIERARCHY_CYCLE", "a layer cannot be its own parent",
                               layer_id=layer_id)
        # Cycle check: walk the new parent's ancestors
        seen = set()
        current: str | None = new_parent_id
        while current is not None:
            if current in seen:
                raise CommandError("HIERARCHY_CYCLE", "cycle in existing tree",
                                   layer_id=current)
            seen.add(current)
            if current == layer_id:
                raise CommandError("HIERARCHY_CYCLE",
                                   f"{new_parent_id!r} is a descendant of {layer_id!r}",
                                   layer_id=layer_id)
            current = doc["layers"][current].get("parent_id")

    # Locks: the layer or any descendant locked → reject (task 10 step 4)
    subtree = _collect_subtree(doc, layer_id)
    locked = _locked_in_subtree(doc, subtree)
    if locked:
        raise CommandError("LOCKED", "subtree contains locked layers",
                           status=423, layer_id=layer_id,
                           details={"locked_ids": locked})

    # World position before
    old_world = world_pose(layer_id, doc["layers"])

    # New parent world (identity for root)
    if new_parent_id is not None:
        new_parent_world = world_pose(new_parent_id, doc["layers"])
    else:
        new_parent_world = Pose()

    new_local = reparent_pose(old_world, new_parent_world)

    # Sibling order
    order = payload.get("order")
    if order is None:
        siblings = [n for n in doc["layers"].values()
                    if n.get("parent_id") == new_parent_id and n["id"] != layer_id]
        order = max((s.get("order", 0) for s in siblings), default=-1) + 1
    if isinstance(order, bool) or not isinstance(order, int) or order < 0:
        raise CommandError("INVALID_STRUCTURE", f"order must be a non-negative int, got {order!r}")

    layer["parent_id"] = new_parent_id
    layer["order"] = order
    layer["pose"] = {"tx": new_local.tx, "ty": new_local.ty,
                     "scale": new_local.scale, "angle_deg": new_local.angle_deg}
    return doc


def cmd_reorder_siblings(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Reassign ``order`` 0..n-1 for an exact list of sibling ids."""
    parent_id = payload.get("parent_id")
    ids = payload.get("order")
    if not isinstance(ids, list) or not ids:
        raise CommandError("INVALID_STRUCTURE", "order must be a non-empty list of ids")
    if len(set(ids)) != len(ids):
        raise CommandError("INVALID_STRUCTURE", "order list contains duplicate ids")
    layers = doc["layers"]
    for lid in ids:
        if lid not in layers:
            raise CommandError("UNKNOWN_LAYER", f"layer {lid!r} does not exist",
                               status=404, layer_id=lid)
        if layers[lid].get("parent_id") != parent_id:
            raise CommandError("INVALID_STRUCTURE",
                               f"layer {lid!r} is not a sibling under parent {parent_id!r}",
                               layer_id=lid)
    # No extras: every sibling must be in the list
    actual = {lid for lid, n in layers.items() if n.get("parent_id") == parent_id}
    if actual != set(ids):
        missing = sorted(actual - set(ids))
        raise CommandError("INVALID_STRUCTURE",
                           f"siblings {missing} missing from order list")
    for i, lid in enumerate(ids):
        layers[lid]["order"] = i
    return doc


def cmd_set_stack_order(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Reassign ``stack_rank`` 0..n-1 for an exact permutation of all layers."""
    ids = payload.get("order")
    if not isinstance(ids, list) or not ids:
        raise CommandError("INVALID_STRUCTURE", "order must be a non-empty list of ids")
    layers = doc["layers"]
    if len(set(ids)) != len(ids):
        raise CommandError("INVALID_STRUCTURE", "order list contains duplicate ids")
    if set(ids) != set(layers.keys()):
        missing = sorted(set(layers.keys()) - set(ids))
        extra = sorted(set(ids) - set(layers.keys()))
        raise CommandError("INVALID_STRUCTURE",
                           f"order list must be a permutation of all layers "
                           f"(missing={missing}, unknown={extra})")
    for i, lid in enumerate(ids):
        layers[lid]["stack_rank"] = i
    return doc


def cmd_delete_subtree(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Delete a layer and all its descendants.  Assets remain."""
    layer_id = payload.get("layer_id")
    _require_layer(doc, layer_id)
    subtree = _collect_subtree(doc, layer_id)
    descendants = subtree[1:]
    confirm = payload.get("confirm_descendants")
    if confirm != len(descendants):
        raise CommandError(
            "CONFIRMATION_MISMATCH",
            f"confirm_descendants={confirm!r} but the subtree has {len(descendants)} descendants",
            layer_id=layer_id,
            details={"descendant_count": len(descendants), "descendants": descendants},
        )
    for lid in subtree:
        del doc["layers"][lid]
    return doc


def cmd_duplicate_subtree(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Duplicate a subtree with new ids; assets are shared (spec §6.2)."""
    layer_id = payload.get("layer_id")
    _require_layer(doc, layer_id)
    subtree = _collect_subtree(doc, layer_id)
    offset = payload.get("offset_mm") or {}
    dx = offset.get("dx", 0.0)
    dy = offset.get("dy", 0.0)

    layers = doc["layers"]
    max_rank = max((n.get("stack_rank", 0) for n in layers.values()), default=-1)

    id_map: dict[str, str] = {}
    n = 0
    for lid in subtree:
        new_id = f"{lid}_copy"
        while new_id in layers or new_id in id_map.values():
            n += 1
            new_id = f"{lid}_copy{n}"
        id_map[lid] = new_id

    for lid in subtree:
        node = layers[lid]
        new_node = dict(node)
        new_node["id"] = id_map[lid]
        old_parent = node.get("parent_id")
        new_node["parent_id"] = id_map[old_parent] if old_parent in id_map else old_parent
        pose = dict(node["pose"])
        if lid == layer_id:
            pose["tx"] += dx
            pose["ty"] += dy
        new_node["pose"] = pose
        new_node["stack_rank"] = max_rank + 1 + subtree.index(lid)
        layers[id_map[lid]] = new_node
    return doc


def cmd_apply_fit_result(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Confirm a fit proposal only if the revision is still current (task 14 step 3)."""
    layer_id = payload.get("layer_id")
    layer = _require_layer(doc, layer_id)
    if layer.get("locked", False):
        raise CommandError("LOCKED", f"layer {layer_id!r} is locked", status=423,
                           layer_id=layer_id, details={"locked_ids": [layer_id]})
    base_revision = payload.get("base_revision")
    if base_revision is None:
        raise CommandError("INVALID_STRUCTURE", "base_revision is required")
    if base_revision != doc.get("revision"):
        raise CommandError("REVISION_CONFLICT",
                           f"base_revision {base_revision} != document revision {doc.get('revision')}",
                           status=409)
    pose = payload.get("pose")
    _validate_pose(pose)
    layer["pose"] = {
        "tx": pose["tx"], "ty": pose["ty"],
        "scale": pose["scale"], "angle_deg": pose["angle_deg"],
    }
    return doc


def cmd_restore_snapshot(doc: dict, payload: Mapping[str, Any]) -> dict:
    """Undo/redo: restore a previously confirmed content snapshot (spec §13.4).

    The client keeps a local history of confirmed contents (max 100 actions)
    and sends the snapshot to restore.  The server replaces the document's
    ``assets`` and ``layers`` with the snapshot's, validates the result and
    creates a NEW revision — undo never decrements the revision counter.
    Document identity (``id``, ``name``, ``schema_version``, ``units``,
    ``canvas``, ``default_extrusion_mm``, ``stack_gap_mm``) is preserved.
    """
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, Mapping):
        raise CommandError(
            "INVALID_STRUCTURE",
            "snapshot must be an object with 'assets' and 'layers'",
        )
    if not isinstance(snapshot.get("assets"), Mapping):
        raise CommandError("INVALID_STRUCTURE", "snapshot.assets must be an object")
    if not isinstance(snapshot.get("layers"), Mapping):
        raise CommandError("INVALID_STRUCTURE", "snapshot.layers must be an object")
    doc["assets"] = copy.deepcopy(dict(snapshot["assets"]))
    doc["layers"] = copy.deepcopy(dict(snapshot["layers"]))
    return doc


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Any] = {
    "set_pose": cmd_set_pose,
    "set_canvas": cmd_set_canvas,
    "set_layer_properties": cmd_set_layer_properties,
    "add_layers": cmd_add_layers,
    "set_parent": cmd_set_parent,
    "reorder_siblings": cmd_reorder_siblings,
    "set_stack_order": cmd_set_stack_order,
    "delete_subtree": cmd_delete_subtree,
    "duplicate_subtree": cmd_duplicate_subtree,
    "apply_fit_result": cmd_apply_fit_result,
    "restore_snapshot": cmd_restore_snapshot,
}


def dispatch(command_type: str, doc: dict, payload: Mapping[str, Any]) -> dict:
    """Look up and run a command handler.

    Raises ``CommandError(UNKNOWN_COMMAND, 422)`` for types that are not
    implemented yet — never simulates success.
    """
    handler = HANDLERS.get(command_type)
    if handler is None:
        raise CommandError(
            "UNKNOWN_COMMAND",
            f"command type {command_type!r} is not implemented",
            status=422,
        )
    return handler(doc, payload)
