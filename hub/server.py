#!/usr/bin/env python3
"""The hub's local web server, and `hub open`, `hub serve` and `hub stop`.

It listens on 127.0.0.1 only, and answers only a browser that holds this
launch's token:

- The Host header must be 127.0.0.1 or localhost at this port. A web page
  that points its own domain at 127.0.0.1 (DNS rebinding) still sends its own
  host name, and is refused.
- Every request needs the token cookie, set once from the URL `hub open`
  opens. The cookie is SameSite=Strict, so other sites' pages cannot send it.
- There are no CORS headers, so no other origin may read a response.

The port is the same every time -- 7437, or hub.json's "port" -- so the hub
can be bookmarked; hub.json's "port": 0 asks for a free one each time. A port
something else holds is an error that says so, never a quiet move elsewhere.

The token is kept too, in the cache folder's `token` (mode 0600), and the
cookie lasts a year, so a bookmark still works after the server restarts.
Deleting the file makes the next start issue a new token, which signs out
every browser. The running server's port and token are in server.json (mode
0600), for `hub open` and `hub stop` to find. Anything running as the user can
read both files. The token keeps out web pages, not local processes, which can
read everything the hub reads anyway.

The server exits after IDLE_SECONDS without a request. An open page sends a
heartbeat every minute, so the server stays up while someone is looking.
"""
from __future__ import annotations

import argparse
import errno
import http.cookies
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discovery  # noqa: E402
import paths  # noqa: E402
from registry import Registry  # noqa: E402

IDLE_SECONDS = float(os.environ.get("HUB_IDLE_SECONDS", "600"))
START_TIMEOUT = 15  # seconds `hub open` waits for a new server
WEB = os.path.join(paths.ROOT, "web")
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
    "/static/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
VIEW_FILE = re.compile(r"^/views/([a-z0-9-]+)/(.+)$")
PAGE_FILE = re.compile(r"^/sources/([^/]+)/page/(.+)$")
# A file of a view, in its folder or below. No part starts with a dot, so a
# path can neither climb out (..) nor reach a hidden file.
VIEW_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$")
VIEW_TYPES = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
              "css": "text/css; charset=utf-8", "json": "application/json",
              "svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg",
              "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
WIDGET = re.compile(r"^/api/sources/([^/]+)/widgets/([^/]+)$")
CALL = re.compile(r"^/api/sources/([^/]+)/call/([^/]+)$")
ENTITY = re.compile(r"^/api/entities/([^/]+)/([^/]+)$")
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                               "base-uri 'none'; form-action 'self'",
}
# A view is one source's page -- the source's own, or one the hub ships --
# shown in a frame on the hub's page, and nowhere else.
VIEW_HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'self'; "
                               "base-uri 'none'; form-action 'self'",
}


COOKIE_SECONDS = 365 * 86400


def info_path() -> str:
    return os.path.join(paths.cache_dir(), "server.json")


def token_path() -> str:
    return os.path.join(paths.cache_dir(), "token")


def load_token() -> str:
    """The hub's token: the one kept from earlier starts, else a new one,
    written readable by its owner only."""
    try:
        with open(token_path(), encoding="utf-8") as f:
            token = f.read().strip()
        if len(token) >= 32:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    os.makedirs(paths.cache_dir(), mode=0o700, exist_ok=True)
    tmp = f"{token_path()}.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    os.replace(tmp, token_path())
    return token


class Hub(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, registry: Registry):
        super().__init__(("127.0.0.1", port), Handler)
        self.registry = registry
        self.port = self.server_address[1]
        self.token = load_token()
        self.cookie = f"hub_token_{self.port}"  # cookies ignore ports; keep two hubs apart
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.last_request = time.monotonic()


