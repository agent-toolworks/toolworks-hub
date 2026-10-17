#!/usr/bin/env python3
"""hub check-source: does a source folder meet the contract?

Run it in a source's own test suite, the way token-save runs scripts/verify.
It checks what the hub relies on, in the order the hub relies on it: the
manifest, the `run` program, `run detect`, then -- only when detect says the
source is working or stale -- `run fetch` for every widget and `run show` for every
entity it was given an ID for.

A source that is not set up on this machine still passes. Its fetch and show
checks are reported as SKIP, never as PASS, so a green run on a machine
without the tool does not claim more than it checked. To check them in CI,
give the source the environment it needs (token-save's verify points
CLAUDE_CONFIG_DIR at fixtures for the same reason).

Exit status: 0 when nothing failed, 1 when something did, 2 for bad usage.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contract  # noqa: E402
import runner  # noqa: E402


class Report:
    COLOURS = {"PASS": "\033[32m", "FAIL": "\033[31m", "WARN": "\033[33m", "SKIP": "\033[2m"}

    def __init__(self):
        self.counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
        self.tty = sys.stdout.isatty()

    def add(self, level: str, what: str, details=()) -> None:
        self.counts[level] += 1
        tag = f"{self.COLOURS[level]}{level}\033[0m" if self.tty else level
        print(f"  {tag}  {what}")
        for line in details:
            print(f"        {line}")

    def finish(self) -> int:
        c = self.counts
        print(f"\n{c['PASS']} passed, {c['FAIL']} failed, {c['WARN']} warnings, {c['SKIP']} skipped")
        return 1 if c["FAIL"] else 0


def _stderr_lines(stderr: str) -> list:
    lines = [l for l in stderr.splitlines() if l.strip()][-5:]
    return [f"stderr: {l}" for l in lines]


def check_manifest(src: str, r: Report):
    path = os.path.join(src, "source.json")
    if not os.path.isfile(path):
        r.add("FAIL", "manifest", ["there is no source.json in the folder"])
        return None
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except (ValueError, UnicodeDecodeError) as e:
        r.add("FAIL", "manifest", [f"source.json is not valid JSON ({e})"])
        return None
    if isinstance(m, dict) and isinstance(m.get("contract"), int) \
            and not isinstance(m["contract"], bool) and m["contract"] != contract.CONTRACT_VERSION:
        r.add("FAIL", "manifest", [f"written for contract {m['contract']}; this hub implements "
                                   f"contract {contract.CONTRACT_VERSION}, so it would show the "
                                   f"source as incompatible"])
        return None

    errors = contract.validate(m, "manifest.schema.json")
    if not isinstance(m, dict):
        r.add("FAIL", "manifest", errors)
        return None

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
    if errors:
        r.add("FAIL", "manifest", errors)
        return None

    r.add("PASS", f"manifest: {m['name']} {m['version']}, {len(widgets)} widget(s)")
    known = contract.entities()["types"]
    for t in m.get("shows", []):
        if t not in known:
            r.add("WARN", f"manifest: shows '{t}'",
                  ["contract/entities.json does not define it, so the hub will never ask for it"])
    return m


def check_run(src: str, r: Report) -> bool:
    path = os.path.join(src, "run")
    if not os.path.isfile(path):
        r.add("FAIL", "run", ["there is no `run` program in the folder"])
        return False
    if not os.access(path, os.X_OK):
        r.add("FAIL", "run", ["`run` is not executable (chmod +x run)"])
        return False
    r.add("PASS", "run: present and executable")
    return True


def check_detect(src: str, r: Report):
    value, problem, stderr = runner.call(src, ["detect"], contract.DETECT_TIMEOUT)
    if problem:
        r.add("FAIL", f"detect: {problem}", _stderr_lines(stderr))
        return None
    errors = contract.validate(value, "detect.schema.json")
    if errors:
        r.add("FAIL", "detect: output breaks detect.schema.json", errors)
        return None
    reason = f" ({value['reason']})" if "reason" in value else ""
    r.add("PASS", f"detect: {value['state']}{reason}")
    return value


def _shape_problems(kind: str, data, path: str) -> tuple:
    """What the schemas cannot say: table rows keyed by real columns, and refs
    whose entity type is known and whose ID fits its rule. Returns
    (errors, warnings)."""
    errors, warnings = [], []
    refs = []
    if kind == "table":
        cols = [c["id"] for c in data["columns"]]
        for dup in sorted({c for c in cols if cols.count(c) > 1}):
            errors.append(f"{path}.columns: id '{dup}' is used more than once")
        for i, row in enumerate(data["rows"]):
            for key, cell in row.items():
                if key not in cols:
                    errors.append(f"{path}.rows[{i}]: '{key}' is not one of the columns")
                if isinstance(cell, dict):
                    refs.append((f"{path}.rows[{i}].{key}", cell))
    elif kind in ("list", "search"):
        field = "items" if kind == "list" else "results"
        for i, item in enumerate(data[field]):
            if "ref" in item:
                refs.append((f"{path}.{field}[{i}].ref", item["ref"]))

    types = contract.entities()["types"]
    for where, ref in refs:
        t = types.get(ref["ref"])
        if t is None:
            warnings.append(f"{where}: entity type '{ref['ref']}' is not in contract/entities.json, "
                            f"so the hub shows it as plain text")
        elif not re.fullmatch(t["id_pattern"], ref["id"]):
            errors.append(f"{where}: {ref['id']!r} is not a valid {ref['ref']} ID")
    return errors, warnings


def check_fetch(src: str, widget: dict, timeout: int, r: Report) -> None:
    label = f"fetch {widget['id']}"
    args = ["fetch", widget["id"]]
    if widget["kind"] == "search":
        if "example_query" not in widget:
            r.add("SKIP", label, ["a search widget is checked only with example_query in the manifest"])
            return
        args += ["--query", widget["example_query"]]
    value, problem, stderr = runner.call(src, args, timeout)
    if problem:
        r.add("FAIL", f"{label}: {problem}", _stderr_lines(stderr))
        return
    warnings = []
    if isinstance(value, dict) and value.get("kind", widget["kind"]) != widget["kind"]:
        # Reported alone: the data was checked against the printed kind, so
        # its errors would describe the wrong shape.
        errors = [f"$.kind: the manifest says '{widget['kind']}', fetch printed {value['kind']!r}"]
    else:
        errors = contract.validate(value, "fetch.schema.json")
    if not errors:
        errors, warnings = _shape_problems(value["kind"], value["data"], "$.data")
    r.add("FAIL" if errors else "PASS", label, errors)
    for w in warnings:
        r.add("WARN", label, [w])


def check_show(src: str, manifest: dict, given: list, timeout: int, r: Report) -> None:
    shows = manifest.get("shows", [])
    types = contract.entities()["types"]
    for etype, eid in given:
        label = f"show {etype} {eid}"
        if etype not in shows:
            r.add("FAIL", label, [f"the manifest does not list '{etype}' in shows"])
            continue
        rule = types.get(etype)
        if rule is not None and not re.fullmatch(rule["id_pattern"], eid):
            r.add("FAIL", label, [f"{eid!r} is not a valid {etype} ID, so the hub would never ask for it"])
            continue
        value, problem, stderr = runner.call(src, ["show", etype, eid], timeout)
        if problem:
            r.add("FAIL", f"{label}: {problem}", _stderr_lines(stderr))
            continue
        errors = contract.validate(value, "show.schema.json")
        warnings = []
        if not errors:
            ids = [w["id"] for w in value["widgets"]]
            for dup in sorted({i for i in ids if ids.count(i) > 1}):
                errors.append(f"$.widgets: id '{dup}' is used more than once")
            for i, w in enumerate(value["widgets"]):
                e, wn = _shape_problems(w["kind"], w["data"], f"$.widgets[{i}].data")
                errors += e
                warnings += wn
        r.add("FAIL" if errors else "PASS", label, errors)
        for w in warnings:
            r.add("WARN", label, [w])
    checked = {t for t, _ in given}
    for etype in shows:
        if etype not in checked:
            r.add("SKIP", f"show {etype}", [f"pass --entity {etype}=<id> to check it"])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="hub check-source",
                                description="Check a source folder against the hub's source contract.")
    p.add_argument("source", help="the source folder")
    p.add_argument("--entity", action="append", default=[], metavar="TYPE=ID",
                   help="also check `run show TYPE ID`; repeatable")
    a = p.parse_args(argv)

    given = []
    for e in a.entity:
        etype, sep, eid = e.partition("=")
        if not (sep and etype and eid):
            p.error(f"--entity wants TYPE=ID, got {e!r}")
        given.append((etype, eid))
    src = os.path.abspath(a.source)
    if not os.path.isdir(src):
        p.error(f"{a.source} is not a folder")

    r = Report()
    print(f"Checking {src} against source contract {contract.CONTRACT_VERSION}\n")
    manifest = check_manifest(src, r)
    if manifest is None or not check_run(src, r):
        return r.finish()
    detected = check_detect(src, r)
    if detected is None:
        return r.finish()
    if detected["state"] not in ("working", "stale"):  # stale still fetches
        why = [f"detect reports {detected['state']}: {detected['reason']}"]
        if "fix" in detected:
            why.append(f"fix: {detected['fix']}")
        r.add("SKIP", "fetch and show", why)
        return r.finish()

    timeout = manifest.get("timeout_seconds", contract.DEFAULT_TIMEOUT)
    for widget in manifest.get("widgets", []):
        check_fetch(src, widget, timeout, r)
    check_show(src, manifest, given, timeout, r)
    return r.finish()


if __name__ == "__main__":
    sys.exit(main())
