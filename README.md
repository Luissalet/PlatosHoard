# Plato's Hoard

[Español](README.es.md)

A local editor for turning 2D images into editable silhouettes and printable 3D relief. Upload a PNG, refine the layers in the browser editor and export the resulting black silhouette, vector SVG or extruded STL. The original one-shot converter remains available at `/legacy`.

## Run locally

Requires Python 3 and the packages in `requirements.txt`.

```sh
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

Open [http://127.0.0.1:5000/editor](http://127.0.0.1:5000/editor). On other operating systems, use `.venv/bin/python` in the commands. The Flask app stores editor documents and jobs under `data/` and limits uploads to 16 MB.

## Faustus and Hoard Hub

[`faustus-plugin.json`](faustus-plugin.json) lets Hoard Hub discover and start the app. Faustus reaches its REST editor API through the generic REST MCP bridge defined by its manifest. Plato has **no** shared `/api/agent/call` contract: the bridge and Hub handle discovery and launch.

### Export events

When `python app.py` runs and an export job finishes with STL in its formats, Plato unpacks every STL of the bundle into `data/exports/<job id>/<family>/` and announces each one on the family bus as `plato.export.done {path, ref, title, format, job_id, document_id, layer_id, family}`, where `ref` is `hoard://plato/export/<job id>` and `title` is the document and layer name. The Hub's recommended rule hands the `path` to the model library (`model_import_file`). Exports have no 3MF format, so only STL is announced. The events of one bundle are sent 5.5 s apart (`PLATO_EVENT_SPACING_S`) because that rule ignores a repeat inside its 5 s cooldown. Plato writes `data/mcp-token` at start (the Hub knows the sender by it). A job is noticed by a background watcher, not only when the editor asks for it. Without the Hub nothing changes: the STL files are still unpacked and the emit is dropped. The legacy `/api/process` preview is not an export and emits nothing.

`hoard_link/` is the shared family library, vendored unchanged; only its standard-library `family` module is loaded (through a private package name in `plato_family.py`), so Plato's requirements do not change.

## Verify

```sh
python -m pytest tests/editor -q
python -m pytest tests -q
```

The repository's `implementation_status.json` records completed editor tasks and known test results. The full historical suite has recorded a pre-existing star IoU failure; check the current run rather than assuming it passed. Node's `npm test` is a placeholder and is not the Python test suite.
