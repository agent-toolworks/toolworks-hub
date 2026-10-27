#!/usr/bin/env python3
"""Claude Code sessions, as the hub's page shows them in every session table
and on the session page.

A table whose rows link to sessions (a `session` reference cell) is a session
table, whichever source printed it. The hub adds what it knows about each of
those sessions to the table's payload, so every source's session table shows
the same Status, Title and Open columns without each source working them out:

- status: from `claude agents --json --all` (claude_bg.list_sessions), the
          live state of an interactive or background session; a session it
          does not list has `ended`.
- title:  the session's own records in its transcript, the last of each:
          custom-title (a /rename), else ai-title, else agent-name; else the
          name `claude agents` gives.
- links:  the claude.ai session it is bridged to (a bridge-session record, or
          a background session's jobs/<id>/state.json), as https and claude://.

Opening a session (open_session) runs Claude Code on this machine:
`claude --desktop --resume ID` for the app, and a terminal window running
`claude --resume ID` (`claude attach SHORT_ID` for a live background
session). Both run in the session's folder, on its Claude Code folder.

The transcript (transcript) is the conversation's text: what was typed, what
Claude answered, and the name of each tool it used with a short summary of
what it was used on. Thinking, tool results, subagents' records and Claude
Code's own records are left out.
"""
from __future__ import annotations

import json
import os
import platform
import pty
import re
import select
import shlex
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import claude_bg  # noqa: E402
import discovery  # noqa: E402
import paths  # noqa: E402

SESSION_ID = claude_bg.SESSION_ID
HEAD_BYTES = 256 * 1024
TAIL_BYTES = 512 * 1024
LIVE_SECONDS = 5        # one `claude agents` listing serves the tables of one page
OPEN_WAIT = 10          # seconds `claude --desktop` gets to say it failed
TEXT_LIMIT = 20000      # characters of one message
SUMMARY_LIMIT = 200     # characters of a tool use's summary
TERMINALS = ("Terminal", "iTerm")
# A tool's input fields that say what it was used on, the first one present.
TOOL_FIELDS = ("description", "command", "file_path", "path", "pattern", "url", "query", "prompt",
               "skill", "subject")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
COMMAND = re.compile(r"<command-name>\s*([^<]*?)\s*</command-name>")
# Claude Code's own messages, typed into the conversation as the user's.
OWN = re.compile(r"^\s*<(local-command-stdout|local-command-stderr|local-command-caveat|"
                 r"system-reminder|task-notification|bash-stdout|bash-stderr)\b")


class Failed(Exception):
    """A session that could not be opened or read; `command`, when there is
    one, is what to run by hand instead."""

    def __init__(self, message: str, command=None):
        super().__init__(message)
        self.command = command


# --- the live listing ----------------------------------------------------------

_live = {"at": None, "rows": {}, "problems": []}
_live_lock = threading.Lock()


def live():
    """({session id: `hub bg list --all-kinds` row}, problems), at most
    LIVE_SECONDS old."""
    with _live_lock:
        if _live["at"] is None or time.monotonic() - _live["at"] > LIVE_SECONDS:
            got = claude_bg.list_sessions(dirs=folders(), all_kinds=True)
            rows = {}
            for s in got["sessions"]:
                sid = s.get("session_id")
                # A session listed twice (background, then woken): the newer row.
                if sid and sid not in rows:
                    rows[sid] = s
            _live.update(at=time.monotonic(), rows=rows, problems=got["problems"])
        return _live["rows"], _live["problems"]


def folders():
    return discovery.claude_dirs()[0]


# --- transcripts ---------------------------------------------------------------

def find_transcript(sid: str):
    """(path, Claude Code folder) of a session's transcript, or (None, None)."""
    for base in folders():
        root = os.path.join(base, "projects")
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for folder in names:
            path = os.path.join(root, folder, sid + ".jsonl")
            if os.path.isfile(path):
                return path, base
    return None, None


