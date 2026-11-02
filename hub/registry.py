#!/usr/bin/env python3
"""The hub's live view of its sources, and the last good result of each widget.

Sources are found and detected again at most every DETECT_EVERY seconds, when
someone asks for the status. A widget is fetched again once its source's
refresh_seconds have passed. When a fetch fails, the last good result is kept
and served with the failure beside it, so a source that breaks for a minute
shows old data with a warning rather than an empty panel.

Search widgets are fetched on every query and never cached, and so are calls
from a view: a source's own page (its manifest's "page", inside its page/
folder), or else a page the hub ships for it, under web/views/<name>/.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contract  # noqa: E402
import discovery  # noqa: E402
import pages  # noqa: E402
import paths  # noqa: E402
import runner  # noqa: E402
import sources  # noqa: E402
from version import RELEASES, VERSION  # noqa: E402

DETECT_EVERY = 30  # seconds
VIEWS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "views")
OP = re.compile(r"^[a-z][a-z0-9_-]*$")


def view_for(s):
    """The URL of this source's view, if it has one: its own page first, else
    the page the hub ships for it."""
    page = (s.manifest or {}).get("page")
    if page:
        return f"/sources/{urllib.parse.quote(s.key, safe='')}/{page}"
    return f"/views/{s.name}/index.html" if os.path.isfile(os.path.join(VIEWS, s.name, "index.html")) else None


def order_tabs(loaded: list, page_list: list, names: list):
    """(the tabs in the order the page shows them, problems). Without a
    tab_order: working sources first, so the first tab has something to show,
    then the rest, then link pages, each in the order found. The names
    tab_order lists come first, in its order: a source by its key, else a page
    by its name. A source that is not working stays with the others at the
    end, listed or not. A name that is neither is a problem."""
    first = [{"source": s.key} for s in loaded if s.state in sources.FETCHABLE]
    rest = [{"source": s.key} for s in loaded if s.state not in sources.FETCHABLE]
    tabs = first + rest + [{"page": p["name"]} for p in page_list]
    keys = {s.key for s in loaded}
    names_of = {p["name"] for p in page_list}
    listed, problems = [], []
    for n in names:
        if n in keys:
            if {"source": n} in first:
                listed.append({"source": n})
        elif n in names_of:
            listed.append({"page": n})
        else:
            problems.append(f"{discovery.tilde(paths.hub_config_path())}: tab_order names {n}, which is no source or page")
    return listed + [t for t in tabs if t not in listed], problems


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Registry:
    def __init__(self):
        self._lock = threading.Lock()
        self._sources: dict = {}       # key -> Source
        self._order: list = []
        self._available: list = []
        self._problems: list = []
        self._pages: list = []
        self._tabs: list = []
        self._claude_dirs: list = []
        self._detected_at = None       # time.monotonic()
        self._cache: dict = {}         # (key, widget id) -> entry
        self._widget_locks: dict = {}

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            if not force and self._detected_at is not None \
                    and time.monotonic() - self._detected_at < DETECT_EVERY:
                return
            found, problems = discovery.find()
            resolved = sources.resolve(found)
            self._sources = {s.key: s for s in resolved}
            self._order = [s.key for s in resolved]
            self._available = discovery.not_installed()
            self._pages, page_problems = pages.find()
            _, port_problem = discovery.port_setting()
            self._claude_dirs, dir_problems = discovery.claude_dirs()
            names, order_problems = discovery.tab_order_setting()
            self._tabs, more = order_tabs(resolved, self._pages, names)
            self._problems = problems + page_problems + dir_problems + ([port_problem] if port_problem else []) \
                + order_problems + more
            self._detected_at = time.monotonic()

    def status(self) -> dict:
        with self._lock:
            loaded = [self._sources[k] for k in self._order]
            return {
                "hub": {"version": VERSION, "contract": contract.CONTRACT_VERSION,
                        "release_url": f"{RELEASES}/tag/v{VERSION}"},
                "sources": [{**s.public(), "view": view_for(s)} for s in loaded],
                "available": list(self._available),
                "pages": list(self._pages),
                "tabs": list(self._tabs),  # {"source": key} or {"page": name}, in the page's order
                "claude_dirs": list(self._claude_dirs),
                "problems": list(self._problems),
                # A reference is drawn as a link only when something can show it.
                "linkable": sorted({t for s in loaded if s.state in sources.FETCHABLE
                                    for t in s.manifest.get("shows", [])}
                                   & set(contract.entities()["types"])),
            }

    def entity(self, etype: str, eid: str):
        """(HTTP status, payload) for an entity page: one section from every
        source that shows this type. A source that knows nothing about the
        entity (an empty widgets list) is left out; one that fails is kept,
        with its error, so a missing section is never mistaken for "nothing
        to say"."""
        rule = contract.entities()["types"].get(etype)
        if rule is None:
            return 404, {"error": f"there is no entity type '{etype}'"}
        if not re.fullmatch(rule["id_pattern"], eid):
            return 400, {"error": f"{eid!r} is not a valid {etype} ID"}
        with self._lock:
            showers = [self._sources[k] for k in self._order
                       if self._sources[k].state in sources.FETCHABLE
                       and etype in self._sources[k].manifest.get("shows", [])]
        sections = []
        if showers:
            with ThreadPoolExecutor(max_workers=min(8, len(showers))) as pool:
                sections = list(pool.map(lambda s: self._show(s, etype, eid), showers))
        return 200, {"type": etype, "id": eid, "title": rule["title"],
                     "sections": [x for x in sections if x.get("widgets") or x.get("error")]}

    @staticmethod
    def _show(s, etype: str, eid: str) -> dict:
        section = {"source": s.key, "title": s.title}
        timeout = s.manifest.get("timeout_seconds", contract.DEFAULT_TIMEOUT)
        value, problem, stderr = runner.call(s.dir, ["show", etype, eid], timeout, s.name)
        if problem:
            section["error"] = {"problem": f"show {problem}", "details": sources.stderr_lines(stderr),
                                "at": _now_iso()}
            return section
        errors, _ = contract.show_problems(value, s.manifest["contract"])
        if errors:
            section["error"] = {"problem": "show printed output that breaks the contract",
                                "details": errors[:20], "at": _now_iso()}
            return section
        section["widgets"] = value["widgets"]
        return section

    def widget(self, key: str, widget_id: str, query=None, force: bool = False):
        """(HTTP status, payload) for one widget of one source; force fetches
        it again however fresh the last fetch is."""
        with self._lock:
            s = self._sources.get(key)
        if s is None:
            return 404, {"error": f"there is no source '{key}'"}
        if s.state not in sources.FETCHABLE:
            return 409, {"error": f"{s.title} is {s.state.replace('_', ' ')}: {s.reason}"}
        w = s.widget(widget_id)
        if w is None:
            return 404, {"error": f"{s.title} has no widget '{widget_id}'"}

        if w["kind"] == "search":
            if not query:
                return 200, {"kind": "search", "data": {"results": []}}
            return 200, self._fetch(s, w, ["--query", query])

        ttl = s.manifest.get("refresh_seconds", 300)
        lock = self._widget_locks.setdefault((key, widget_id), threading.Lock())
        with lock:  # one fetch at a time per widget; the rest wait and share it
            entry = self._cache.get((key, widget_id))
            if not force and entry and entry.get("good") and time.monotonic() - entry["good_at"] < ttl \
                    and not entry.get("error"):
                return 200, self._payload(entry)
            result = self._fetch(s, w, [])
            entry = entry or {}
            if "error" in result:
                entry["error"] = result["error"]
            else:
                entry = {"good": result, "good_at": time.monotonic()}
            self._cache[(key, widget_id)] = entry
            return 200, self._payload(entry)

    def page_dir(self, key: str):
        """The page/ folder of a working source that declares a page, which
        the server serves files from; None for any other source."""
        with self._lock:
            s = self._sources.get(key)
        if s is None or s.state not in sources.FETCHABLE or not (s.manifest or {}).get("page"):
            return None
        return os.path.join(s.dir, "page")

    def call(self, key: str, op: str, args: str):
        """(HTTP status, payload) for `run call <op> <args>`, which only a view
        uses. The answer is the view's to read, so the hub checks only that it
        is a JSON object."""
        with self._lock:
            s = self._sources.get(key)
        if s is None:
            return 404, {"error": f"there is no source '{key}'"}
        if view_for(s) is None:
            return 404, {"error": f"the hub has no page for {s.title}, so nothing calls it"}
        if s.state not in sources.FETCHABLE:
            return 409, {"error": f"{s.title} is {s.state.replace('_', ' ')}: {s.reason}"}
        if not OP.match(op):
            return 400, {"error": f"{op!r} is not a call name"}
        if len(args.encode()) > contract.MAX_CALL_ARGS:
            return 400, {"error": "the arguments are too long"}
        try:
            parsed = json.loads(args)
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict):
            return 400, {"error": "the arguments must be a JSON object"}
        value, problem, stderr = runner.call(s.dir, ["call", op, args], contract.CALL_TIMEOUT, s.name)
        if problem:
            return 502, {"error": f"{s.title}: call {op} {problem}", "details": sources.stderr_lines(stderr)}
        if not isinstance(value, dict):
            return 502, {"error": f"{s.title}: call {op} printed JSON that is not an object"}
        return 200, value

    @staticmethod
    def _payload(entry: dict) -> dict:
        payload = dict(entry.get("good") or {})
        if entry.get("error"):
            payload["error"] = entry["error"]
        return payload

    @staticmethod
    def _fetch(s, w: dict, extra: list) -> dict:
        timeout = s.manifest.get("timeout_seconds", contract.DEFAULT_TIMEOUT)
        value, problem, stderr = runner.call(s.dir, ["fetch", w["id"], *extra], timeout, s.name)
        if problem:
            return {"error": {"problem": f"fetch {problem}", "details": sources.stderr_lines(stderr),
                              "at": _now_iso()}}
        errors, _ = contract.fetch_problems(value, w["kind"], s.manifest["contract"])
        if errors:
            return {"error": {"problem": "fetch printed output that breaks the contract",
                              "details": errors[:20], "at": _now_iso()}}
        value["fetched_at"] = _now_iso()
        return value
