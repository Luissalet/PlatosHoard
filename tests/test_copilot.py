"""The Faustus copilot in Plato's Hoard: relay routes, launcher injection, manifest."""
from __future__ import annotations

import base64
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from flask import Flask
from werkzeug.serving import make_server

import plato_copilot

ROOT = Path(__file__).resolve().parent.parent


class FakeFaustus:
    """Just enough of Faustus: capabilities, the panel element, a chat stream that stays open until released."""

    def __init__(self):
        self.seen = []
        self.release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, status, body, kind="application/json", extra=None):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                owner.seen.append(("GET", self.path, self.headers.get("Authorization"), None))
                if self.path == "/api/hoard-copilot/capabilities":
                    self._send(200, b'{"contract_version": "1.0"}')
                elif self.path == "/api/hoard-copilot/panel.js":
                    self._send(200, b"customElements.define('faustus-chat', class extends HTMLElement {});", "application/javascript")
                else:
                    self._send(404, b'{"detail": "nope"}')

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                owner.seen.append(("POST", self.path, self.headers.get("Authorization"), body))
                if self.path == "/api/chat_stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("X-Odysseus-Run-Id", "run-1")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for part in (b"data: one\n\n", b"data: two\n\n"):
                        if part.startswith(b"data: two"):
                            owner.release.wait(10)
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                elif self.path == "/api/sessions/missing":
                    self._send(404, b'{"detail": "no such session"}')
                else:
                    self._send(200, b'{"ok": true}')

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.release.set()
        self.server.shutdown()


@pytest.fixture()
def faustus():
    fake = FakeFaustus()
    yield fake
    fake.close()


@pytest.fixture()
def relay_app(tmp_path, faustus):
    app = Flask(__name__)

    @app.get("/page")
    def page():
        return "<html><body><h1>Page</h1></body></html>"

    @app.get("/bare")
    def bare():
        return "<h1>fragment</h1>"

    @app.get("/api/data")
    def data():
        return "<html><body>not a page</body></html>", 200, {"Content-Type": "text/html"}

    plato_copilot.install_copilot(app, tmp_path, faustus_urls=[faustus.url], token="secret-token")
    return app


def test_info_names_the_app_and_keeps_a_stable_space_id(relay_app, tmp_path, faustus):
    client = relay_app.test_client()
    first = client.get("/api/copilot/info").get_json()
    assert first["app_id"] == "platos" and first["app_name"] == "Plato's Hoard"
    assert first["panel_url"] == "/api/copilot/panel.js" and first["tool_server_id"] == "platos"
    assert first["settings_url"] == faustus.url + "/studio/settings"
    assert (tmp_path / "copilot-space-id").read_text().strip() == first["data_space_id"]
    again = Flask(__name__)
    plato_copilot.install_copilot(again, tmp_path, faustus_urls=[faustus.url], token="x")
    assert again.test_client().get("/api/copilot/info").get_json()["data_space_id"] == first["data_space_id"]


def test_token_stays_on_the_server(relay_app, faustus):
    client = relay_app.test_client()
    panel = client.get("/api/copilot/panel.js")
    assert panel.status_code == 200 and b"faustus-chat" in panel.data
    assert "secret-token" not in client.get("/api/copilot/info").get_data(as_text=True)
    assert "secret-token" not in panel.get_data(as_text=True)
    assert all(auth == "Bearer secret-token" for _, _, auth, _ in faustus.seen)


def test_launcher_is_served_and_injected_into_html_pages_only(relay_app):
    client = relay_app.test_client()
    launcher = client.get("/api/copilot/launcher.js")
    assert launcher.status_code == 200 and launcher.mimetype == "application/javascript"
    assert b"__hoardCopilotLauncher" in launcher.data and b"/api/copilot/proxy" in launcher.data
    page = client.get("/page").get_data()
    assert page.count(b'src="/api/copilot/launcher.js"') == 1 and page.index(b"launcher.js") < page.index(b"</body>")
    assert client.get("/bare").get_data().endswith(b'launcher.js"></script>')
    assert b"launcher.js" not in client.get("/api/data").get_data()


def test_inject_launcher_is_idempotent():
    once = plato_copilot.inject_launcher(b"<body>x</body>")
    assert plato_copilot.inject_launcher(once) == once


def test_proxy_forces_origin_and_forwards_the_body(relay_app, faustus):
    client = relay_app.test_client()
    space = client.get("/api/copilot/info").get_json()["data_space_id"]
    envelope = {"path": "/api/hoard-copilot/turn", "method": "POST", "headers": {"X-Faustus-Client-Version": "1", "Authorization": "Bearer evil"},
                "body": {"text": "hi", "context": {"origin": {"app_id": "someone-else", "data_space_id": "stolen", "surface": "page"}}}}
    assert client.post("/api/copilot/proxy", json=envelope).status_code == 200
    method, path, auth, body = faustus.seen[-1]
    sent = json.loads(body)
    assert (method, path, auth) == ("POST", "/api/hoard-copilot/turn", "Bearer secret-token")
    assert sent["context"]["origin"] == {"app_id": "platos", "data_space_id": space, "surface": "page"}


