#!/usr/bin/env python3
"""Where the hub finds sources, and which components are not installed.

Sources come from these places and nowhere else; the hub never downloads one:

1. inside the hub, under sources/ (the Claude data source);
2. a hub-source/ folder inside each installed Claude Code plugin, found by the
   plugin's installPath in installed_plugins.json -- never by searching the
   PATH, because plugins run from Claude Code's cache;
3. the user's folder, ~/.config/agent-toolworks/sources/*/;
4. the folders named in ~/.config/agent-toolworks/hub.json's source_dirs, in
   the order listed -- a git checkout of a team's sources, say. A folder with
   a source.json is one source; any other folder holds one per subfolder.

A plugin that is not installed has no folder, so it has no source, and nothing
else needs to know it is absent. To say what is missing, the hub reads the
agent-toolworks catalogue Claude Code keeps on disk, without a network call.

agent-teller is a separate tool that runs separately. It is never listed,
whichever marketplace it comes from, and neither is the hub itself.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paths  # noqa: E402

MARKETPLACE = "agent-toolworks"
EXCLUDED = frozenset({"agent-teller", "toolworks-hub"})


@dataclass
class Found:
    dir: str
    origin: str                   # "built-in", "plugin", "user" or "folder"
    plugin: Optional[str] = None  # "token-save@agent-toolworks"


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, f"could not read {path} ({e})"


def plugin_records():
    """({"name@marketplace": record}, problem). The user-scope record wins
    when a plugin is installed in several scopes."""
    path = os.path.join(paths.claude_dir(), "plugins", "installed_plugins.json")
    doc, problem = _read_json(path)
    if not isinstance(doc, dict):
        return {}, problem
    records = {}
    for key, entries in (doc.get("plugins") or {}).items():
        if isinstance(entries, list):
            entries = [e for e in entries if isinstance(e, dict)]
            if entries:
                records[key] = next((e for e in entries if e.get("scope") == "user"), entries[0])
    return records, problem


def _subdirs(root: str) -> list:
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [os.path.join(root, n) for n in names
            if not n.startswith(".") and os.path.isdir(os.path.join(root, n))]


SETTINGS = ("source_dirs", "page_dirs", "port")
DEFAULT_PORT = 7437


def tilde(path: str) -> str:
    """A path as a person writes it: under the home folder, with ~."""
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def source_dirs():
    """(the folders hub.json's source_dirs names, problems). Problems with
    hub.json as a whole, such as an unknown setting, are reported here."""
    return folder_setting("source_dirs", whole_file=True)


def port_setting():
    """(the port hub.json names, else DEFAULT_PORT; problem or None). 0 means
    a free port each time."""
    path = paths.hub_config_path()
    doc, _ = _read_json(path)
    value = doc.get("port", DEFAULT_PORT) if isinstance(doc, dict) else DEFAULT_PORT
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 65535:
        return DEFAULT_PORT, f"{tilde(path)}: port must be a number from 0 to 65535; using {DEFAULT_PORT}"
    return value, None


def folder_setting(key: str, whole_file: bool = False):
    """(the folders a hub.json setting names, problems). `~` is expanded, and
    a relative path is taken from the folder hub.json is in."""
    full_path = paths.hub_config_path()
    doc, problem = _read_json(full_path)
    if doc is None:
        return [], [problem] if problem and whole_file else []
    path = tilde(full_path)
    if not isinstance(doc, dict):
        return [], [f"{path} is not a JSON object"] if whole_file else []
    problems = [f"{path}: unknown setting '{k}'" for k in sorted(doc)
                if k not in SETTINGS] if whole_file else []
    dirs = doc.get(key, [])
    if not isinstance(dirs, list) or not all(isinstance(d, str) and d for d in dirs):
        return [], problems + [f"{path}: {key} must be a list of folder paths"]
    out = []
    for d in dirs:
        full = os.path.join(os.path.dirname(full_path), os.path.expanduser(d))
        if os.path.isdir(full):
            out.append(os.path.normpath(full))
        else:
            problems.append(f"{path}: {key} names {d}, which is not a folder")
    return out, problems


def find():
    """(every source folder, in the order names are claimed, problems)."""
    found = [Found(d, "built-in") for d in _subdirs(paths.builtin_sources_dir())]
    records, problem = plugin_records()
    problems = [problem] if problem else []
    for key in sorted(records):
        if key.partition("@")[0] in EXCLUDED:
            continue
        install = records[key].get("installPath")
        if isinstance(install, str) and os.path.isdir(os.path.join(install, "hub-source")):
            found.append(Found(os.path.join(install, "hub-source"), "plugin", key))
    found += [Found(d, "user") for d in _subdirs(paths.user_sources_dir())]
    dirs, more = source_dirs()
    problems += more
    for root in dirs:
        one = os.path.isfile(os.path.join(root, "source.json"))
        found += [Found(d, "folder") for d in ([root] if one else _subdirs(root))]
    return found, problems


def catalogue() -> list:
    """The plugins the agent-toolworks marketplace lists, from Claude Code's
    copy on disk. Empty when that marketplace was never added."""
    known, _ = _read_json(os.path.join(paths.claude_dir(), "plugins", "known_marketplaces.json"))
    entry = known.get(MARKETPLACE) if isinstance(known, dict) else None
    location = entry.get("installLocation") if isinstance(entry, dict) else None
    if not isinstance(location, str):
        location = os.path.join(paths.claude_dir(), "plugins", "marketplaces", MARKETPLACE)
    doc, _ = _read_json(os.path.join(location, ".claude-plugin", "marketplace.json"))
    plugins = doc.get("plugins") if isinstance(doc, dict) else None
    return [p for p in plugins or [] if isinstance(p, dict) and isinstance(p.get("name"), str)]


def not_installed() -> list:
    records, _ = plugin_records()
    out = []
    for p in catalogue():
        name = p["name"]
        if name in EXCLUDED or f"{name}@{MARKETPLACE}" in records:
            continue
        out.append({
            "name": name,
            "description": p.get("description") if isinstance(p.get("description"), str) else "",
            "homepage": p.get("homepage") if isinstance(p.get("homepage"), str) else None,
            "install": f"/plugin install {name}@{MARKETPLACE}",
        })
    return out
