"""The Faustus copilot inside Plato's Hoard: a small same-origin relay for Flask.

HoardLink ships the relay for FastAPI and Node hosts (``hoard_link.copilot``); Plato is Flask, so the same four routes
live here with the same behaviour:

* ``GET  /api/copilot/info``       the app, its stable data space id and where the panel comes from;
* ``GET  /api/copilot/panel.js``   the ``<faustus-chat>`` element, served by Faustus through this relay;
* ``POST /api/copilot/proxy``      every panel request (chat turns as Server-Sent Events, sessions, uploads) goes through
  here; the Faustus token stays on the server, ``origin.app_id`` / ``data_space_id`` are forced and only the copilot
  paths of Faustus are reachable;
* ``GET  /api/copilot/launcher.js`` the floating "Faustus" button and side panel, added to every HTML page the app serves.

Faustus is found through ``PLATOS_FAUSTUS_URL`` / ``HOARD_FAUSTUS_URL`` (+ ``HOARD_FAUSTUS_TOKEN``) or the Hoard Link
config, falling back to ``127.0.0.1:7000`` / ``7001``. ``HOARD_COPILOT=0`` turns the copilot off.

The launcher is the one from the vendored Hoard Link (``hoard_link/copilot_launcher.py``); a copy of the same file in
``static/copilot/launcher.js`` serves while the vendored copy predates it.
"""

from __future__ import annotations

import base64
import importlib
import json
import logging
import os
import re
import sys
import threading
import types
import urllib.error
import urllib.request
import uuid
from email.message import Message
from email.parser import BytesHeaderParser
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from flask import Blueprint, Response, jsonify, request

APP_ID = "platos"
APP_NAME = "Plato's Hoard"
LAUNCHER_PATH = "/api/copilot/launcher.js"
_LAUNCHER_TAG = b'<script type="module" src="/api/copilot/launcher.js"></script>'
_HERE = Path(__file__).resolve().parent
_PKG = "_plato_hoard_link"
_FALLBACK_LAUNCHER = _HERE / "static" / "copilot" / "launcher.js"
_FORWARD_UP = ("content-type", "x-odysseus-run-id", "x-faustus-idempotent-replay", "x-faustus-api-version")
_FORWARD_DOWN = ("x-faustus-client-version", "x-odysseus-run-id")
log = logging.getLogger("plato.copilot")


def _vendored(name: str) -> Any:
    """A standard-library-only module of the vendored Hoard Link, loaded without running its package ``__init__``."""
    folder = _HERE / "hoard_link"
    if not folder.is_dir():
        return None
    if _PKG not in sys.modules:
        stub = types.ModuleType(_PKG)
        stub.__path__ = [str(folder)]  # type: ignore[attr-defined]
        sys.modules[_PKG] = stub
    try:
        return importlib.import_module(f"{_PKG}.{name}")
    except Exception:  # noqa: BLE001 - the copilot is an addition: a missing or older copy must not stop the editor
        return None


def launcher_js() -> bytes:
    """The browser launcher: the vendored Hoard Link's, else the copy that ships with the app."""
    module = _vendored("copilot_launcher")
    text = getattr(module, "LAUNCHER_JS", None)
    if isinstance(text, str) and text.strip():
        return text.encode("utf-8")
    return _FALLBACK_LAUNCHER.read_bytes()


def inject_launcher(html: bytes) -> bytes:
    """``html`` with the launcher script before ``</body>`` (or at the end); unchanged when it is already there."""
    if b"/api/copilot/launcher.js" in html:
        return html
    at = html.lower().rfind(b"</body>")
    if at < 0:
        return html + _LAUNCHER_TAG
    return html[:at] + _LAUNCHER_TAG + html[at:]


