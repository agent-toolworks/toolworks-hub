#!/usr/bin/env python3
"""Where the hub reads and writes, each from the variable the tool that owns
it reads, so the hub and the tool never disagree about where things are.

- Claude Code's folder: $CLAUDE_CONFIG_DIR, else ~/.claude -- the order
  token-save documents for history.db.
- The hub's settings and the user's sources: $XDG_CONFIG_HOME/agent-toolworks,
  else ~/.config/agent-toolworks, beside repo-fleet's ~/.config/repo-fleet.
- The hub's own settings file: hub.json in that folder.
- Each source's private settings: source-config/<name>/ in that folder, kept
  out of the source's own folder so a source shared in a git repo never holds
  one person's settings.
- The hub's cache, server.json and server.log: $XDG_CACHE_HOME/agent-toolworks,
  else ~/.cache/agent-toolworks.
"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def claude_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "agent-toolworks")


def cache_dir() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "agent-toolworks")


def user_sources_dir() -> str:
    return os.path.join(config_dir(), "sources")


def hub_config_path() -> str:
    return os.path.join(config_dir(), "hub.json")


def source_config_dir(name: str) -> str:
    return os.path.join(config_dir(), "source-config", name)


def builtin_sources_dir() -> str:
    return os.path.join(ROOT, "sources")
