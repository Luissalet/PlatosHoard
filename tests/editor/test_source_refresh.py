import copy
import hashlib
import io
import json
import zipfile

import pytest
from PIL import Image, ImageDraw

from silhouettes.editor.api import create_editor_app
from silhouettes.editor.document_store import DocumentStore
from silhouettes.editor.source_refresh import source_paths


def png(changed=False):
    image = Image.new('RGBA', (32, 32))
    draw = ImageDraw.Draw(image)
    draw.rectangle((3, 3, 24, 24), fill='black')
    if changed:
        draw.rectangle((15, 10, 29, 29), fill='black')
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def setup_project(tmp_path, smoothing=False):
    store = DocumentStore(tmp_path / 'server')
    app = create_editor_app(store)
    app.testing = True
    client = app.test_client()
    doc = client.post('/api/v2/documents', json={'name': 'Test'}).get_json()
    response = client.post(f"/api/v2/documents/{doc['id']}/assets", data={
        'files[]': (io.BytesIO(png()), '0144-articuno.png'), 'smoothing': str(smoothing).lower()})
    assert response.status_code == 200
    doc = response.get_json()['document']
    package = client.get(f"/api/v2/documents/{doc['id']}/package").data
    root = tmp_path / 'Generation 1'
    (root / '144').mkdir(parents=True)
    (root / '144/project.silhouettes').write_bytes(package)
    source = root / '0144-articuno.png'
    source.write_bytes(png())
    config = store.root / 'source-collections.json'
    config.write_text(json.dumps({'collections': [{'projects': str(root), 'artwork': str(root)}]}))
    return client, doc, package, source, config


@pytest.mark.parametrize('smoothing', [False, True])
def test_open_refreshes_png_preserves_placement_and_saves_current_bytes(tmp_path, smoothing):
    client, before, package, source, config = setup_project(tmp_path, smoothing)
    source.write_bytes(png(True))
    response = client.post('/api/v2/documents/import', data={'file': (io.BytesIO(package), 'project.silhouettes')})
    assert response.status_code == 201
    assert response.headers['X-Plato-Images-Updated'] == '1'
    after = response.get_json()
    layers = copy.deepcopy(after['layers'])
    for key, layer in layers.items():
        layer['asset_id'] = before['layers'][key]['asset_id']
    assert layers == before['layers']
    assert after['canvas'] == before['canvas']
    old_asset = next(iter(before['assets'].values()))
    asset = next(iter(after['assets'].values()))
    assert asset['id'] != old_asset['id']
    assert asset['geometry_hash'] != old_asset['geometry_hash']
    assert asset['trace_settings']['smoothing'] is smoothing
    assert asset['mm_per_source_unit'] == old_asset['mm_per_source_unit']
    assert asset['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    saved = client.get(f"/api/v2/documents/{after['id']}/package").data
    with zipfile.ZipFile(io.BytesIO(saved)) as archive:
        assert archive.read(f"assets/{asset['id']}/source.png") == source.read_bytes()
    # Save the reopened project in place, then reopen: no further re-tracing.
    (source.parent / '144/project.silhouettes').write_bytes(saved)
    response = client.post('/api/v2/documents/import', data={'file': (io.BytesIO(saved), 'project.silhouettes')})
    assert response.status_code == 201
    assert response.headers['X-Plato-Images-Updated'] == '0'
    assert next(iter(response.get_json()['assets'])) == asset['id']


@pytest.mark.parametrize('state', ['missing', 'invalid', 'unchanged'])
def test_missing_or_invalid_original_preserves_portable_copy(tmp_path, state):
    client, before, package, source, config = setup_project(tmp_path)
    if state == 'missing':
        source.unlink()
    elif state == 'invalid':
        source.write_bytes(b'not an image')
    response = client.post('/api/v2/documents/import', data={'file': (io.BytesIO(package), 'project.silhouettes')})
    assert response.status_code == 201
    assert response.headers['X-Plato-Images-Updated'] == '0'
    assert response.get_json()['assets'] == before['assets']
    assert bool(json.loads(response.headers['X-Plato-Images-Warnings'])) == (state != 'unchanged')


def test_ambiguous_collections_never_select_png_by_name(tmp_path):
    client, doc, package, source, config = setup_project(tmp_path)
    other = tmp_path / 'Generation 2'
    (other / '144').mkdir(parents=True)
    (other / '144/project.silhouettes').write_bytes(package)
    (other / source.name).write_bytes(png(True))
    data = json.loads(config.read_text())
    data['collections'].append({'projects': str(other), 'artwork': str(other)})
    config.write_text(json.dumps(data))
    paths, configured = source_paths(doc, config)
    assert configured and paths == {}