def multipart_context(body: bytes, content_type: str, origin: dict) -> bytes:
    """Replace only the ``context`` field of a multipart body; keep uploaded bytes and MIME framing exact."""
    header = Message()
    header["Content-Type"] = content_type
    boundary = header.get_param("boundary")
    if header.get_content_type() != "multipart/form-data" or not boundary:
        return body
    separators = list(re.finditer(rb"(?:^|\r\n)--" + re.escape(boundary.encode()) + rb"(?:--)?(?:\r\n|$)", body))
    replacements = []
    for start, end in zip(separators, separators[1:]):
        part = body[start.end():end.start()]
        split = part.find(b"\r\n\r\n")
        if split < 0:
            continue
        headers = BytesHeaderParser().parsebytes(part[:split] + b"\r\n\r\n")
        if headers.get_param("name", header="Content-Disposition") not in ("context", "hoard_context"):
            continue
        context = json.loads(part[split + 4:].decode("utf-8"))
        if isinstance(context, dict):
            context["origin"] = {**context.get("origin", {}), **origin}
            replacements.append((start.end() + split + 4, end.start(), json.dumps(context, ensure_ascii=False).encode("utf-8")))
    for start, end, replacement in reversed(replacements):
        body = body[:start] + replacement + body[end:]
    return body


