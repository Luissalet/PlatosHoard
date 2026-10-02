"""What Plato's Hoard takes from Hoard Link: the request guard, the atomic document write and the token file."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import plato_family
from app import app
from silhouettes.editor.document_store import DocumentStore


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_a_foreign_host_name_is_refused(client):
    # a page that rebinds its DNS name to 127.0.0.1 sends its own name in Host
    refused = client.get("/editor", headers={"Host": "evil.example"})
    assert refused.status_code == 403 and refused.get_json()["error"] == "Only local access is allowed."
    assert client.get("/editor", headers={"Host": "127.0.0.1:5000"}).status_code == 200
    assert client.get("/editor", headers={"Host": "localhost:5000"}).status_code == 200


def test_cross_origin_and_cross_site_requests_are_refused(client):
    local = {"Host": "127.0.0.1:5000"}
    assert client.post("/api/process", headers={**local, "Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/process", headers={**local, "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors"}).status_code == 403
    # same-origin traffic reaches the route (no file uploaded: the route's own 400)
    assert client.post("/api/process", headers={**local, "Origin": "http://127.0.0.1:5000"}).status_code == 400


def test_an_allowed_host_name_is_opened_by_the_environment(client, monkeypatch):
    monkeypatch.setenv("PLATO_ALLOWED_HOSTS", "pc2.example")
    assert client.get("/editor", headers={"Host": "pc2.example"}).status_code == 200
    assert client.get("/editor", headers={"Host": "pc3.example"}).status_code == 403


def test_the_app_listens_on_loopback_by_default():
    import app as module

    assert module.HOST == "127.0.0.1" and module.PORT == 5000


def test_the_guard_refuses_everything_when_the_vendored_copy_is_missing(monkeypatch):
    monkeypatch.setattr(plato_family, "guard", None)
    assert plato_family.check_request("GET", {"host": "127.0.0.1:5000"}, 5000)[0] == 403


def test_documents_are_written_atomically(tmp_path, monkeypatch):
    store = DocumentStore(tmp_path)
    created = store.create_document("Atómico")
    path = store.documents_dir / f"{created['id']}.json"
    before = path.read_text(encoding="utf-8")
    assert before.endswith("\n") and json.loads(before)["envelope_version"] == 1
    # a replace that fails leaves the previous file and no temp file behind
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        store._write_envelope(created["id"], {**json.loads(before), "marker": True})
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == before
    assert [p.name for p in store.documents_dir.iterdir()] == [path.name]


def test_orphans_of_both_temp_name_styles_are_cleaned_at_startup(tmp_path):
    store = DocumentStore(tmp_path)
    old_style = store.documents_dir / "abc.json.tmp.99999.deadbeef"
    new_style = store.documents_dir / "abc.json.99999.140000.deadbeef.tmp"
    keep = store.documents_dir / "keep.json"
    for p in (old_style, new_style, keep):
        p.write_text("{}", encoding="utf-8")
    DocumentStore(tmp_path)
    assert not old_style.exists() and not new_style.exists() and keep.exists()


def test_the_token_file_is_created_once_by_the_shared_helper(tmp_path):
    plato_family.configure(tmp_path)
    token = (tmp_path / "mcp-token").read_text(encoding="utf-8").strip()
    assert len(token) >= 32
    plato_family.configure(tmp_path)
    assert (tmp_path / "mcp-token").read_text(encoding="utf-8").strip() == token
