import io
import re

import pytest

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_editor_route_returns_200_and_required_ids(client):
    response = client.get("/editor")

    assert response.status_code == 200
    html = response.get_data(as_text=True)

    required_ids = [
        "document-toolbar",
        "canvas-width-mm",
        "canvas-height-mm",
        "canvas-padding-top",
        "canvas-padding-right",
        "canvas-padding-bottom",
        "canvas-padding-left",
        "import-files",
        "layer-tree",
        "editor-svg",
        "inspector",
        "viewer-3d",
        "fit-best",
        "fit-at-position",
        "auto-scale-drag",
        "fit-status",
        "recipe-select",
        "export-selected",
        "export-batch",
        "errors-panel",
        "canvas-guides",
        "layer-content",
        "constraint-overlays",
        "selection-handles",
    ]

    for element_id in required_ids:
        assert f'id="{element_id}"' in html, f"Falta el ID obligatorio: {element_id}"


def test_editor_route_has_no_duplicate_ids(client):
    response = client.get("/editor")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    ids = re.findall(r'id="([^"]+)"', html)
    duplicates = {element_id for element_id in ids if ids.count(element_id) > 1}

    assert duplicates == set()


def test_legacy_route_still_returns_200(client):
    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Silhouette 3D" in html
    assert "api/process" in html


def test_editor_static_assets_are_served(client):
    script = client.get("/static/editor/editor.js")
    style = client.get("/static/editor/editor.css")

    assert script.status_code == 200
    assert style.status_code == 200
    assert b"editor.js" in script.data or b"store" in script.data
    assert b"editor-shell" in style.data


def test_legacy_api_process_contract_rejects_missing_file(client):
    response = client.post("/api/process", data={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload == {"error": "No file uploaded"}


def test_legacy_api_process_contract_rejects_non_png(client):
    response = client.post(
        "/api/process",
        data={"file": (io.BytesIO(b"not a png"), "example.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Only PNG files are supported"}