def _path_allowed(path: Any) -> bool:
    """Only the copilot surface of Faustus is reachable through the relay."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return False
    route = urlsplit(path).path
    return route.startswith(("/api/hoard-copilot/", "/api/chat/", "/api/session/", "/api/sessions/")) \
        or route in ("/api/chat_stream", "/api/session", "/api/sessions")


def _error(status: int, message: str) -> Response:
    response = jsonify({"detail": message})
    response.status_code = status
    return response


class Relay:
    """Finds Faustus and streams its answers back without buffering (a chat turn is a Server-Sent Events stream)."""

    def __init__(self, urls: list[str], token: Optional[str]):
        self.urls = [u.rstrip("/") for u in urls if u]
        self.headers = {"Authorization": "Bearer " + token} if token else {}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback: never through a proxy
        self.selected: Optional[str] = None
        self._lock = threading.Lock()

    def locate(self) -> str:
        if self.selected:
            return self.selected
        for base in self.urls:
            try:
                req = urllib.request.Request(base + "/api/hoard-copilot/capabilities", headers=self.headers)
                with self.opener.open(req, timeout=3) as response:
                    if response.status == 200 and json.load(response).get("contract_version"):
                        with self._lock:
                            self.selected = base
                        return base
            except (OSError, ValueError, urllib.error.URLError):
                continue
        raise ConnectionError("Faustus copilot is unavailable on the configured endpoints")

    def forward(self, path: str, method: str = "GET", body: Optional[bytes] = None,
                content_type: str = "application/json", extra: Optional[dict] = None) -> Response:
        try:
            base = self.locate()
        except ConnectionError as exc:
            return _error(503, str(exc))
        headers = {**self.headers, **(extra or {}), "Content-Type": content_type}
        req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
        try:
            upstream = self.opener.open(req, timeout=900)
        except urllib.error.HTTPError as exc:   # Faustus answered with an error status: pass it through untouched
            upstream = exc
        except (OSError, urllib.error.URLError):
            self.selected = None
            return _error(503, "Faustus copilot connection failed")

        def chunks():
            try:
                while True:
                    chunk = upstream.read1(65536) if hasattr(upstream, "read1") else upstream.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                upstream.close()

        forwarded = {k: v for k, v in upstream.headers.items() if k.lower() in _FORWARD_UP}
        forwarded["X-Accel-Buffering"] = "no"
        forwarded["Cache-Control"] = "no-cache"
        status = upstream.code if isinstance(upstream, urllib.error.HTTPError) else upstream.status
        return Response(chunks(), status=status, headers=forwarded, direct_passthrough=True)


def _space_id(data_dir: Path) -> str:
    """One stable data space id per data folder, created once."""
    path = Path(data_dir) / "copilot-space-id"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(str(uuid.uuid4()))
    except FileExistsError:
        pass
    return path.read_text(encoding="utf-8").strip()


def _faustus_config() -> tuple[list[str], Optional[str]]:
    env_url = os.environ.get("PLATOS_FAUSTUS_URL") or os.environ.get("HOARD_FAUSTUS_URL")
    token = os.environ.get("HOARD_FAUSTUS_TOKEN") or None
    urls: list[str] = [env_url] if env_url else []
    module = _vendored("config")
    if module is not None:
        try:
            link = module.LinkConfig.load(app=APP_ID)
            urls = urls or list(link.faustus_urls)
            token = token or link.faustus_token
        except Exception:  # noqa: BLE001
            log.debug("Hoard Link config not readable", exc_info=True)
    return urls or ["http://127.0.0.1:7000", "http://127.0.0.1:7001"], token


def make_blueprint(data_dir: Path | str, *, faustus_urls: Optional[list[str]] = None, token: Optional[str] = None) -> Blueprint:
    if faustus_urls is None:
        faustus_urls, found = _faustus_config()
        token = token if token is not None else found
    relay = Relay(list(faustus_urls), token)
    space_id = _space_id(Path(data_dir))
    bp = Blueprint("faustus_copilot", __name__, url_prefix="/api/copilot")
    bp.relay = relay  # type: ignore[attr-defined]

    @bp.get("/info")
    def info():
        try:
            base = relay.locate()
        except ConnectionError as exc:
            return _error(503, str(exc))
        return jsonify({"app_id": APP_ID, "app_name": APP_NAME, "data_space_id": space_id, "panel_url": "/api/copilot/panel.js",
                        "server": "/", "tool_server_id": APP_ID, "settings_url": base + "/studio/settings"})

    @bp.get("/panel.js")
    def panel():
        return relay.forward("/api/hoard-copilot/panel.js")

    @bp.get("/launcher.js")
    def launcher():
        return Response(launcher_js(), mimetype="application/javascript", headers={"Cache-Control": "no-cache"})

    @bp.post("/proxy")
    def proxy():
        try:
            envelope = request.get_json(force=True, silent=True)
            if not isinstance(envelope, dict):
                raise ValueError("Invalid copilot transport request")
            path = envelope.get("path", "")
            if not _path_allowed(path):
                raise ValueError("Unknown copilot operation")
            body = None
            if "raw_body" in envelope:
                body = base64.b64decode(envelope["raw_body"], validate=True)
                body = multipart_context(body, envelope.get("content_type", ""), {"app_id": APP_ID, "data_space_id": space_id})
            elif "body" in envelope:
                payload = envelope["body"]
                if isinstance(payload, dict):
                    for field in ("context", "hoard_context"):
                        if isinstance(payload.get(field), dict):
                            payload[field]["origin"] = {**payload[field].get("origin", {}), "app_id": APP_ID, "data_space_id": space_id}
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            extra = {k: v for k, v in (envelope.get("headers") or {}).items() if k.lower() in _FORWARD_DOWN}
            return relay.forward(path, str(envelope.get("method", "GET")).upper(), body,
                                 envelope.get("content_type", "application/json"), extra)
        except (ValueError, TypeError, AttributeError):
            return _error(400, "Invalid copilot transport request")

    return bp


def install_copilot(app: Any, data_dir: Path | str, *, faustus_urls: Optional[list[str]] = None, token: Optional[str] = None) -> bool:
    """Mount the relay and add the launcher to every HTML page. ``HOARD_COPILOT=0`` leaves the app as it was."""
    if os.environ.get("HOARD_COPILOT", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    app.register_blueprint(make_blueprint(data_dir, faustus_urls=faustus_urls, token=token))

    @app.after_request
    def _add_launcher(response):
        if (request.method not in ("GET", "HEAD") or request.path.startswith("/api/") or response.status_code != 200
                or response.direct_passthrough or response.mimetype != "text/html" or response.headers.get("Content-Encoding")):
            return response
        response.set_data(inject_launcher(response.get_data()))
        response.headers.pop("ETag", None)
        return response

    return True
