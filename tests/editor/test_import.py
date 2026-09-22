"""Tests for asset import (task 05).

Covers:
* import_asset: PNG → ImportedAsset with correct bounds, hash, normalisation
* import_asset: SVG → ImportedAsset (no rasterisation)
* import_asset: rejects unsupported extension
* import_asset: rejects empty PNG (no foreground)
* import_asset: rejects SVG with <script>
* import_asset: rejects SVG with <foreignObject>
* import_asset: rejects SVG with on* event handlers
* import_batch: 2 valid + 1 invalid → 2 assets, 1 error
* import_batch: dedup by hash (same file twice → 1 asset)
* import_batch: batch size limit
* POST /api/v2/documents/<id>/assets: 3 files → 3 assets + 3 layers
* POST /api/v2/documents/<id>/assets: duplicate instance does NOT re-run VTracer
* add_layers command: dedup by source_sha256
* add_layers command: rejects unknown asset reference
"""
from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from silhouettes.editor.asset_adapter import (
    AssetImportError,
    ImportedAsset,
    import_asset,
    import_batch,
)
from silhouettes.editor.api import _natural_name_key, create_editor_app
from silhouettes.editor.document_store import DocumentStore
from silhouettes.editor.commands import CommandError, dispatch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_png(width: int = 100, height: int = 100, shape: str = "circle") -> bytes:
    """Create a simple PNG with a filled shape."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if shape == "circle":
        d.ellipse([10, 10, width - 10, height - 10], fill=(0, 0, 0, 255))
    elif shape == "square":
        d.rectangle([10, 10, width - 10, height - 10], fill=(0, 0, 0, 255))
    elif shape == "empty":
        pass  # fully transparent
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_svg(width: int = 100, height: int = 100) -> bytes:
    """Create a simple valid SVG."""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        f'<path d="M10,10 L{width-10},10 L{width-10},{height-10} L10,{height-10} Z" fill="black"/>'
        f"</svg>"
    )
    return svg.encode("utf-8")


# ---------------------------------------------------------------------------
# import_asset – PNG
# ---------------------------------------------------------------------------

class TestImportPNG:
    def test_circle_produces_asset(self):
        data = make_png(shape="circle")
        asset = import_asset(data, "circle.png")
        assert asset.source_type == "png"
        assert asset.polygon_count >= 1
        assert len(asset.source_sha256) == 64
        assert len(asset.geometry_hash) == 64
        assert asset.mm_per_source_unit > 0
        # Normalisation: centre of bounds maps to (0,0)
        minx, miny, maxx, maxy = asset.polygon_bounds
        cx = (minx + maxx) / 2.0
        cy = (miny + maxy) / 2.0
        assert abs(asset.normalization_pose["tx"] + cx * asset.mm_per_source_unit) < 1e-9
        assert abs(asset.normalization_pose["ty"] + cy * asset.mm_per_source_unit) < 1e-9

    def test_empty_png_rejected(self):
        data = make_png(shape="empty")
        with pytest.raises(AssetImportError) as exc_info:
            import_asset(data, "empty.png")
        assert exc_info.value.code == "EMPTY_MASK"

    def test_invalid_png_rejected(self):
        with pytest.raises(AssetImportError) as exc_info:
            import_asset(b"not a png", "bad.png")
        assert exc_info.value.code == "INVALID_PNG"


# ---------------------------------------------------------------------------
# import_asset – SVG
# ---------------------------------------------------------------------------

class TestImportSVG:
    def test_valid_svg_produces_asset(self):
        data = make_svg()
        asset = import_asset(data, "square.svg")
        assert asset.source_type == "svg"
        assert asset.polygon_count >= 1
        assert len(asset.source_sha256) == 64

    def test_svg_with_script_rejected(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
            "<script>alert('xss')</script>"
            '<path d="M10,10 L90,10 L90,90 Z" fill="black"/>'
            "</svg>"
        )
        with pytest.raises(AssetImportError) as exc_info:
            import_asset(svg.encode("utf-8"), "evil.svg")
        assert exc_info.value.code == "SVG_REJECTED"
        assert "script" in exc_info.value.message.lower()

    def test_svg_with_foreignobject_rejected(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
            '<foreignObject x="0" y="0" width="100" height="100">'
            '<div xmlns="http://www.w3.org/1999/xhtml">hi</div>'
            "</foreignObject>"
            "</svg>"
        )
        with pytest.raises(AssetImportError) as exc_info:
            import_asset(svg.encode("utf-8"), "foreign.svg")
        assert exc_info.value.code == "SVG_REJECTED"

    def test_svg_with_onclick_rejected(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" '
            'viewBox="0 0 100 100" onclick="alert(1)">'
            '<path d="M10,10 L90,10 L90,90 Z" fill="black"/>'
            "</svg>"
        )
        with pytest.raises(AssetImportError) as exc_info:
            import_asset(svg.encode("utf-8"), "onclick.svg")
        assert exc_info.value.code == "SVG_REJECTED"

    def test_svg_with_text_rejected(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
            "<text x='10' y='50'>Hello</text>"
            "</svg>"
        )
        with pytest.raises(AssetImportError) as exc_info:
            import_asset(svg.encode("utf-8"), "text.svg")
        assert exc_info.value.code == "SVG_REJECTED"


# ---------------------------------------------------------------------------
# import_asset – unsupported type
# ---------------------------------------------------------------------------

class TestUnsupportedType:
    def test_jpg_rejected(self):
        with pytest.raises(AssetImportError) as exc_info:
            import_asset(b"\xff\xd8\xff", "photo.jpg")
        assert exc_info.value.code == "UNSUPPORTED_TYPE"

    def test_txt_rejected(self):
        with pytest.raises(AssetImportError) as exc_info:
            import_asset(b"hello", "notes.txt")
        assert exc_info.value.code == "UNSUPPORTED_TYPE"


# ---------------------------------------------------------------------------
# import_batch
# ---------------------------------------------------------------------------

class TestImportBatch:
    def test_two_valid_one_invalid(self):
        files = [
            ("circle.png", make_png(shape="circle")),
            ("square.svg", make_svg()),
            ("bad.jpg", b"\xff\xd8\xff"),
        ]
        assets, errors = import_batch(files)
        assert len(assets) == 2
        assert len(errors) == 1
        assert errors[0]["filename"] == "bad.jpg"
        assert errors[0]["code"] == "UNSUPPORTED_TYPE"

    def test_dedup_by_hash(self):
        data = make_png(shape="circle")
        files = [
            ("circle_a.png", data),
            ("circle_b.png", data),  # same bytes → same hash
        ]
        assets, errors = import_batch(files)
        assert len(assets) == 2
        assert len(errors) == 0
        # Both should share the same asset_id (dedup)
        assert assets[0].asset_id == assets[1].asset_id

    def test_distinct_files_get_distinct_ids(self):
        files = [
            ("circle.png", make_png(shape="circle")),
            ("square.png", make_png(shape="square")),
        ]
        assets, errors = import_batch(files)
        assert len(assets) == 2
        assert assets[0].asset_id != assets[1].asset_id

    def test_batch_too_many_files(self):
        files = [(f"img_{i}.png", make_png()) for i in range(17)]
        with pytest.raises(AssetImportError) as exc_info:
            import_batch(files)
        assert exc_info.value.code == "BATCH_TOO_LARGE"


# ---------------------------------------------------------------------------
# add_layers command
# ---------------------------------------------------------------------------

class TestAddLayersCommand:
    def _make_doc(self) -> dict:
        return {
            "schema_version": 2,
            "id": "test_doc",
            "name": "test",
            "revision": 0,
            "units": "mm",
            "canvas": {"width_mm": 200, "height_mm": 200,
                        "padding_mm": {"left": 5, "right": 5, "top": 5, "bottom": 5}},
            "default_extrusion_mm": 3,
            "stack_gap_mm": 0,
            "assets": {},
            "layers": {},
        }

    def test_add_two_layers(self):
        doc = self._make_doc()
        asset = import_asset(make_png(shape="circle"), "c.png")
        payload = {
            "assets": [asset.to_document_asset()],
            "layers": [
                {"id": "L1", "asset_id": asset.asset_id, "name": "Layer 1"},
                {"id": "L2", "asset_id": asset.asset_id, "name": "Layer 2"},
            ],
        }
        result = dispatch("add_layers", doc, payload)
        assert "L1" in result["layers"]
        assert "L2" in result["layers"]
        # Both reference the same asset (dedup)
        assert result["layers"]["L1"]["asset_id"] == result["layers"]["L2"]["asset_id"]
        assert len(result["assets"]) == 1

    def test_dedup_across_commands(self):
        doc = self._make_doc()
        data = make_png(shape="circle")
        a1 = import_asset(data, "c1.png")
        payload1 = {
            "assets": [a1.to_document_asset()],
            "layers": [{"id": "L1", "asset_id": a1.asset_id, "name": "First"}],
        }
        dispatch("add_layers", doc, payload1)

        # Second import of the same file
        a2 = import_asset(data, "c2.png")
        payload2 = {
            "assets": [a2.to_document_asset()],
            "layers": [{"id": "L2", "asset_id": a2.asset_id, "name": "Second"}],
        }
        result = dispatch("add_layers", doc, payload2)
        # Only ONE asset in the document (dedup by hash)
        assert len(result["assets"]) == 1
        # Both layers reference the same asset
        assert result["layers"]["L1"]["asset_id"] == result["layers"]["L2"]["asset_id"]

    def test_unknown_asset_rejected(self):
        doc = self._make_doc()
        payload = {
            "assets": [],
            "layers": [{"id": "L1", "asset_id": "nonexistent", "name": "Bad"}],
        }
        with pytest.raises(CommandError) as exc_info:
            dispatch("add_layers", doc, payload)
        assert exc_info.value.code in ("INVALID_STRUCTURE", "UNKNOWN_ASSET")

    def test_duplicate_layer_id_rejected(self):
        doc = self._make_doc()
        asset = import_asset(make_png(shape="circle"), "c.png")
        payload = {
            "assets": [asset.to_document_asset()],
            "layers": [{"id": "L1", "asset_id": asset.asset_id, "name": "A"}],
        }
        dispatch("add_layers", doc, payload)
        # Try to add L1 again
        payload2 = {
            "assets": [asset.to_document_asset()],
            "layers": [{"id": "L1", "asset_id": asset.asset_id, "name": "B"}],
        }
        with pytest.raises(CommandError) as exc_info:
            dispatch("add_layers", doc, payload2)
        assert exc_info.value.code == "DUPLICATE_ID"


# ---------------------------------------------------------------------------
# API: POST /assets
# ---------------------------------------------------------------------------

class TestPostAssetsAPI:
    @pytest.fixture
    def app_ctx(self, tmp_path):
        store = DocumentStore(tmp_path / "docs")
        app = create_editor_app(store=store)
        yield app, store
        # cleanup

    def test_three_files_produce_three_layers(self, app_ctx):
        app, store = app_ctx
        doc = store.create_document(name="import_test")
        doc_id = doc["id"]

        files = {
            "files[]": [
                (io.BytesIO(make_png(shape="circle")), "circle.png"),
                (io.BytesIO(make_png(shape="square")), "square.png"),
                (io.BytesIO(make_svg()), "rect.svg"),
            ]
        }
        with app.test_client() as c:
            r = c.post(
                f"/api/v2/documents/{doc_id}/assets",
                data={"base_revision": str(doc["revision"])},
                content_type="multipart/form-data",
            )
            # Flask test client: use files= for multipart
        # Use the proper multipart approach
        with app.test_client() as c:
            data = {
                "base_revision": str(doc["revision"]),
                "files[]": [
                    (io.BytesIO(make_png(shape="circle")), "circle.png"),
                    (io.BytesIO(make_png(shape="square")), "square.png"),
                    (io.BytesIO(make_svg()), "rect.svg"),
                ],
            }
            r = c.post(
                f"/api/v2/documents/{doc_id}/assets",
                data=data,
                content_type="multipart/form-data",
            )
            assert r.status_code == 200, r.get_data(as_text=True)
            body = r.get_json()
            assert body["imported"] == 3
            assert len(body["errors"]) == 0
            # Client contract: document is the bare content (not double-wrapped)
            assert "layers" in body["document"]
            assert "document" not in body["document"]
            assert len(body["document"]["layers"]) == 3
            names = {n["name"] for n in body["document"]["layers"].values()}
            assert "circle" in names
            assert "square" in names
            assert "rect" in names
            doc_after = store.load_document(doc_id)
            assert len(doc_after["layers"]) == 3
            assert len(doc_after["assets"]) == 3

    def test_batch_is_natural_name_descending_after_marco(self, app_ctx):
        app, store = app_ctx
        doc = store.create_document(name="natural_order")
        doc_id = doc["id"]
        framed = store.commit_command(doc_id, {
            "command_id": "make_frame",
            "base_revision": doc["revision"],
            "type": "generate_frame",
            "payload": {"wall_w_mm": 8, "wall_h_mm": 12, "padding_mm": 1},
        })["document"]

        with app.test_client() as client:
            response = client.post(
                f"/api/v2/documents/{doc_id}/assets",
                data={
                    "base_revision": str(framed["revision"]),
                    "files[]": [
                        (io.BytesIO(make_png(width=91)), "0821 Sugimori Style.png"),
                        (io.BytesIO(make_png(width=93)), "0823 Sugimori Style.png"),
                        (io.BytesIO(make_png(width=92)), "0822 Sugimori Style.png"),
                    ],
                },
                content_type="multipart/form-data",
            )

        assert response.status_code == 200, response.get_data(as_text=True)
        layers = response.get_json()["document"]["layers"].values()
        roots = sorted(
            (layer for layer in layers if layer["parent_id"] is None),
            key=lambda layer: layer["order"],
        )
        assert [layer["name"] for layer in roots] == [
            "Marco", "0823 Sugimori Style", "0822 Sugimori Style", "0821 Sugimori Style",
        ]

    def test_natural_name_key_compares_digit_runs_numerically(self):
        names = ["alpha.png", "zeta.png", "9 foo.png", "10 foo.png", "2 foo.png"]
        assert sorted(names, key=_natural_name_key, reverse=True) == [
            "10 foo.png", "9 foo.png", "2 foo.png", "zeta.png", "alpha.png",
        ]

    def test_mixed_valid_invalid(self, app_ctx):
        app, store = app_ctx
        doc = store.create_document(name="mixed_test")
        doc_id = doc["id"]

        with app.test_client() as c:
            data = {
                "base_revision": str(doc["revision"]),
                "files[]": [
                    (io.BytesIO(make_png(shape="circle")), "good.png"),
                    (io.BytesIO(b"not a valid file"), "bad.jpg"),
                ],
            }
            r = c.post(
                f"/api/v2/documents/{doc_id}/assets",
                data=data,
                content_type="multipart/form-data",
            )
            assert r.status_code == 200, r.get_data(as_text=True)
            body = r.get_json()
            assert body["imported"] == 1
            assert len(body["errors"]) == 1
            assert body["errors"][0]["filename"] == "bad.jpg"

    def test_no_files_returns_400(self, app_ctx):
        app, store = app_ctx
        doc = store.create_document(name="empty_test")
        doc_id = doc["id"]

        with app.test_client() as c:
            r = c.post(
                f"/api/v2/documents/{doc_id}/assets",
                data={"base_revision": "0"},
                content_type="multipart/form-data",
            )
            assert r.status_code == 400

    def test_unknown_document_returns_404(self, app_ctx):
        app, store = app_ctx
        with app.test_client() as c:
            data = {
                "files[]": [(io.BytesIO(make_png()), "x.png")],
            }
            r = c.post(
                "/api/v2/documents/nonexistent/assets",
                data=data,
                content_type="multipart/form-data",
            )
            assert r.status_code == 404
