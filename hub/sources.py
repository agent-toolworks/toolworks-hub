#!/usr/bin/env python3
"""A source as the hub sees it: where it was found, its manifest, its state.

Most states come from the source's own `run detect`. The hub works out three
itself, because in each case there is nothing it can trust to ask:

- not_installed: a catalogue plugin that is not installed has no source at all
  (discovery.not_installed lists those; they never become a Source).
- incompatible: a manifest written for another contract version. The hub
  cannot know what that source's output means, so it does not run it.
- broken: a manifest that breaks the contract, a missing or non-executable
  `run`, a name an earlier source already claimed, or a detect that fails.
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contract  # noqa: E402
import runner  # noqa: E402

STATES = ("not_installed", "not_set_up", "off", "incompatible", "stale", "broken", "working")
FETCHABLE = ("working", "stale")


def stderr_lines(stderr: str, keep: int = 5) -> list:
    lines = [l for l in stderr.splitlines() if l.strip()][-keep:]
    return [f"stderr: {l}" for l in lines]


def load_manifest(src: str):
    """(manifest, [], None) when source.json meets the contract;
    (None, errors, None) when it does not; (None, [], n) when it was written
    for contract n, which this hub does not implement."""
    path = os.path.join(src, "source.json")
    if not os.path.isfile(path):
        return None, ["there is no source.json in the folder"], None
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except (ValueError, UnicodeDecodeError) as e:
        return None, [f"source.json is not valid JSON ({e})"], None
    if isinstance(m, dict) and isinstance(m.get("contract"), int) \
            and not isinstance(m["contract"], bool) and m["contract"] != contract.CONTRACT_VERSION:
        return None, [], m["contract"]

    errors = contract.validate(m, "manifest.schema.json")
    if not isinstance(m, dict):
        return None, errors, None
    widgets = [w for w in m.get("widgets", []) if isinstance(w, dict)]
    ids = [w.get("id") for w in widgets]
    for dup in sorted({i for i in ids if isinstance(i, str) and ids.count(i) > 1}):
        errors.append(f"$.widgets: id '{dup}' is used more than once")
    page = m.get("page")
    if isinstance(page, str):
        root = os.path.realpath(src)
        full = os.path.realpath(os.path.join(src, page))
        if not full.startswith(root + os.sep):
            errors.append(f"$.page: {page!r} points outside the source folder")
        elif not os.path.isfile(full):
            errors.append(f"$.page: {page!r} does not exist")
    return (None, errors, None) if errors else (m, [], None)


def run_problem(src: str) -> Optional[str]:
    path = os.path.join(src, "run")
    if not os.path.isfile(path):
        return "there is no `run` program in the folder"
    if not os.access(path, os.X_OK):
        return "`run` is not executable (chmod +x run)"
    return None


@dataclass
class Source:
    key: str            # unique among the hub's sources; used in URLs
    name: str
    dir: str
    origin: str
    plugin: Optional[str] = None
    manifest: Optional[dict] = None
    state: str = "broken"
    reason: str = ""
    fix: Optional[str] = None
    details: list = field(default_factory=list)
    tool_version: Optional[str] = None

    @property
    def title(self) -> str:
        return self.manifest["title"] if self.manifest else self.name

    def widget(self, widget_id: str) -> Optional[dict]:
        for w in (self.manifest or {}).get("widgets", []):
            if w["id"] == widget_id:
                return w
        return None

    def public(self) -> dict:
        m = self.manifest or {}
        return {
            "key": self.key, "name": self.name, "title": self.title,
            "origin": self.origin, "plugin": self.plugin, "dir": self.dir,
            "state": self.state, "reason": self.reason, "fix": self.fix,
            "details": self.details, "tool_version": self.tool_version,
            "version": m.get("version"), "network": m.get("network", False),
            "refresh_seconds": m.get("refresh_seconds", 300),
            "widgets": [{"id": w["id"], "kind": w["kind"], "title": w["title"]}
                        for w in m.get("widgets", [])],
            "shows": m.get("shows", []),
        }


def _load(found) -> Source:
    """A Source with its manifest checked. state is None when it is ready for
    detect, and final otherwise."""
    m, errors, other = load_manifest(found.dir)
    name = m["name"] if m else os.path.basename(found.dir)
    s = Source(key=name, name=name, dir=found.dir, origin=found.origin,
               plugin=found.plugin, manifest=m, state=None)
    check = f"hub check-source {found.dir}"
    if other is not None:
        s.state = "incompatible"
        s.reason = (f"written for source contract {other}; this hub implements "
                    f"contract {contract.CONTRACT_VERSION}")
        s.fix = "update the hub" if other > contract.CONTRACT_VERSION else "update the source"
    elif m is None:
        s.state, s.reason, s.fix, s.details = "broken", "source.json breaks the contract", check, errors
    else:
        problem = run_problem(found.dir)
        if problem:
            s.state, s.reason, s.fix = "broken", problem, check
    return s


def detect(s: Source) -> None:
    value, problem, stderr = runner.call(s.dir, ["detect"], contract.DETECT_TIMEOUT)
    if problem:
        s.state, s.reason, s.details = "broken", f"detect {problem}", stderr_lines(stderr)
        s.fix = f"hub check-source {s.dir}"
        return
    errors = contract.validate(value, "detect.schema.json")
    if errors:
        s.state, s.reason, s.details = "broken", "detect printed output that breaks the contract", errors
        s.fix = f"hub check-source {s.dir}"
        return
    s.state = value["state"]
    s.reason = value.get("reason", "")
    s.fix = value.get("fix")
    s.tool_version = value.get("tool_version")


def resolve(found: list) -> list:
    """Every found folder as a Source with its state. Names are claimed in
    discovery order (built-in, then plugins, then the user's), and a later
    source with a claimed name is broken: two sources answering to one name
    would make every link to it ambiguous."""
    out, claimed = [], {}
    for f in found:
        s = _load(f)
        if s.name in claimed:
            first = claimed[s.name]
            s.key = f"{s.name}~{sum(1 for o in out if o.name == s.name) + 1}"
            s.state = "broken"
            s.reason = f"the name '{s.name}' is already used by the {first.origin} source in {first.dir}"
            s.fix = "rename this source in its source.json"
            s.details = []
        else:
            claimed[s.name] = s
        out.append(s)
    pending = [s for s in out if s.state is None]
    if pending:
        with ThreadPoolExecutor(max_workers=min(8, len(pending))) as pool:
            list(pool.map(detect, pending))
    return out