class Handler(BaseHTTPRequestHandler):
    server_version = "toolworks-hub"
    sys_version = ""

    def log_message(self, fmt, *args):
        if os.environ.get("HUB_DEBUG"):
            super().log_message(fmt, *args)

    def do_GET(self):
        hub = self.server
        hub.last_request = time.monotonic()
        if self.headers.get("Host") not in hub.hosts:
            return self._text(403, "This hub answers only on 127.0.0.1.\n")
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)

        if url.path == "/" and "token" in query:
            if not _same(query["token"][0], hub.token):
                return self._text(403, "That token is not this hub's. Run `hub open` again.\n")
            return self._send(303, "text/plain; charset=utf-8", b"", {
                "Location": "/",
                "Set-Cookie": f"{hub.cookie}={hub.token}; Path=/; HttpOnly; SameSite=Strict; "
                              f"Max-Age={COOKIE_SECONDS}",
            })
        if not self._authorised():
            return self._text(403, "This browser has no token for this hub. Open it with `hub open`.\n")

        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            with open(os.path.join(WEB, name), "rb") as f:
                return self._send(200, ctype, f.read())
        m = VIEW_FILE.match(url.path)
        if m:
            return self._view_file(os.path.join(WEB, "views", m.group(1)), m.group(2))
        m = PAGE_FILE.match(url.path)
        if m:
            folder = hub.registry.page_dir(urllib.parse.unquote(m.group(1)))
            if folder is None:
                return self._json(404, {"error": "not found"})
            return self._view_file(folder, m.group(2))
        if url.path == "/api/ping":
            return self._json(200, {"ok": True})
        if url.path == "/api/status":
            hub.registry.refresh(force=query.get("refresh") == ["1"])
            return self._json(200, hub.registry.status())
        m = WIDGET.match(url.path)
        if m:
            key, widget_id = (urllib.parse.unquote(g) for g in m.groups())
            code, payload = hub.registry.widget(key, widget_id, (query.get("query") or [None])[0])
            return self._json(code, payload)
        m = CALL.match(url.path)
        if m:
            key, op = (urllib.parse.unquote(g) for g in m.groups())
            return self._json(*hub.registry.call(key, op, (query.get("args") or ["{}"])[0]))
        m = ENTITY.match(url.path)
        if m:
            etype, eid = (urllib.parse.unquote(g) for g in m.groups())
            hub.registry.refresh()
            return self._json(*hub.registry.entity(etype, eid))
        return self._json(404, {"error": "not found"})

    def _view_file(self, folder: str, rel: str) -> None:
        """A file of a view, read from inside its folder only: a symlink that
        leads out of the folder is not followed."""
        ctype = VIEW_TYPES.get(rel.rsplit(".", 1)[-1].lower()) if VIEW_PATH.match(rel) else None
        root = os.path.realpath(folder)
        path = os.path.realpath(os.path.join(root, rel))
        if ctype is None or not path.startswith(root + os.sep) or not os.path.isfile(path):
            return self._json(404, {"error": "not found"})
        with open(path, "rb") as f:
            return self._send(200, ctype, f.read(), VIEW_HEADERS)

    def _authorised(self) -> bool:
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return False
        morsel = jar.get(self.server.cookie)
        return morsel is not None and _same(morsel.value, self.server.token)

    def _send(self, code: int, ctype: str, body: bytes, extra=None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in {**HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, text: str) -> None:
        self._send(code, "text/plain; charset=utf-8", text.encode())

    def _json(self, code: int, value) -> None:
        self._send(code, "application/json", json.dumps(value).encode())


def _same(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


# --- server.json -------------------------------------------------------------

def read_info():
    try:
        with open(info_path(), encoding="utf-8") as f:
            info = json.load(f)
        return info if isinstance(info, dict) else None
    except (OSError, ValueError):
        return None


def _write_info(hub: Hub) -> None:
    os.makedirs(paths.cache_dir(), mode=0o700, exist_ok=True)
    tmp = f"{info_path()}.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"port": hub.port, "token": hub.token, "pid": os.getpid(),
                   "started_at": time.time()}, f)
    os.replace(tmp, info_path())


def _remove_info() -> None:
    info = read_info()
    if info and info.get("pid") == os.getpid():
        try:
            os.unlink(info_path())
        except OSError:
            pass


