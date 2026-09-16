"""Smoke: sync fit endpoint returns a pose."""
from io import BytesIO
from pathlib import Path
import tempfile

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
