"""Smoke: sync fit endpoint returns a pose."""
from io import BytesIO
from pathlib import Path
import tempfile

import pytest
from PIL import Image, ImageDraw

from silhouettes.editor.api import create_editor_app
from silhouettes.editor.document_store import DocumentStore


def _png():
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([10, 10, 90, 90], fill=(0, 0, 0, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_sync_fit_canvas():
    tmp = Path(tempfile.mkdtemp())
    store = DocumentStore(tmp / "docs")
    app = create_editor_app(store=store)
    with app.test_client() as c:
        doc = c.post("/api/v2/documents", json={"name": "fit"}).get_json()
        r = c.post(
            f"/api/v2/documents/{doc['id']}/assets",
            data={"base_revision": "0", "files[]": (BytesIO(_png()), "circle.png")},
            content_type="multipart/form-data",
        )
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        lid = next(iter(body["document"]["layers"]))
        fr = c.post(
            f"/api/v2/documents/{doc['id']}/fit",
            json={"layer_id": lid, "target": "canvas", "mode": "best", "padding_mm": 2},
        )
        assert fr.status_code == 200, fr.get_data(as_text=True)
        out = fr.get_json()
        assert "pose" in out
        assert out["pose"]["scale"] > 0
        assert out["status"] == "completed"


def test_at_position_regrows_after_returning_from_parent_edge():
    """A reduced edge preview must not cap the next centre fit's scale."""
    tmp = Path(tempfile.mkdtemp())
    store = DocumentStore(tmp / "docs")
    app = create_editor_app(store=store)
    with app.test_client() as c:
        doc = c.post("/api/v2/documents", json={"name": "regrow"}).get_json()
        doc_id = doc["id"]

        def upload(name, revision):
            response = c.post(
                f"/api/v2/documents/{doc_id}/assets",
                data={"base_revision": str(revision),
                      "files[]": (BytesIO(_png()), name)},
                content_type="multipart/form-data",
            )
            assert response.status_code == 200, response.get_data(as_text=True)
            return response.get_json()["document"]

        doc = upload("parent.png", 0)
        parent_id = next(iter(doc["layers"]))
        doc = upload("child.png", doc["revision"])
        child_id = next(lid for lid in doc["layers"] if lid != parent_id)
        response = c.post(f"/api/v2/documents/{doc_id}/commands", json={
            "command_id": "nest-child",
            "base_revision": doc["revision"],
            "type": "set_parent",
            "payload": {"layer_id": child_id, "new_parent_id": parent_id},
        })
        assert response.status_code == 200, response.get_data(as_text=True)
        doc = response.get_json()["document"]

        parent_pose = doc["layers"][parent_id]["pose"]
        asset = doc["assets"][doc["layers"][parent_id]["asset_id"]]
        radius_world = (asset["local_bounds"][2] - asset["local_bounds"][0]) \
            * parent_pose["scale"] / 2
        edge_local_x = radius_world * 0.65 / parent_pose["scale"]

        def fit_at(tx, ty, proposed_scale):
            result = c.post(f"/api/v2/documents/{doc_id}/fit", json={
                "layer_id": child_id,
                "target": "parent_shape",
                "mode": "at_position",
                "quality": "preview",
                "padding_mm": 0,
                "max_evaluations": 512,
                "pose": {"tx": tx, "ty": ty, "scale": proposed_scale,
                         "angle_deg": 0},
            })
            assert result.status_code == 200, result.get_data(as_text=True)
            return result.get_json()

        edge = fit_at(edge_local_x, 0, 0.01)
        centre = fit_at(0, 0, edge["pose_local"]["scale"])

        assert edge["pose_local"]["tx"] == pytest.approx(edge_local_x)
        assert edge["pose_local"]["ty"] == pytest.approx(0)
        assert centre["pose_local"]["tx"] == pytest.approx(0)
        assert centre["pose_local"]["ty"] == pytest.approx(0)
        assert centre["pose_world"]["scale"] > edge["pose_world"]["scale"] * 1.2
