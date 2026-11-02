#!/usr/bin/env python3
"""hub status: every source the hub finds and the state of each, without
starting the server. --json prints what the server's /api/status returns."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discovery  # noqa: E402
from registry import Registry  # noqa: E402

LABELS = {"working": "working", "stale": "update waiting", "not_set_up": "not set up",
          "off": "off", "incompatible": "incompatible", "broken": "broken"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="hub status", description=__doc__)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    registry = Registry()
    registry.refresh(force=True)
    st = registry.status()
    if a.json:
        print(json.dumps(st, indent=2))
        return 0

    print(f"toolworks-hub {st['hub']['version']}, source contract {st['hub']['contract']}\n")
    print("CLAUDE CODE FOLDERS")
    for d in st["claude_dirs"]:
        print(f"  {discovery.tilde(d)}")
    print()
    # Sources and pages in the order the page's tabs show them.
    place = {next(iter(t.items())): i for i, t in enumerate(st["tabs"])}
    print("TABS")
    print(f"  {', '.join(t.get('source') or t.get('page') for t in st['tabs']) or '(none)'}\n")
    width = max([len(s["key"]) for s in st["sources"]] + [8])
    print("SOURCES")
    for s in sorted(st["sources"], key=lambda s: place[("source", s["key"])]):
        line = f"  {s['key']:<{width}}  {s['origin']:<8}  {LABELS.get(s['state'], s['state'])}"
        print(f"{line}  ({s['reason']})" if s["reason"] else line)
        if s["fix"]:
            print(f"  {'':<{width}}  {'':<8}  fix: {s['fix']}")
        for d in s["details"]:
            print(f"  {'':<{width}}  {'':<8}  {d}")
    if st["pages"]:
        print("\nPAGES")
        w = max(len(x["name"]) for x in st["pages"])
        for x in sorted(st["pages"], key=lambda x: place[("page", x["name"])]):
            print(f"  {x['name']:<{w}}  {x['title']}  ({sum(len(s['links']) for s in x['sections'])} links)")
    if st["available"]:
        print("\nNOT INSTALLED")
        w = max(len(x["name"]) for x in st["available"])
        for x in st["available"]:
            print(f"  {x['name']:<{w}}  {x['install']}")
    for problem in st["problems"]:
        print(f"\nproblem: {problem}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