def ping(info: dict) -> bool:
    """Whether the server in info answers with its token. Proxies are bypassed:
    an http_proxy in the environment must not carry the token off the machine."""
    try:
        port = int(info["port"])
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ping",
                                     headers={"Cookie": f"hub_token_{port}={info['token']}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


# --- commands ----------------------------------------------------------------

def serve(port=None, idle: bool = True) -> int:
    if port is None:
        port, _ = discovery.port_setting()
    registry = Registry()
    try:
        hub = Hub(port, registry)
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, errno.EACCES):
            print(f"hub: port {port} is in use by another program. Set \"port\" in "
                  f"{discovery.tilde(paths.hub_config_path())} to another number, or to 0 for a "
                  f"free port each time.", file=sys.stderr, flush=True)
            return 1
        raise
    # Detect in the background so the first request is answered at once; it
    # waits on the registry's lock until detection finishes.
    threading.Thread(target=registry.refresh, kwargs={"force": True}, daemon=True).start()
    _write_info(hub)

    def stop(*_):
        threading.Thread(target=hub.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)

    if idle:
        def watch():
            while True:
                time.sleep(min(5.0, max(0.5, IDLE_SECONDS / 4)))
                if time.monotonic() - hub.last_request > IDLE_SECONDS:
                    hub.shutdown()
                    return
        threading.Thread(target=watch, daemon=True).start()

    print(f"hub: serving on http://127.0.0.1:{hub.port}/ (pid {os.getpid()})", flush=True)
    try:
        hub.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        hub.server_close()
        _remove_info()
    return 0


def open_hub(browser: bool = True) -> int:
    info = read_info()
    if not (info and ping(info)):
        os.makedirs(paths.cache_dir(), mode=0o700, exist_ok=True)
        log_path = os.path.join(paths.cache_dir(), "server.log")
        with open(log_path, "ab") as log:
            proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "serve"],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    start_new_session=True)
        deadline = time.monotonic() + START_TIMEOUT
        while True:
            info = read_info()
            if info and info.get("pid") == proc.pid and ping(info):
                break
            if proc.poll() is not None or time.monotonic() > deadline:
                print(f"hub: the server did not start; see {log_path}", file=sys.stderr)
                last = _last_line(log_path)
                if last:
                    print(last, file=sys.stderr)
                return 1
            time.sleep(0.1)
    url = f"http://127.0.0.1:{info['port']}/?token={info['token']}"
    print(url)
    opener = "open" if sys.platform == "darwin" else shutil.which("xdg-open")
    if browser and opener:
        subprocess.run([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


def _last_line(path: str) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.fstat(f.fileno()).st_size - 2000))
            lines = f.read().decode("utf-8", "replace").strip().splitlines()
        return lines[-1] if lines else ""
    except OSError:
        return ""


def stop_hub() -> int:
    info = read_info()
    if not (info and ping(info)):
        if info:
            try:
                os.unlink(info_path())  # left by a server that did not exit cleanly
            except OSError:
                pass
        print("hub: not running")
        return 0
    os.kill(int(info["pid"]), signal.SIGTERM)
    deadline = time.monotonic() + 5
    while read_info() is not None and time.monotonic() < deadline:
        time.sleep(0.1)
    if read_info() is not None:
        print("hub: the server did not stop", file=sys.stderr)
        return 1
    print("hub: stopped")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="hub")
    sub = p.add_subparsers(dest="command", required=True)
    o = sub.add_parser("open", help="start the server if needed and open the hub in a browser")
    o.add_argument("--no-browser", action="store_true", help="only print the URL")
    s = sub.add_parser("serve", help="run the server in the foreground")
    s.add_argument("--port", type=int, default=None,
                   help=f"default: hub.json's port, else {discovery.DEFAULT_PORT}; 0 for a free port")
    s.add_argument("--no-idle-exit", action="store_true", help="keep running with no requests")
    sub.add_parser("stop", help="stop the running server")
    a = p.parse_args(argv)
    if a.command == "open":
        return open_hub(browser=not a.no_browser)
    if a.command == "serve":
        return serve(port=a.port, idle=not a.no_idle_exit)
    return stop_hub()


if __name__ == "__main__":
    sys.exit(main())
