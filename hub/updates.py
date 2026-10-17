#!/usr/bin/env python3
"""Whether a newer hub has been released: the one network call the hub makes.

The running server asks its own git checkout's origin for its release tags --
`git ls-remote --tags --refs origin 'v*'`, with the user's own git
credentials, reading tag names and sending nothing else -- once at start and
every CHECK_EVERY seconds after, in a thread of its own so no page waits on
it. A failure (offline, no access, not a git checkout) is remembered for
RETRY_AFTER and shown as nothing: an update check that cannot run is not an
error worth a banner.

`"check_updates": false` in hub.json turns it off. HUB_UPDATE_REMOTE names
another remote to ask (scripts/verify points it at a local repo).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discovery  # noqa: E402
import paths  # noqa: E402
from version import RELEASES, VERSION  # noqa: E402

CHECK_EVERY = 6 * 3600
RETRY_AFTER = 3600
TIMEOUT = 15
TAG = re.compile(r"refs/tags/v(\d+)\.(\d+)\.(\d+)$")


def version_key(v: str):
    return tuple(int(x) for x in v.split("."))


def latest_tag(remote=None):
    """The highest vX.Y.Z tag on the remote, as "X.Y.Z", or None when the
    remote cannot be read or has none."""
    remote = remote or os.environ.get("HUB_UPDATE_REMOTE") or "origin"
    try:
        p = subprocess.run(["git", "-C", paths.ROOT, "ls-remote", "--tags", "--refs", remote, "v*"],
                           capture_output=True, text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    found = [m.groups() for m in (TAG.search(line) for line in p.stdout.splitlines()) if m]
    return ".".join(max(found, key=lambda g: tuple(map(int, g)))) if found else None


def enabled() -> bool:
    doc, _ = discovery._read_json(paths.hub_config_path())
    return not (isinstance(doc, dict) and doc.get("check_updates") is False)


class Checker:
    """The newest release, checked in the background."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest = None
        self._next = 0.0
        self._running = False

    def poke(self) -> None:
        """Start a check when one is due. Never blocks."""
        with self._lock:
            if self._running or time.monotonic() < self._next or not enabled():
                return
            self._running = True
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self) -> None:
        latest = latest_tag()
        with self._lock:
            if latest:
                self._latest = latest
            self._next = time.monotonic() + (CHECK_EVERY if latest else RETRY_AFTER)
            self._running = False

    def status(self) -> dict:
        """{"latest", "update", "url"} for the page; update is True only when
        a released version is newer than this one."""
        with self._lock:
            latest = self._latest
        newer = bool(latest) and enabled() and version_key(latest) > version_key(VERSION)
        return {"latest": latest, "update": newer, "url": f"{RELEASES}/tag/v{latest}" if newer else None}
