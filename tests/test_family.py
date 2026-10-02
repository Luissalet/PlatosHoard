"""plato.export.done: finished export bundles are unpacked and announced for the model library."""
from __future__ import annotations

import base64
import io
import json
import time
import zipfile
from types import SimpleNamespace

import pytest
from shapely.geometry import box
from shapely.wkb import dumps as wkb_dumps

import plato_family
from silhouettes.editor.jobs import JobScheduler, JobState, TERMINAL_STATES

STL = b"solid x\nendsolid x\n"


def make_bundle(path, names=("normal_registered/a.stl", "inverse_registered/b.stl", "normal_registered/a.svg"), manifest=True):
    with zipfile.ZipFile(path, "w") as z:
        for name in names:
            z.writestr(name, STL if name.endswith(".stl") else "<svg/>")
        if manifest:
            z.writestr("manifest.json", json.dumps({"items": [
                {"layer_id": "L1", "family": "normal_registered", "files": [{"path": "normal_registered/a.stl"}, {"path": "normal_registered/a.svg"}]},
                {"layer_id": "L2", "family": "inverse_registered", "files": [{"path": "inverse_registered/b.stl"}]},
            ]}))
    return path


class Store:
    def load_document(self, doc_id):
        if doc_id == "gone":
            raise KeyError(doc_id)
        return {"name": "Escudo", "layers": {"L1": {"name": "Fondo"}, "L2": {"name": "Letras"}}}


@pytest.fixture
def sent(monkeypatch):
    events = []
    monkeypatch.setattr(plato_family, "emit", lambda kind, data: events.append((kind, data)) or True)
    return events


def job(tmp_path, zip_path, document_id="doc1", job_type="export"):
    return SimpleNamespace(id="job_abc123", job_type=job_type, document_id=document_id, download_path=str(zip_path) if zip_path else None)


def test_unpack_stl_only_takes_stl_files_and_reads_the_manifest(tmp_path):
    files = plato_family.unpack_stl(make_bundle(tmp_path / "x.zip"), tmp_path / "out")
    assert [f["stem"] for f in files] == ["a", "b"]
    assert files[0]["layer_id"] == "L1" and files[1]["family"] == "inverse_registered"
    assert all((tmp_path / "out" / f["file"]).read_bytes() == STL for f in files)
    assert not (tmp_path / "out" / "normal_registered" / "a.svg").exists()


def test_unpack_without_manifest_and_unsafe_names(tmp_path):
    path = tmp_path / "x.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("piece.stl", STL)
        z.writestr("../evil.stl", STL)
        z.writestr("/abs.stl", STL)
        z.writestr("a/b/c/d.stl", STL)
        z.writestr(".hidden/x.stl", STL)
    files = plato_family.unpack_stl(path, tmp_path / "out")
    assert [f["stem"] for f in files] == ["piece"]
    assert not (tmp_path / "evil.stl").exists() and not (tmp_path / "out" / "abs.stl").exists()


def test_announce_emits_one_event_per_stl_with_ref_and_title(tmp_path, sent):
    slept = []
    payloads = plato_family.announce(job(tmp_path, make_bundle(tmp_path / "x.zip")), Store(), tmp_path / "exports", sleep=slept.append)
    assert [kind for kind, _ in sent] == ["plato.export.done"] * 2
    first, second = (data for _, data in sent)
    assert first["ref"] == second["ref"] == "hoard://plato/export/job_abc123"
    assert first["title"] == "Escudo - Fondo (normal_registered)" and second["title"] == "Escudo - Letras (inverse_registered)"
    assert first["path"].replace("\\", "/").endswith("job_abc123/normal_registered/a.stl") and (tmp_path / "exports" / "job_abc123" / "normal_registered" / "a.stl").is_file()
    assert first["format"] == "stl" and first["layer_id"] == "L1" and payloads == [d for _, d in sent]
    assert slept == [plato_family.EVENT_SPACING_S]  # the rule cooldown: the second event waits


def test_announce_survives_a_missing_document_and_ignores_other_jobs(tmp_path, sent):
    plato_family.announce(job(tmp_path, make_bundle(tmp_path / "x.zip"), document_id="gone"), Store(), tmp_path / "e", sleep=lambda s: None)
    assert sent[0][1]["title"] == "a (normal_registered)"
    sent.clear()
    assert plato_family.announce(job(tmp_path, None), Store(), tmp_path / "e") == []
    assert plato_family.announce(job(tmp_path, make_bundle(tmp_path / "y.zip"), job_type="synthetic"), Store(), tmp_path / "e") == []
    svg_only = make_bundle(tmp_path / "z.zip", names=("normal_registered/a.svg",), manifest=False)
    assert plato_family.announce(job(tmp_path, svg_only), Store(), tmp_path / "e") == [] and sent == []
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    assert plato_family.announce(job(tmp_path, bad), Store(), tmp_path / "e") == []


def test_configure_writes_the_token_once_and_names_the_app(tmp_path, monkeypatch):
    assert plato_family.family is not None
    token_file = plato_family.configure(tmp_path)
    token = (tmp_path / "mcp-token").read_text()
    assert token_file == str(tmp_path / "mcp-token") and len(token) >= 32
    plato_family.configure(tmp_path)
    assert (tmp_path / "mcp-token").read_text() == token
    assert plato_family.family.health_block()["app"] == "platos"


def test_emit_is_a_quiet_noop_without_the_hub(tmp_path, monkeypatch):
    monkeypatch.setenv("HOARD_EVENTS", "0")
    plato_family.configure(tmp_path)
    assert plato_family.emit("plato.export.done", {"path": "x"}) in (True, False)  # never raises


def test_scheduler_runs_the_hook_once_for_a_finished_export(tmp_path):
    seen = []
    sched = JobScheduler(output_dir=tmp_path)
    sched.add_done_hook(seen.append)
    sched.add_done_hook(lambda rec: 1 / 0)  # a broken observer never breaks the scheduler
    try:
        plan = [{"layer_id": "L1", "family": "normal_registered", "stem": "layer_one", "extrusion_mm": 3.0,
                 "geom_hex": base64.b64encode(wkb_dumps(box(10, 10, 60, 40))).decode("ascii"), "pose": None}]
        rec = sched.submit("export", {"task": "export", "plan": plan, "width_mm": 100.0, "height_mm": 60.0, "formats": ["stl", "svg"],
                                      "output_path": str(tmp_path / "bundle.zip")})
        deadline = time.time() + 60
        while time.time() < deadline and rec.state not in TERMINAL_STATES:
            sched.poll()
            time.sleep(0.2)
        assert rec.state == JobState.COMPLETED, rec.error
        sched.poll()
        sched.get(rec.id)
        assert seen == [rec]
        files = plato_family.unpack_stl(tmp_path / "bundle.zip", tmp_path / "out")
        assert [f["stem"] for f in files] == ["layer_one"] and files[0]["layer_id"] == "L1"
        assert (tmp_path / "out" / files[0]["file"]).stat().st_size > 100
    finally:
        sched.shutdown()


def test_failed_jobs_do_not_call_the_hook(tmp_path):
    seen = []
    sched = JobScheduler(output_dir=tmp_path)
    sched.add_done_hook(seen.append)
    try:
        rec = sched.submit("export", {"task": "export", "plan": [], "width_mm": 1.0, "height_mm": 1.0, "output_path": str(tmp_path / "e.zip")})
        deadline = time.time() + 60
        while time.time() < deadline and rec.state not in TERMINAL_STATES:
            sched.poll()
            time.sleep(0.2)
        assert rec.state == JobState.FAILED and seen == []
    finally:
        sched.shutdown()
