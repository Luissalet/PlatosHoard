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

[`faustus-plugin.json`](faustus-plugin.json) lets Hoard Hub discover and start the app. Faustus reaches its REST editor API through the generic REST MCP bridge defined by its manifest. Plato currently has **no** shared `/api/agent/call` contract, agent token or Hub event publisher; the bridge and Hub handle discovery and launch.

## Verify

```sh
python -m pytest tests/editor -q
python -m pytest tests -q
```

The repository's `implementation_status.json` records completed editor tasks and known test results. The full historical suite has recorded a pre-existing star IoU failure; check the current run rather than assuming it passed. Node's `npm test` is a placeholder and is not the Python test suite.