def _chunks(path: str):
    """The transcript's first HEAD_BYTES and last TAIL_BYTES (once when the
    file is smaller), as lines; a line cut at either edge is dropped."""
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            head = f.read(HEAD_BYTES)
            if size <= HEAD_BYTES + TAIL_BYTES:
                return (head + f.read()).splitlines()
            f.seek(size - TAIL_BYTES)
            tail = f.read()
    except OSError:
        return []
    return head.splitlines()[:-1] + tail.splitlines()[1:]


META = {"custom-title": "customTitle", "ai-title": "aiTitle", "agent-name": "agentName",
        "bridge-session": "bridgeSessionId"}


def meta(path: str) -> dict:
    """The session's folder (cwd), and the last of each META record, from the
    start and the end of its transcript."""
    found = {}
    for line in _chunks(path):
        if b'"cwd"' not in line and not any(k.encode() in line for k in META):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if "cwd" not in found and isinstance(rec.get("cwd"), str) and rec["cwd"]:
            found["cwd"] = rec["cwd"]
        field = META.get(rec.get("type"))
        if field and isinstance(rec.get(field), str) and rec[field].strip():
            found[rec["type"]] = rec[field].strip()
    return found


def bridge_links(value):
    m = claude_bg.BRIDGE_ID.match(value) if isinstance(value, str) else None
    if not m:
        return None
    return {"web": f"https://claude.ai/code/session_{m.group(2)}",
            "app": f"claude://code/session_{m.group(2)}"}


# --- what the page shows -------------------------------------------------------

def status_of(row) -> str:
    """A live session's status (busy, idle; a background session's state when
    its process is gone: done, stopped...), else `ended`."""
    if not row:
        return "ended"
    return row.get("status") or row.get("state") or "listed"


def describe(sid: str, rows: dict) -> dict:
    """What the page shows for one session, and what it may open it in."""
    row = rows.get(sid)
    path, base = find_transcript(sid)
    m = meta(path) if path else {}
    folder = (row or {}).get("claude_dir") or base
    cwd = (row or {}).get("cwd") or m.get("cwd")
    links = (row or {}).get("links") or bridge_links(m.get("bridge-session"))
    alive = bool(row and row.get("pid"))
    kind = (row or {}).get("kind")
    out = {
        "status": status_of(row),
        "kind": kind,
        "title": m.get("custom-title") or m.get("ai-title") or m.get("agent-name") or (row or {}).get("name"),
        "short_id": (row or {}).get("id"),
        "pid": (row or {}).get("pid"),
        "cwd": cwd,
        "claude_dir": discovery.tilde(folder) if folder else None,
        "links": links,
        # A running session bridged to claude.ai opens in the app at its
        # claude:// link; any other is resumed there (claude --desktop).
        "app_link": links["app"] if alive and links else None,
    }
    # Why each way of opening it is not offered, or None when it is.
    why = {}
    if not links:
        why["web"] = "not connected to claude.ai"
    if not cwd:
        why["app"] = why["terminal"] = "the folder it ran in is not known"
    elif alive and kind == "interactive":
        why["terminal"] = f"already open in a terminal (process {row['pid']})"
        if not links:
            why["app"] = "open in a terminal, and not connected to claude.ai"
    elif alive and not links:
        why["app"] = "running in the background, and not connected to claude.ai"
    out["unavailable"] = why
    return out


def describe_all(ids) -> dict:
    """{session id: describe()} for each session ID among ids."""
    ids = [i for i in dict.fromkeys(ids) if isinstance(i, str) and SESSION_ID.match(i)]
    if not ids:
        return {}
    rows, _ = live()
    return {sid: describe(sid, rows) for sid in ids}


def session_ids(payload) -> list:
    """The session IDs a table payload's cells refer to, in row order."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if payload.get("kind") != "table" or not isinstance(data, dict):
        return []
    ids = []
    for row in data.get("rows") or []:
        for v in (row.values() if isinstance(row, dict) else []):
            if isinstance(v, dict) and v.get("ref") == "session" and isinstance(v.get("id"), str):
                ids.append(v["id"])
    return ids


def enrich(payload: dict) -> dict:
    """A table payload with `sessions` added when its rows refer to sessions."""
    ids = session_ids(payload)
    return dict(payload, sessions=describe_all(ids)) if ids else payload


# --- opening a session ---------------------------------------------------------

def terminal_app() -> str:
    """hub.json's "terminal" (Terminal or iTerm), else Terminal."""
    doc, _ = discovery._read_json(paths.hub_config_path())
    named = doc.get("terminal") if isinstance(doc, dict) else None
    return named if named in TERMINALS else "Terminal"