def test_proxy_forces_origin_in_a_multipart_context_and_keeps_the_file_bytes(relay_app, faustus):
    client = relay_app.test_client()
    space = client.get("/api/copilot/info").get_json()["data_space_id"]
    boundary = "xBOUNDARYx"
    payload = bytes(range(256))
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"context\"\r\n\r\n{json.dumps({'origin': {'app_id': 'evil'}})}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.bin\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() \
        + payload + f"\r\n--{boundary}--\r\n".encode()
    envelope = {"path": "/api/hoard-copilot/upload", "method": "POST", "content_type": f"multipart/form-data; boundary={boundary}",
                "raw_body": base64.b64encode(body).decode()}
    assert client.post("/api/copilot/proxy", json=envelope).status_code == 200
    sent = faustus.seen[-1][3]
    assert payload in sent
    assert json.dumps({"origin": {"app_id": "platos", "data_space_id": space}}).encode() in sent
    assert b"evil" not in sent


@pytest.mark.parametrize("path", ["/api/admin/keys", "//evil.example/api/chat/x", "api/chat/x", "/api/hoard-copilot", "/studio/settings", 7, None])
def test_proxy_refuses_paths_outside_the_copilot_surface(relay_app, faustus, path):
    before = len(faustus.seen)
    response = relay_app.test_client().post("/api/copilot/proxy", json={"path": path, "method": "GET"})
    assert response.status_code == 400
    assert len([s for s in faustus.seen[before:] if s[1] != "/api/hoard-copilot/capabilities"]) == 0


def test_proxy_refuses_garbage(relay_app):
    client = relay_app.test_client()
    assert client.post("/api/copilot/proxy", data=b"not json", content_type="application/json").status_code == 400
    assert client.post("/api/copilot/proxy", json=["list"]).status_code == 400
    assert client.post("/api/copilot/proxy", json={"path": "/api/chat/x", "raw_body": "***"}).status_code == 400


def test_upstream_errors_pass_through(relay_app):
    response = relay_app.test_client().post("/api/copilot/proxy", json={"path": "/api/sessions/missing", "method": "GET"})
    assert response.status_code == 404 and response.get_json() == {"detail": "nope"}


def test_faustus_down_answers_503_and_recovers(tmp_path):
    app = Flask(__name__)
    plato_copilot.install_copilot(app, tmp_path, faustus_urls=["http://127.0.0.1:9"], token=None)
    client = app.test_client()
    assert client.get("/api/copilot/info").status_code == 503
    assert client.get("/api/copilot/panel.js").status_code == 503
    assert client.post("/api/copilot/proxy", json={"path": "/api/chat/x"}).status_code == 503


def test_chat_stream_is_not_buffered(tmp_path, faustus):
    """The first event reaches the browser while Faustus still holds the turn open."""
    app = Flask(__name__)
    plato_copilot.install_copilot(app, tmp_path, faustus_urls=[faustus.url], token="t")
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/copilot/proxy",
                                         data=json.dumps({"path": "/api/chat_stream", "method": "POST", "body": {"message": "hi"}}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            assert response.headers["Content-Type"] == "text/event-stream"
            assert response.headers["X-Odysseus-Run-Id"] == "run-1"
            first = response.read1(1024)
            assert b"data: one" in first and not faustus.release.is_set()
            faustus.release.set()
            rest = response.read()
            assert b"data: two" in first + rest
    finally:
        faustus.release.set()
        server.shutdown()


def test_copilot_off_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("HOARD_COPILOT", "0")
    app = Flask(__name__)
    assert plato_copilot.install_copilot(app, tmp_path) is False
    assert app.test_client().get("/api/copilot/info").status_code == 404


def test_the_app_serves_the_copilot_on_its_editor(tmp_path, faustus):
    from app import app as plato

    relay = plato.blueprints["faustus_copilot"].relay
    relay.urls, relay._selected = (faustus.url,), None
    client = plato.test_client()
    page = client.get("/editor")
    assert page.status_code == 200 and page.get_data().count(b"/api/copilot/launcher.js") == 1
    assert b"launcher.js" in client.get("/legacy").get_data()
    assert client.get("/api/copilot/info").get_json()["app_id"] == "platos"
    assert b"launcher.js" not in client.get("/static/editor/editor.js").get_data()


def test_the_launcher_is_the_vendored_one_and_the_relay_is_the_vendored_relay(relay_app):
    from _plato_hoard_link.copilot_launcher import LAUNCHER_JS
    from _plato_hoard_link.copilot_stdlib import CopilotRelay

    assert relay_app.test_client().get("/api/copilot/launcher.js").get_data(as_text=True) == LAUNCHER_JS
    assert isinstance(relay_app.blueprints["faustus_copilot"].relay, CopilotRelay)
    assert not (ROOT / "static" / "copilot").exists()   # no private copy of the launcher


def test_faustus_url_and_token_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATOS_FAUSTUS_URL", "http://127.0.0.1:9/")
    monkeypatch.setenv("HOARD_FAUSTUS_TOKEN", "abc")
    relay = plato_copilot.make_blueprint(tmp_path).relay
    assert relay.urls == ("http://127.0.0.1:9",) and relay.token == "abc"


def test_the_copilot_is_left_out_when_the_vendored_copy_has_no_copilot_modules(tmp_path, monkeypatch):
    monkeypatch.setattr(plato_copilot, "_stdlib", None)
    app = Flask(__name__)
    assert plato_copilot.install_copilot(app, tmp_path) is False
    assert app.test_client().get("/api/copilot/info").status_code == 404


def test_manifest_declares_the_copilot():
    manifest = json.loads((ROOT / "faustus-plugin.json").read_text(encoding="utf-8"))
    copilot = manifest["x-family"]["copilot"]
    assert copilot["provider"] == "faustus" and copilot["contract"] == "1.0" and copilot["panel"] == "/api/copilot/panel.js"
    assert "active_objects" in copilot["context"]
