#!/usr/bin/env python3
"""The hub's live view of its sources, and the last good result of each widget.

Sources are found and detected again at most every DETECT_EVERY seconds, when
someone asks for the status. A widget is fetched again once its source's
refresh_seconds have passed. When a fetch fails, the last good result is kept
and served with the failure beside it, so a source that breaks for a minute
shows old data with a warning rather than an empty panel.

Search widgets are fetched on every query and never cached.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contract  # noqa: E402
import discovery  # noqa: E402
import runner  # noqa: E402
import sources  # noqa: E402
from version import VERSION  # noqa: E402

DETECT_EVERY = 30  # seconds


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Registry:
    def __init__(self):
        self._lock = threading.Lock()
        self._sources: dict = {}       # key -> Source
        self._order: list = []
        self._available: list = []
        self._problems: list = []
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
            self._problems = problems
            self._detected_at = time.monotonic()

    def status(self) -> dict:
        with self._lock:
            return {
                "hub": {"version": VERSION, "contract": contract.CONTRACT_VERSION},
                "sources": [self._sources[k].public() for k in self._order],
                "available": list(self._available),
                "problems": list(self._problems),
            }

    def widget(self, key: str, widget_id: str, query=None):
        """(HTTP status, payload) for one widget of one source."""
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
            if entry and entry.get("good") and time.monotonic() - entry["good_at"] < ttl \
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

    @staticmethod
    def _payload(entry: dict) -> dict:
        payload = dict(entry.get("good") or {})
        if entry.get("error"):
            payload["error"] = entry["error"]
        return payload

    @staticmethod
    def _fetch(s, w: dict, extra: list) -> dict:
        timeout = s.manifest.get("timeout_seconds", contract.DEFAULT_TIMEOUT)
        value, problem, stderr = runner.call(s.dir, ["fetch", w["id"], *extra], timeout)
        if problem:
            return {"error": {"problem": f"fetch {problem}", "details": sources.stderr_lines(stderr),
                              "at": _now_iso()}}
        errors, _ = contract.fetch_problems(value, w["kind"])
        if errors:
            return {"error": {"problem": "fetch printed output that breaks the contract",
                              "details": errors[:20], "at": _now_iso()}}
        value["fetched_at"] = _now_iso()
        return value