def terminal_command(sid: str, d: dict) -> str:
    """The shell command a terminal runs to open the session."""
    claude = claude_bg.find_claude() or "claude"
    folder = os.path.expanduser(d["claude_dir"]) if d.get("claude_dir") else None
    env = ""
    if folder and not claude_bg._is_default(folder):
        env = f"CLAUDE_CONFIG_DIR={shlex.quote(folder)} "
    if d.get("pid") and d.get("kind") == "background" and d.get("short_id"):
        run = f"{env}{shlex.quote(claude)} attach {shlex.quote(d['short_id'])}"
    else:
        run = f"{env}{shlex.quote(claude)} --resume {shlex.quote(sid)}"
    return f"cd {shlex.quote(d['cwd'])} && {run}"


def open_session(sid: str, where: str) -> dict:
    """Opens a session in the Claude app or a terminal window. Raises Failed,
    with the command to run by hand when there is one."""
    if not SESSION_ID.match(sid or ""):
        raise Failed(f"not a session id: {sid!r}")
    if where not in ("app", "terminal"):
        raise Failed(f"cannot open a session in {where!r}: app or terminal")
    rows, _ = live()
    d = describe(sid, rows)
    why = d["unavailable"].get(where)
    if why:
        raise Failed(f"cannot open it in the {where}: {why}")
    if where == "app" and d["app_link"]:
        return {"opened": "app", "link": d["app_link"]}
    if where == "app":
        return _desktop(sid, d)
    command = terminal_command(sid, d)
    if platform.system() != "Darwin":
        raise Failed("opening a terminal is done on macOS only; run this in one", command)
    app = terminal_app()
    # A script the terminal app is asked to open runs in a new window of it,
    # with no AppleScript, so macOS asks no permission to control the app.
    script = _script(command)
    try:
        r = subprocess.run(["open", "-a", app, script], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=OPEN_WAIT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Failed(f"could not open {app}: {e}", command)
    if r.returncode != 0:
        raise Failed(f"could not open {app}: {claude_bg._tail(r.stderr) or f'exit {r.returncode}'}", command)
    return {"opened": "terminal", "app": app, "command": command}


SCRIPT_HOURS = 1  # a script is read when the window opens; older ones are removed


def _script(command: str) -> str:
    """A .command file, readable by its owner only, that runs the command and
    then leaves a shell open in the session's folder."""
    folder = os.path.join(paths.cache_dir(), "open")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    now = time.time()
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if now - os.path.getmtime(path) > SCRIPT_HOURS * 3600:
                os.remove(path)
        except OSError:
            pass
    fd, path = tempfile.mkstemp(prefix="session-", suffix=".command", dir=folder)
    with os.fdopen(fd, "w") as f:
        f.write(f'#!/bin/sh\n{command}\nexec "${{SHELL:-/bin/zsh}}" -l\n')
    os.chmod(path, 0o700)
    return path


def _desktop(sid: str, d: dict) -> dict:
    """`claude --desktop --resume ID`, on a terminal of its own: Claude Code
    refuses to hand a session to the app when its output is not one."""
    claude = claude_bg.find_claude()
    if not claude:
        raise Failed(claude_bg.NOT_FOUND)
    folder = os.path.expanduser(d["claude_dir"]) if d.get("claude_dir") else folders()[0]
    if not os.path.isdir(d["cwd"]):
        raise Failed(f"the folder it ran in is gone: {d['cwd']}")
    main, side = pty.openpty()
    try:
        p = subprocess.Popen([claude, "--desktop", "--resume", sid], cwd=d["cwd"],
                             env=claude_bg.session_env(folder), stdin=side, stdout=side, stderr=side,
                             start_new_session=True)
    except OSError as e:
        os.close(main)
        raise Failed(f"could not run {claude}: {e}")
    finally:
        os.close(side)
    said = b""
    end = time.monotonic() + OPEN_WAIT
    try:
        while time.monotonic() < end and len(said) < 65536:
            ready, _, _ = select.select([main], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(main, 4096)
                except OSError:  # the other end closed: it has exited
                    chunk = b""
                if not chunk:
                    break
                said += chunk
            elif p.poll() is not None:
                break
    finally:
        os.close(main)
    try:
        code = p.wait(timeout=1)
    except subprocess.TimeoutExpired:
        return {"opened": "app"}  # still handing it over; it said nothing wrong
    text = ANSI.sub("", said.decode("utf-8", "replace"))
    if code != 0:
        raise Failed(f"`claude --desktop --resume` failed: {claude_bg._tail(text) or f'exit {code}'}")
    return {"opened": "app", "said": claude_bg._tail(text)}


# --- the transcript ------------------------------------------------------------

_parsed = {}  # path -> ((size, mtime), entries); the last few read
_parsed_lock = threading.Lock()
KEEP_PARSED = 4


def _short(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _tool_summary(inp) -> str:
    if not isinstance(inp, dict):
        return ""
    for k in TOOL_FIELDS:
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return _short(" ".join(v.split()), SUMMARY_LIMIT)
    return ""


def _entries(rec) -> list:
    """The transcript entries of one record: {role, kind, text, [tool], at}."""
    if rec.get("isSidechain") or rec.get("isMeta"):
        return []
    role, msg, at = rec.get("type"), rec.get("message"), rec.get("timestamp")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
    out = []
    for b in blocks if isinstance(blocks, list) else []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip():
            text = b["text"]
            if role == "user":
                cmd = COMMAND.search(text)
                if cmd:
                    out.append({"role": role, "kind": "command", "text": cmd.group(1), "at": at})
                    continue
                if OWN.match(text):
                    continue
            out.append({"role": role, "kind": "text", "text": _short(text, TEXT_LIMIT), "at": at})
        elif b.get("type") == "tool_use" and role == "assistant":
            out.append({"role": role, "kind": "tool", "tool": str(b.get("name") or "tool"),
                        "text": _tool_summary(b.get("input")), "at": at})
        elif b.get("type") == "image" and role == "user":
            out.append({"role": role, "kind": "text", "text": "[image]", "at": at})
    return out


def _parse(path: str) -> list:
    entries = []
    with open(path, "rb") as f:
        for line in f:
            # Most of a long transcript is other records; parse only these two.
            if b'"user"' not in line and b'"assistant"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("type") in ("user", "assistant"):
                entries.extend(_entries(rec))
    return entries


def transcript(sid: str, before=None, limit: int = 200) -> dict:
    """The entries before index `before` (default: the end), `limit` of them
    at most: {"total", "start", "entries"}."""
    if not SESSION_ID.match(sid or ""):
        raise Failed(f"not a session id: {sid!r}")
    path, _ = find_transcript(sid)
    if not path:
        raise Failed(f"no transcript for session {sid}")
    try:
        st = os.stat(path)
    except OSError as e:
        raise Failed(f"could not read the transcript: {e}")
    key = (st.st_size, st.st_mtime)
    with _parsed_lock:
        hit = _parsed.get(path)
    if hit and hit[0] == key:
        entries = hit[1]
    else:
        try:
            entries = _parse(path)
        except OSError as e:
            raise Failed(f"could not read the transcript: {e}")
        with _parsed_lock:
            _parsed.pop(path, None)
            _parsed[path] = (key, entries)
            while len(_parsed) > KEEP_PARSED:
                _parsed.pop(next(iter(_parsed)))
    end = len(entries) if before is None else max(0, min(int(before), len(entries)))
    start = max(0, end - max(1, min(int(limit), 1000)))
    return {"total": len(entries), "start": start, "entries": entries[start:end],
            "path": discovery.tilde(path)}
