"""Task 03 — transactional store, idempotent commands, CAS (spec §8.2–8.4).

Covers:
- a confirmed action increments the revision exactly once,
- two requests with the same base_revision: only one commits, the other 409s,
- retrying the same command_id returns the stored result without re-applying,
- unknown command types are rejected (422), never simulated as success,
- the persisted file is never truncated (atomic write),
- orphan temp files are cleaned at startup,
- POST /documents → 201, GET /documents/{id} → 200, unknown id → 404,
- uniform error envelope with code/message/recoverable.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from silhouettes.editor.api import create_editor_app
from silhouettes.editor.commands import CommandError
from silhouettes.editor.document_store import DocumentStore, StoreError
from silhouettes.editor.models import validate_document


@pytest.fixture()
def store(tmp_path: Path) -> DocumentStore:
    return DocumentStore(tmp_path)


@pytest.fixture()
def client(store: DocumentStore):
    app = create_editor_app(store)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _make_layer_doc(store: DocumentStore) -> tuple[str, dict]:
    """Create a document with one asset and one layer so set_pose has a target."""
    doc = store.create_document("Test doc")
    doc_id = doc["id"]
    asset = {
        "id": "a1",
        "name": "A",
        "source_type": "svg",
        "source_uri": "assets/a1/source.svg",
        "canonical_svg_uri": "assets/a1/canonical.svg",
        "source_sha256": "0" * 64,
        "source_viewbox": [0, 0, 10, 10],
        "mm_per_source_unit": 1.0,
        "normalization_pose": {"tx": 0, "ty": 0, "scale": 1, "angle_deg": 0},
        "geometry_hash": "1" * 64,
        "trace_settings": {},
        "curve_tolerance_source": 0.01,
    }
    layer = {
        "id": "L1",
        "asset_id": "a1",
        "name": "Layer 1",
        "parent_id": None,
        "order": 0,
        "stack_rank": 0,
        "pose": {"tx": 10, "ty": 20, "scale": 1.0, "angle_deg": 0},
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
    # Direct mutation for test setup (bypasses CAS; store is ours in tests).
    with store._lock_for(doc_id):
        env = store._cache[doc_id]
        env["document"]["assets"]["a1"] = asset
        env["document"]["layers"]["L1"] = layer
        store._write_envelope(doc_id, env)
    return doc_id, store.load_document(doc_id)


# --- store-level tests -------------------------------------------------------


def test_confirmed_action_increments_revision_once(store: DocumentStore) -> None:
    doc_id, doc = _make_layer_doc(store)
    assert doc["revision"] == 0
    result = store.commit_command(doc_id, {
        "command_id": "cmd-1",
        "base_revision": 0,
        "type": "set_pose",
        "payload": {"layer_id": "L1", "pose": {"tx": 50, "ty": 60, "scale": 2.0, "angle_deg": 15}},
    })
    assert result["revision"] == 1
    assert result["replayed"] is False
    assert result["document"]["layers"]["L1"]["pose"]["tx"] == 50
    # persisted on disk
    on_disk = json.loads((store.documents_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
    assert on_disk["document"]["revision"] == 1


def test_same_base_revision_second_request_gets_conflict(store: DocumentStore) -> None:
    doc_id, _ = _make_layer_doc(store)
    store.commit_command(doc_id, {
        "command_id": "cmd-A",
        "base_revision": 0,
        "type": "set_pose",
        "payload": {"layer_id": "L1", "pose": {"tx": 1, "ty": 1, "scale": 1, "angle_deg": 0}},
    })
    with pytest.raises(StoreError) as exc:
        store.commit_command(doc_id, {
            "command_id": "cmd-B",
            "base_revision": 0,  # stale
            "type": "set_pose",
            "payload": {"layer_id": "L1", "pose": {"tx": 2, "ty": 2, "scale": 1, "angle_deg": 0}},
        })
    assert exc.value.code == "REVISION_CONFLICT"
    assert exc.value.status == 409
    # the layer was moved exactly once
    doc = store.load_document(doc_id)
    assert doc["layers"]["L1"]["pose"]["tx"] == 1
    assert doc["revision"] == 1


def test_replayed_command_id_returns_stored_result_without_reapplying(store: DocumentStore) -> None:
    doc_id, _ = _make_layer_doc(store)
    first = store.commit_command(doc_id, {
        "command_id": "cmd-X",
        "base_revision": 0,
        "type": "set_pose",
        "payload": {"layer_id": "L1", "pose": {"tx": 77, "ty": 88, "scale": 1, "angle_deg": 0}},
    })
    assert first["revision"] == 1
    # retry: same command_id, even with a now-stale base_revision
    second = store.commit_command(doc_id, {
        "command_id": "cmd-X",
        "base_revision": 0,
        "type": "set_pose",
        "payload": {"layer_id": "L1", "pose": {"tx": 77, "ty": 88, "scale": 1, "angle_deg": 0}},
    })
    assert second["replayed"] is True
    assert second["revision"] == 1  # not incremented again
    doc = store.load_document(doc_id)
    assert doc["layers"]["L1"]["pose"]["tx"] == 77  # moved once, not twice
    assert doc["revision"] == 1


def test_unknown_command_rejected_not_simulated(store: DocumentStore) -> None:
    doc_id, _ = _make_layer_doc(store)
    with pytest.raises(CommandError) as exc:
        store.commit_command(doc_id, {
            "command_id": "cmd-Y",
            "base_revision": 0,
            "type": "teleport_layer",  # not a real command type
            "payload": {"snapshot": {}},
        })
    assert exc.value.code == "UNKNOWN_COMMAND"
    assert exc.value.status == 422
    # document untouched
    doc = store.load_document(doc_id)
    assert doc["revision"] == 0


def test_restore_snapshot_restores_content_and_increments_revision(store: DocumentStore) -> None:
    doc_id, _ = _make_layer_doc(store)
    before = store.load_document(doc_id)
    snapshot = {"assets": before["assets"], "layers": before["layers"]}
    # move the layer, then undo back to the snapshot
    store.commit_command(doc_id, {
        "command_id": "cmd-move",
        "base_revision": 0,
        "type": "set_pose",
        "payload": {"layer_id": "L1", "pose": {"tx": 99, "ty": 99, "scale": 3.0, "angle_deg": 30}},
    })
    result = store.commit_command(doc_id, {
        "command_id": "cmd-undo",
        "base_revision": 1,
        "type": "restore_snapshot",
        "payload": {"snapshot": snapshot},
    })
    assert result["revision"] == 2  # undo creates a NEW revision, never decrements
    assert result["document"]["layers"]["L1"]["pose"] == before["layers"]["L1"]["pose"]
    # document identity preserved
    assert result["document"]["id"] == before["id"]
    assert result["document"]["canvas"] == before["canvas"]
    # persisted
    on_disk = json.loads((store.documents_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
    assert on_disk["document"]["revision"] == 2
    assert on_disk["document"]["layers"]["L1"]["pose"]["tx"] == 10


def test_restore_snapshot_invalid_payload_rejected(store: DocumentStore) -> None:
    doc_id, _ = _make_layer_doc(store)
    for bad in ({"snapshot": None}, {"snapshot": {"assets": {}}}, {"snapshot": {"layers": {}}}, {}):
        with pytest.raises(CommandError) as exc:
            store.commit_command(doc_id, {
                "command_id": f"cmd-bad-{bad}",
                "base_revision": 0,
                "type": "restore_snapshot",
                "payload": bad,
            })
        assert exc.value.code == "INVALID_STRUCTURE"
    doc = store.load_document(doc_id)
    assert doc["revision"] == 0


def test_set_layer_properties_whitelist(store: DocumentStore) -> None:
    doc_id, _ = _make_layer_doc(store)
    result = store.commit_command(doc_id, {
        "command_id": "cmd-P",
        "base_revision": 0,
        "type": "set_layer_properties",
        "payload": {"layer_id": "L1", "fields": {"visible": False, "extrusion_mm": 5}},
    })
    assert result["document"]["layers"]["L1"]["visible"] is False
    assert result["document"]["layers"]["L1"]["extrusion_mm"] == 5
    # unauthorized field rejected
    with pytest.raises(CommandError) as exc:
        store.commit_command(doc_id, {
            "command_id": "cmd-P2",
            "base_revision": 1,
            "type": "set_layer_properties",
            "payload": {"layer_id": "L1", "fields": {"revision": 99}},
        })
    assert exc.value.code == "UNAUTHORIZED_FIELD"


def test_set_canvas(store: DocumentStore) -> None:
    doc_id, _ = _make_layer_doc(store)
    result = store.commit_command(doc_id, {
        "command_id": "cmd-C",
        "base_revision": 0,
        "type": "set_canvas",
        "payload": {"width_mm": 300, "height_mm": 250},
    })
    assert result["document"]["canvas"]["width_mm"] == 300
    assert result["document"]["canvas"]["height_mm"] == 250
    # padding overflow rejected
    with pytest.raises(CommandError):
        store.commit_command(doc_id, {
            "command_id": "cmd-C2",
            "base_revision": 1,
            "type": "set_canvas",
            "payload": {"width_mm": 10, "height_mm": 10,
                        "padding_mm": {"left": 6, "right": 6, "top": 0, "bottom": 0}},
        })


def test_persisted_file_never_truncated(store: DocumentStore, tmp_path: Path) -> None:
    """Simulate a crash mid-write: a partial temp file must not corrupt the doc."""
    doc_id, _ = _make_layer_doc(store)
    good = (store.documents_dir / f"{doc_id}.json").read_text(encoding="utf-8")
    # leave an orphan temp file (as a crashed run would)
    orphan = store.documents_dir / f"{doc_id}.json.tmp.99999.deadbeef"
    orphan.write_text('{"envelope_version": 1, "document": {"truncat', encoding="utf-8")
    # document still fully readable
    doc = store.load_document(doc_id)
    validate_document(doc)
    # a new store instance on the same root cleans the orphan at startup
    DocumentStore(tmp_path)
    assert not orphan.exists()
    # and the real file is byte-identical to before
    assert (store.documents_dir / f"{doc_id}.json").read_text(encoding="utf-8") == good


def test_command_results_window_is_bounded(store: DocumentStore) -> None:
    doc_id, _ = _make_layer_doc(store)
    for i in range(130):
        store.commit_command(doc_id, {
            "command_id": f"cmd-{i}",
            "base_revision": i,
            "type": "set_pose",
            "payload": {"layer_id": "L1", "pose": {"tx": i, "ty": 0, "scale": 1, "angle_deg": 0}},
        })
    env = json.loads((store.documents_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
    assert len(env["command_results"]) <= 128
    assert "cmd-129" in env["command_results"]
    assert "cmd-0" not in env["command_results"]
    assert env["document"]["revision"] == 130


def test_concurrent_commits_do_not_lose_updates(store: DocumentStore) -> None:
    doc_id, _ = _make_layer_doc(store)
    errors: list = []

    def worker(n: int) -> None:
        try:
            # each worker retries until it wins the CAS race
            for _ in range(50):
                try:
                    doc = store.load_document(doc_id)
                    store.commit_command(doc_id, {
                        "command_id": f"w{n}-{id(object())}",
                        "base_revision": doc["revision"],
                        "type": "set_pose",
                        "payload": {"layer_id": "L1", "pose": {"tx": n, "ty": 0, "scale": 1, "angle_deg": 0}},
                    })
                    return
                except StoreError as exc:
                    if exc.code != "REVISION_CONFLICT":
                        raise
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    doc = store.load_document(doc_id)
    assert doc["revision"] == 8  # exactly one commit per worker
    validate_document(doc)


# --- API-level tests ---------------------------------------------------------


def test_api_create_get_and_command(client) -> None:
    r = client.post("/api/v2/documents", json={"name": "API doc"})
    assert r.status_code == 201
    doc = r.get_json()
    doc_id = doc["id"]
    assert doc["revision"] == 0

    r = client.get(f"/api/v2/documents/{doc_id}")
    assert r.status_code == 200
    assert r.get_json()["id"] == doc_id

    # command on a doc with no layers → 404 UNKNOWN_LAYER, uniform envelope
    r = client.post(f"/api/v2/documents/{doc_id}/commands", json={
        "command_id": "c1", "base_revision": 0, "type": "set_pose",
        "payload": {"layer_id": "nope", "pose": {"tx": 1, "ty": 1, "scale": 1, "angle_deg": 0}},
    })
    assert r.status_code == 404
    body = r.get_json()
    assert body["error"]["code"] == "UNKNOWN_LAYER"
    assert body["error"]["recoverable"] is True


def test_api_unknown_document_404(client) -> None:
    r = client.get("/api/v2/documents/ghost")
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "UNKNOWN_DOCUMENT"


def test_api_revision_conflict_409(client, store: DocumentStore) -> None:
    doc_id, _ = _make_layer_doc(store)
    payload = {"layer_id": "L1", "pose": {"tx": 1, "ty": 1, "scale": 1, "angle_deg": 0}}
    r1 = client.post(f"/api/v2/documents/{doc_id}/commands",
                     json={"command_id": "a", "base_revision": 0, "type": "set_pose", "payload": payload})
    assert r1.status_code == 200
    r2 = client.post(f"/api/v2/documents/{doc_id}/commands",
                     json={"command_id": "b", "base_revision": 0, "type": "set_pose", "payload": payload})
    assert r2.status_code == 409
    assert r2.get_json()["error"]["code"] == "REVISION_CONFLICT"
    assert r2.get_json()["revision"] == 1


def test_api_idempotent_retry_same_command_id(client, store: DocumentStore) -> None:
    doc_id, _ = _make_layer_doc(store)
    body = {"command_id": "same", "base_revision": 0, "type": "set_pose",
            "payload": {"layer_id": "L1", "pose": {"tx": 42, "ty": 0, "scale": 1, "angle_deg": 0}}}
    r1 = client.post(f"/api/v2/documents/{doc_id}/commands", json=body)
    assert r1.status_code == 200
    r2 = client.post(f"/api/v2/documents/{doc_id}/commands", json=body)
    assert r2.status_code == 200
    assert r2.get_json()["replayed"] is True
    assert r2.get_json()["revision"] == 1
    doc = client.get(f"/api/v2/documents/{doc_id}").get_json()
    assert doc["layers"]["L1"]["pose"]["tx"] == 42
    assert doc["revision"] == 1
