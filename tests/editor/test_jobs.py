"""Tests for the async job scheduler (task 04).

Covers:
* JobRecord states and to_dict()
* worker_main cooperative cancellation (in-process, no spawn needed)
* JobScheduler.submit → completed (synthetic worker, spawn on Windows)
* JobScheduler.cancel stops a running job
* Cancelled job never modifies document revision
* GET /api/v2/jobs/<id> returns job state
* POST /api/v2/jobs/<id>/cancel returns 200 while running, 409 after terminal
* GET /api/v2/jobs/<id>/download returns 404 for non-completed job
* Shutdown marks in-flight jobs as failed (not completed)
* No double worker: pool has exactly 1 process
"""
from __future__ import annotations

import multiprocessing as mp
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from silhouettes.editor.jobs import (
    JobRecord,
    JobScheduler,
    JobState,
    TERMINAL_STATES,
    worker_main,
)
from silhouettes.editor.api import create_editor_app
from silhouettes.editor.document_store import DocumentStore


# ---------------------------------------------------------------------------
# worker_main – in-process tests (no spawn)
# ---------------------------------------------------------------------------

class TestWorkerMain:
    def test_completes_all_iterations(self):
        import silhouettes.editor.jobs as jobs_mod
        old = jobs_mod._worker_cancel_event
        jobs_mod._worker_cancel_event = None
        try:
            result = worker_main({"task": "synthetic", "iterations": 5})
        finally:
            jobs_mod._worker_cancel_event = old
        assert result["cancelled"] is False
        assert result["completed_iterations"] == 5
        assert result["task"] == "synthetic"

    def test_cancellation_stops_early(self):
        import silhouettes.editor.jobs as jobs_mod
        ctx = mp.get_context("spawn")
        event = ctx.Event()
        event.set()  # pre-set: worker should stop immediately
        old = jobs_mod._worker_cancel_event
        jobs_mod._worker_cancel_event = event
        try:
            result = worker_main({"task": "synthetic", "iterations": 100})
        finally:
            jobs_mod._worker_cancel_event = old
        assert result["cancelled"] is True
        assert result["completed_iterations"] == 0

    def test_no_cancel_event_completes(self):
        import silhouettes.editor.jobs as jobs_mod
        old = jobs_mod._worker_cancel_event
        jobs_mod._worker_cancel_event = None
        try:
            result = worker_main({"task": "synthetic", "iterations": 3})
        finally:
            jobs_mod._worker_cancel_event = old
        assert result["cancelled"] is False
        assert result["completed_iterations"] == 3

    def test_default_iterations(self):
        import silhouettes.editor.jobs as jobs_mod
        old = jobs_mod._worker_cancel_event
        jobs_mod._worker_cancel_event = None
        try:
            result = worker_main({"task": "synthetic"})
        finally:
            jobs_mod._worker_cancel_event = old
        assert result["completed_iterations"] == 10

    def test_export_writes_zip_and_returns_download_path(self, tmp_path):
        import base64
        from shapely.geometry import box
        from shapely.wkb import dumps as wkb_dumps

        geom = box(10, 10, 50, 50)
        out = tmp_path / "export_test.zip"
        payload = {
            "task": "export",
            "output_path": str(out),
            "width_mm": 200,
            "height_mm": 200,
            "formats": ["svg"],
            "png_width_px": 100,
            "project_revision": 1,
            "plan": [{
                "layer_id": "L1",
                "family": "normal_registered",
                "stem": "normal_registered/00_Square_L1",
                "extrusion_mm": 3.0,
                "geom_hex": base64.b64encode(wkb_dumps(geom)).decode("ascii"),
                "pose": {"tx": 30, "ty": 30, "scale": 1, "angle_deg": 0},
            }],
        }
        result = worker_main(payload)
        assert result.get("cancelled") is False
        assert result.get("error") is None
        assert result["download_path"] == str(out)
        assert out.is_file()
        assert out.stat().st_size > 50


# ---------------------------------------------------------------------------
# JobRecord
# ---------------------------------------------------------------------------

class TestJobRecord:
    def test_to_dict_contains_required_fields(self):
        rec = JobRecord(id="job_test", job_type="fit")
        d = rec.to_dict()
        for key in ("id", "type", "state", "phase", "progress", "created_at"):
            assert key in d, f"missing {key}"
        assert d["state"] == "queued"
        assert d["id"] == "job_test"

    def test_terminal_states_set(self):
        assert JobState.COMPLETED in TERMINAL_STATES
        assert JobState.FAILED in TERMINAL_STATES
        assert JobState.CANCELLED in TERMINAL_STATES
        assert JobState.QUEUED not in TERMINAL_STATES
        assert JobState.RUNNING not in TERMINAL_STATES


# ---------------------------------------------------------------------------
# JobScheduler – spawn-based integration
# ---------------------------------------------------------------------------

