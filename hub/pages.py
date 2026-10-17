#!/usr/bin/env python3
"""Link pages: tabs whose content is a JSON file of links, in sections.

A link page runs nothing. It is for what the hub cannot fetch, or should not:
a team's Grafana alert views, runbooks, dashboards. Each `*.json` file is one
page, named after the file, found in

1. the user's folder, ~/.config/agent-toolworks/pages/;
2. the folders named in hub.json's page_dirs, in the order listed -- a git
   checkout of a team's pages, say.

A file that breaks the format is a problem the page shows, with every fault
named; it never stops the hub, and never becomes half a page.

{
  "title": "Grafana alerts",
  "description": "optional line under the title",
  "sections": [
    {"title": "Firing", "links": [
      {"title": "All firing alerts", "url": "https://grafana.example.com/alerting/list?state=firing",
       "description": "optional"}
    ]}
  ]
}

A url is http(s), opened in a new browser tab, or `#/...`, a page of the hub
itself (#/source/code-search, #/page/runbooks).
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discovery  # noqa: E402
import paths  # noqa: E402

NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
URL = re.compile(r"^(https?://[^\s]+|#/[^\s]*)$")
PAGE_KEYS = {"title": True, "description": False, "sections": True}
SECTION_KEYS = {"title": True, "links": True}
LINK_KEYS = {"title": True, "url": True, "description": False}


def _fields(value, keys: dict, where: str, errors: list) -> bool:
    """Check an object's keys: every required one present, nothing else, and
    every value a non-empty string except the lists the caller checks."""
    if not isinstance(value, dict):
        errors.append(f"{where}: expected an object")
        return False
    for k in sorted(value):
        if k not in keys:
            errors.append(f"{where}: unknown field '{k}'")
    for k, required in keys.items():
        if k not in value:
            if required:
                errors.append(f"{where}: '{k}' is missing")
        elif k not in ("sections", "links") and not (isinstance(value[k], str) and value[k].strip()):
            errors.append(f"{where}.{k}: expected a non-empty string")
    return True


def check(doc) -> list:
    """Every fault in a page file, or [] when it meets the format."""
    errors = []
    if not _fields(doc, PAGE_KEYS, "$", errors):
        return errors
    sections = doc.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("$.sections: expected a non-empty list")
        return errors
    for i, sec in enumerate(sections):
        where = f"$.sections[{i}]"
        if not _fields(sec, SECTION_KEYS, where, errors):
            continue
        links = sec.get("links")
        if not isinstance(links, list) or not links:
            errors.append(f"{where}.links: expected a non-empty list")
            continue
        for j, link in enumerate(links):
            lw = f"{where}.links[{j}]"
            if _fields(link, LINK_KEYS, lw, errors) and isinstance(link.get("url"), str) \
                    and not URL.match(link["url"]):
                errors.append(f"{lw}.url: {link['url']!r} is not an http(s) URL or a #/ hub page")
    return errors


def page_dirs():
    """(the folders hub.json's page_dirs names, problems)."""
    return discovery.folder_setting("page_dirs")


def find():
    """(every page that meets the format, problems). A name already taken by
    an earlier folder is a problem, as with sources."""
    dirs, problems = page_dirs()
    pages, seen = [], {}
    for root in [paths.user_pages_dir()] + dirs:
        try:
            names = sorted(n for n in os.listdir(root) if n.endswith(".json") and not n.startswith("."))
        except OSError:
            continue
        for n in names:
            path = discovery.tilde(os.path.join(root, n))
            name = n[:-len(".json")]
            if not NAME.match(name):
                problems.append(f"{path}: a page file's name must be lowercase letters, digits and dashes")
                continue
            if name in seen:
                problems.append(f"{path}: the page name '{name}' is already used by {seen[name]}")
                continue
            try:
                with open(os.path.join(root, n), encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, ValueError) as e:
                problems.append(f"{path} could not be read ({e})")
                continue
            errors = check(doc)
            if errors:
                problems.append(f"{path} breaks the page format: " + "; ".join(errors[:10]))
                continue
            seen[name] = path
            pages.append({"name": name, "file": path, **doc})  # file: shown as the badge's title
    return pages, problems
