"""Pixel contours stay exact through import, reuse, persistence and extrusion."""
import io

import numpy as np
import pytest
from PIL import Image
from shapely.geometry import box
from shapely.ops import unary_union

from silhouettes.editor.api import create_editor_app
from silhouettes.editor.asset_adapter import import_asset
from silhouettes.editor.commands import dispatch
from silhouettes.editor.document_store import DocumentStore
from silhouettes.editor.models import new_document
from silhouettes.mesh import extrude_polygons
from silhouettes.trace import PRESETS, trace_mask
from silhouettes.validation import top_cap_geometry, validate_mesh
from silhouettes.vector import parse_vector, vector_to_polygons


def pixel_mask(kind):
    mask = np.zeros((16, 16), dtype=np.uint8)
    if kind == "stairs":
        for y in range(2, 14):
            mask[y, 2:3 + (y - 2) // 2] = 255
    elif kind == "hole":
        mask[1:15, 1:15] = 255
        mask[4:12, 4:12] = 0
    elif kind == "islands":
        mask[0:4, 0:3] = 255
        mask[10:16, 12:16] = 255
        mask[6, 7] = 255
    return mask


def png_bytes(mask):
    image = Image.new("RGBA", (mask.shape[1], mask.shape[0]))
    image.putalpha(Image.fromarray(mask))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@pytest.mark.parametrize("kind", ["stairs", "hole", "islands"])
def test_pixel_edges_match_entire_pixel_cells_and_mesh(kind):
    mask = pixel_mask(kind)
    asset = import_asset(png_bytes(mask), "sprite.png", smoothing=False)
    polygons = vector_to_polygons(parse_vector(asset.canonical_svg))
    material = unary_union(polygons)
    expected = unary_union([box(x, y, x + 1, y + 1)
                            for y, x in np.argwhere(mask != 0)])
    assert material.symmetric_difference(expected).area == pytest.approx(0, abs=1e-9)
    for polygon in polygons:
        for ring in [polygon.exterior, *polygon.interiors]:
            points = list(ring.coords)
            for (x0, y0), (x1, y1) in zip(points, points[1:]):
                assert x0 == x1 or y0 == y1, "Pixel edges must stay axis-aligned"
    mesh = extrude_polygons(polygons, thickness=3)
    assert validate_mesh(mesh, polygons, 3)["watertight"]
    assert top_cap_geometry(mesh).symmetric_difference(expected).area == pytest.approx(0, abs=1e-9)


def test_default_is_original_smoothing_and_svg_is_unchanged():
    mask = pixel_mask("stairs")
    data = png_bytes(mask)
    default = import_asset(data, "sprite.png")
    explicit = import_asset(data, "sprite.png", smoothing=True)
    assert default.canonical_svg == explicit.canonical_svg == trace_mask(mask, PRESETS["exact"])
    assert default.canonical_svg != import_asset(data, "sprite.png", smoothing=False).canonical_svg
    svg = default.canonical_svg.encode()
    assert import_asset(svg, "shape.svg", smoothing=False).canonical_svg == svg.decode()


def test_old_smooth_assets_are_reused_but_pixel_assets_are_distinct():
    doc = new_document()
    data = png_bytes(pixel_mask("stairs"))
    for i, smoothing in enumerate([True, False, True, False]):
        asset = import_asset(data, "sprite.png", smoothing=smoothing).to_document_asset()
        if i == 0:
            asset["trace_settings"] = {"preset": "exact"}  # pre-toggle project
        dispatch("add_layers", doc, {"assets": [asset], "layers": [
            {"id": f"layer{i}", "asset_id": asset["id"]},
        ]})
    ids = [doc["layers"][f"layer{i}"]["asset_id"] for i in range(4)]
    assert ids[0] == ids[2]
    assert ids[1] == ids[3]
    assert ids[0] != ids[1]
    assert len(doc["assets"]) == 2


def test_api_smoothing_modes_and_project_roundtrip(tmp_path):
    store = DocumentStore(tmp_path)
    app = create_editor_app(store=store)
    doc = store.create_document(name="Pixel art")
    data = png_bytes(pixel_mask("stairs"))
    with app.test_client() as client:
        for smoothing in [None, "false", "true", "false"]:
            form = {"base_revision": str(doc["revision"]),
                    "files[]": (io.BytesIO(data), "sprite.png")}
            if smoothing is not None:
                form["smoothing"] = smoothing
            response = client.post(f'/api/v2/documents/{doc["id"]}/assets', data=form)
            assert response.status_code == 200, response.get_json()
            doc = response.get_json()["document"]
        assert len(doc["assets"]) == 2
        assert len(doc["layers"]) == 4
        assert {a["trace_settings"]["smoothing"] for a in doc["assets"].values()} == {True, False}
        package = client.get(f'/api/v2/documents/{doc["id"]}/package')
        assert package.status_code == 200
        restored = client.post('/api/v2/documents/import', data={
            "file": (io.BytesIO(package.data), "pixel.silhouettes"),
        })
        assert restored.status_code == 201, restored.get_json()
        assert restored.get_json()["assets"] == doc["assets"]


def test_api_rejects_invalid_smoothing_before_mutating(tmp_path):
    store = DocumentStore(tmp_path)
    app = create_editor_app(store=store)
    doc = store.create_document()
    with app.test_client() as client:
        response = client.post(f'/api/v2/documents/{doc["id"]}/assets', data={
            "smoothing": "maybe", "files[]": (io.BytesIO(png_bytes(pixel_mask("stairs"))), "sprite.png"),
        })
    assert response.status_code == 400
    assert store.load_document(doc["id"])["revision"] == doc["revision"]