class TestJobScheduler:
    def test_submit_and_complete(self, tmp_path):
        sched = JobScheduler(output_dir=tmp_path)
        try:
            rec = sched.submit("synthetic", {"task": "synthetic", "iterations": 3})
            assert rec.state == JobState.RUNNING
            assert rec.phase == "running"

            # Poll until terminal (max 10 s)
            deadline = time.time() + 10
            while time.time() < deadline:
                rec = sched.get(rec.id)
                if rec.state in TERMINAL_STATES:
                    break
                time.sleep(0.1)

            assert rec.state == JobState.COMPLETED, f"state={rec.state} error={rec.error}"
            assert rec.result is not None
            assert rec.result["completed_iterations"] == 3
            assert rec.progress == 1.0
        finally:
            sched.shutdown()

    def test_cancel_running_job(self, tmp_path):
        sched = JobScheduler(output_dir=tmp_path)
        try:
            # Use many iterations so the job is still running when we cancel
            rec = sched.submit("synthetic", {"task": "synthetic", "iterations": 100000})
            time.sleep(0.3)  # let it start

            ok = sched.cancel(rec.id)
            assert ok is True

            # Poll until terminal
            deadline = time.time() + 10
            while time.time() < deadline:
                rec = sched.get(rec.id)
                if rec.state in TERMINAL_STATES:
                    break
                time.sleep(0.1)

            assert rec.state == JobState.CANCELLED, f"state={rec.state} error={rec.error}"
            assert rec.result is not None
            assert rec.result["cancelled"] is True
        finally:
            sched.shutdown()

    def test_cancel_terminal_job_returns_false(self, tmp_path):
        sched = JobScheduler(output_dir=tmp_path)
        try:
            rec = sched.submit("synthetic", {"task": "synthetic", "iterations": 1})
            deadline = time.time() + 10
            while time.time() < deadline:
                rec = sched.get(rec.id)
                if rec.state in TERMINAL_STATES:
                    break
                time.sleep(0.1)
            assert rec.state == JobState.COMPLETED

            # Cancelling a completed job must return False
            assert sched.cancel(rec.id) is False
        finally:
            sched.shutdown()

    def test_cancelling_queued_job_does_not_cancel_running_job(self, tmp_path):
        sched = JobScheduler(output_dir=tmp_path)
        try:
            running = sched.submit("synthetic", {"task": "synthetic", "iterations": 200_000_000})
            queued = sched.submit("synthetic", {"task": "synthetic", "iterations": 1})
            assert running.state == JobState.RUNNING
            assert queued.state == JobState.QUEUED

            assert sched.cancel(queued.id) is True
            assert sched.get(queued.id).state == JobState.CANCELLED
            assert sched._cancel_event.is_set() is False
            assert sched.get(running.id).state == JobState.RUNNING
        finally:
            sched.shutdown()

    def test_next_queued_job_starts_after_active_job_finishes(self, tmp_path):
        sched = JobScheduler(output_dir=tmp_path)
        try:
            first = sched.submit("synthetic", {"task": "synthetic", "iterations": 1})
            second = sched.submit("synthetic", {"task": "synthetic", "iterations": 1})
            assert first.state == JobState.RUNNING
            assert second.state == JobState.QUEUED
            deadline = time.time() + 10
            while time.time() < deadline:
                sched.get(second.id)
                if second.state in TERMINAL_STATES:
                    break
                time.sleep(0.05)
            assert first.state == JobState.COMPLETED
            assert second.state == JobState.COMPLETED
            assert second.started_at is not None
            assert second.started_at >= first.finished_at
        finally:
            sched.shutdown()

    def test_concurrent_submissions_dispatch_only_one_worker_job(self, tmp_path):
        sched = JobScheduler(output_dir=tmp_path)
        try:
            with ThreadPoolExecutor(max_workers=8) as threads:
                records = list(threads.map(
                    lambda _: sched.submit(
                        "synthetic", {"task": "synthetic", "iterations": 200_000_000}
                    ),
                    range(8),
                ))
            running = [rec for rec in records if rec.state == JobState.RUNNING]
            queued = [rec for rec in records if rec.state == JobState.QUEUED]
            assert len(running) == 1
            assert len(queued) == 7
            assert sched._active_job_id == running[0].id
            assert len(sched._futures) == 1
        finally:
            sched.shutdown()

    def test_cancel_unknown_job_returns_false(self, tmp_path):
        sched = JobScheduler(output_dir=tmp_path)
        try:
            assert sched.cancel("job_nonexistent") is False
        finally:
            sched.shutdown()

    def test_shutdown_marks_inflight_as_failed(self, tmp_path):
        sched = JobScheduler(output_dir=tmp_path)
        rec = sched.submit("synthetic", {"task": "synthetic", "iterations": 100000})
        time.sleep(0.2)
        sched.shutdown()
        rec = sched.get(rec.id)
        assert rec.state == JobState.FAILED
        assert rec.error is not None
        assert rec.error["code"] == "SERVER_RESTART"

    def test_download_path_none_for_incomplete(self, tmp_path):
        sched = JobScheduler(output_dir=tmp_path)
        try:
            rec = sched.submit("synthetic", {"task": "synthetic", "iterations": 1})
            # Not yet completed
            assert sched.download_path(rec.id) is None
        finally:
            sched.shutdown()

    def test_download_path_rejects_traversal(self, tmp_path):
        sched = JobScheduler(output_dir=tmp_path)
        try:
            rec = JobRecord(id="job_evil", job_type="test", state=JobState.COMPLETED,
                            download_path=str(tmp_path.parent / "secret.txt"))
            sched._jobs[rec.id] = rec
            assert sched.download_path(rec.id) is None
        finally:
            sched.shutdown()

    def test_single_worker_pool(self, tmp_path):
        """Pool must have exactly 1 process (no double worker)."""
        sched = JobScheduler(output_dir=tmp_path)
        try:
            sched._ensure_pool()
            assert sched._pool is not None
            # Pool._processes is a list of worker Popen objects
            assert len(sched._pool._pool) == 1, f"expected 1 worker, got {len(sched._pool._pool)}"
        finally:
            sched.shutdown()


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

class TestJobAPI:
    @pytest.fixture
    def app_ctx(self, tmp_path):
        store = DocumentStore(tmp_path / "docs")
        sched = JobScheduler(output_dir=tmp_path / "jobs")
        app = create_editor_app(store=store, scheduler=sched)
        yield app, store, sched
        sched.shutdown()

    def test_get_job_unknown_returns_404(self, app_ctx):
        app, _, _ = app_ctx
        with app.test_client() as c:
            r = c.get("/api/v2/jobs/job_nonexistent")
            assert r.status_code == 404
            assert r.get_json()["error"]["code"] == "NOT_FOUND"

    def test_export_rejects_malformed_png_width(self, app_ctx):
        app, store, _ = app_ctx
        doc = store.create_document(name="bad resolution")
        with app.test_client() as c:
            r = c.post(f"/api/v2/documents/{doc['id']}/exports", json={
                "png_width_px": "not-a-number",
            })
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "INVALID_RESOLUTION"

    def test_get_job_returns_state(self, app_ctx):
        app, _, sched = app_ctx
        rec = sched.submit("synthetic", {"task": "synthetic", "iterations": 1})
        with app.test_client() as c:
            r = c.get(f"/api/v2/jobs/{rec.id}")
            assert r.status_code == 200
            body = r.get_json()
            assert body["id"] == rec.id
            assert body["state"] in ("queued", "running", "completed")

    def test_cancel_unknown_job_returns_404(self, app_ctx):
        app, _, _ = app_ctx
        with app.test_client() as c:
            r = c.post("/api/v2/jobs/job_nonexistent/cancel")
            assert r.status_code == 404

    def test_cancel_completed_job_returns_409(self, app_ctx):
        app, _, sched = app_ctx
        rec = sched.submit("synthetic", {"task": "synthetic", "iterations": 1})
        deadline = time.time() + 10
        while time.time() < deadline:
            rec = sched.get(rec.id)
            if rec.state in TERMINAL_STATES:
                break
            time.sleep(0.1)
        assert rec.state == JobState.COMPLETED

        with app.test_client() as c:
            r = c.post(f"/api/v2/jobs/{rec.id}/cancel")
            assert r.status_code == 409
            assert r.get_json()["error"]["code"] == "ALREADY_TERMINAL"

    def test_download_incomplete_returns_409(self, app_ctx):
        app, _, sched = app_ctx
        rec = sched.submit("synthetic", {"task": "synthetic", "iterations": 100000})
        time.sleep(0.2)
        with app.test_client() as c:
            r = c.get(f"/api/v2/jobs/{rec.id}/download")
            # Job is still running → 409 NOT_COMPLETED
            assert r.status_code == 409

    def test_cancelled_job_does_not_change_revision(self, app_ctx):
        """A cancelled job must never modify the document revision."""
        app, store, sched = app_ctx
        doc = store.create_document(name="rev_test")
        rev_before = doc["revision"]

        rec = sched.submit(
            "fit",
            {"task": "synthetic", "iterations": 100000},
            document_id=doc["id"],
            source_revision=rev_before,
        )
        time.sleep(0.3)
        sched.cancel(rec.id)

        deadline = time.time() + 10
        while time.time() < deadline:
            rec = sched.get(rec.id)
            if rec.state in TERMINAL_STATES:
                break
            time.sleep(0.1)

        assert rec.state == JobState.CANCELLED
        # Revision must be unchanged
        doc_after = store.load_document(doc["id"])
        assert doc_after["revision"] == rev_before
