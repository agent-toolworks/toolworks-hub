#!/usr/bin/env python3
"""`hub bg`: Claude Code background sessions, for any source to start, list
and finish.

A source that offers "start a session for this item" needs the same four
things every time, and each has an edge this module handles once (#7):

- start:  `claude --bg`, run in the item's folder. The binary is found on
          PATH and in Claude Code's usual install places, never from
          CLAUDE_CODE_EXECPATH, which a hub started from inside a session
          inherits and which goes stale; that session's other variables are
          left out too, so the new session is not taken for its child.
- list:   `claude agents --json --all`, background rows only (with
          --all-kinds, interactive ones too), from every Claude Code folder
          the hub reads. Rows may lack keys: an interactive row has no `id`,
          and only a row whose process is alive has `pid` and `status`.
- resume: `claude --bg --resume <full session id>` and nothing else. A
          short id, or any other argument, makes Claude Code start a copy
          under a new id instead of waking the session (#9).
- finish: `claude stop`, then `claude rm`. A stopped session keeps its
          transcript until rm.
- links:  the claude.ai session a background session is bridged to, from
          `jobs/<id>/state.json`. That file is Claude Code's own and also
          holds account ids: only `bridgeSessionId` is read, and only a value
          shaped like one is used. No value, no link.

Every command prints one JSON object. A source runs it through
$TOOLWORKS_HUB, which the hub sets for every `run`:

    "$TOOLWORKS_HUB" bg list [--all-kinds]
    "$TOOLWORKS_HUB" bg start --cwd DIR --name NAME [--model M] [--claude-dir D] \\
        --prompt TEXT [-- EXTRA CLAUDE ARGUMENTS]
    "$TOOLWORKS_HUB" bg resume SESSION_ID [--cwd DIR] [--claude-dir D]
    "$TOOLWORKS_HUB" bg stop|rm|finish ID
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discovery  # noqa: E402

# Where Claude Code's installers put the binary, after PATH.
INSTALL_PLACES = ("~/.local/bin/claude", "~/.claude/local/claude",
                  "/opt/homebrew/bin/claude", "/usr/local/bin/claude")
# Variables Claude Code sets for the processes of one session. A hub started
# from that session's shell carries them, and handing them to a new session
# would tie it to the old one.
SESSION_VARS = ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_CODE_SESSION_ID",
                "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
                "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH",
                "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SSE_PORT")
BRIDGE_ID = re.compile(r"^(cse|session)_([A-Za-z0-9_-]+)$")
SHORT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
BACKGROUNDED = re.compile(r"backgrounded\s*·\s*(\S+)(?:\s*·\s*(.*))?")
IDLE = re.compile(r"\s*\(idle\b[^()]*\)\s*$")  # "(idle — send a prompt to start)" after a woken session's name
COPY = re.compile(r"started a copy (?:of that conversation )?as (\S+?)[.,;]?(?:\s|$)")
LIST_TIMEOUT = 15
ACT_TIMEOUT = 60


class Failed(Exception):
    pass


def find_claude():
    """The `claude` binary: TOOLWORKS_CLAUDE_BIN, else PATH, else an install
    place. None when there is none."""
    given = os.environ.get("TOOLWORKS_CLAUDE_BIN")
    if given:
        return given if os.access(given, os.X_OK) else None
    found = shutil.which("claude")
    if found:
        return found
    for place in INSTALL_PLACES:
        path = os.path.expanduser(place)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _is_default(folder: str) -> bool:
    return os.path.realpath(folder) == os.path.realpath(os.path.expanduser("~/.claude"))


def session_env(folder: str) -> dict:
    """The environment for a `claude` command on one Claude Code folder.
    The default ~/.claude is reached with CLAUDE_CONFIG_DIR unset: set to
    ~/.claude, Claude Code would look for its .claude.json inside it rather
    than beside it, and find another account's or none."""
    env = {k: v for k, v in os.environ.items() if k not in SESSION_VARS}
    if _is_default(folder):
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = folder
    return env


def folders() -> list:
    """The folders the hub named to the source running this, else the
    hub's own search."""
    named = [d for d in os.environ.get("TOOLWORKS_CLAUDE_DIRS", "").split(os.pathsep) if d]
    return named or discovery.claude_dirs()[0]


def links(folder: str, short_id: str):
    """{"web", "app"} for a background session bridged to claude.ai, else
    None. Reads one field of jobs/<id>/state.json and nothing else."""
    if not SHORT_ID.match(short_id or ""):
        return None
    try:
        with open(os.path.join(folder, "jobs", short_id, "state.json"), encoding="utf-8") as f:
            value = json.load(f).get("bridgeSessionId")
    except (OSError, ValueError, AttributeError):
        return None
    m = BRIDGE_ID.match(value) if isinstance(value, str) else None
    if not m:
        return None
    return {"web": f"https://claude.ai/code/session_{m.group(2)}",
            "app": f"claude://code/session_{m.group(2)}"}


def _iso(ms):
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _str(v):
    return v if isinstance(v, str) and v else None


def _pid(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def _run(claude, args, folder, timeout, cwd=None):
    try:
        return subprocess.run([claude] + args, env=session_env(folder), cwd=cwd,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Failed(f"`claude {args[0]}` did not answer in {timeout} s")
    except OSError as e:
        raise Failed(f"could not run {claude}: {e}")


def _tail(text: str) -> str:
    lines = (text or "").strip().splitlines()
    return " / ".join(lines[-3:])


def list_sessions(dirs=None, all_kinds=False) -> dict:
    """Every background session in every folder, and with all_kinds the
    interactive ones too. A folder that cannot be listed is a problem in the
    answer, and the others are still listed."""
    claude = find_claude()
    out = {"claude": claude, "sessions": [], "problems": []}
    if not claude:
        out["problems"].append(NOT_FOUND)
        return out
    for folder in dirs or folders():
        try:
            r = _run(claude, ["agents", "--json", "--all"], folder, LIST_TIMEOUT)
        except Failed as e:
            out["problems"].append(f"{discovery.tilde(folder)}: {e}")
            continue
        try:
            rows = json.loads(r.stdout) if r.returncode == 0 else None
        except ValueError:
            rows = None
        if not isinstance(rows, list):
            out["problems"].append(f"{discovery.tilde(folder)}: `claude agents --json` failed: "
                                   f"{_tail(r.stderr) or _tail(r.stdout) or f'exit {r.returncode}'}")
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            kind = row.get("kind")
            if kind == "background":
                if not _str(row.get("id")):
                    continue
            elif not (all_kinds and kind == "interactive" and _str(row.get("sessionId"))):
                continue
            # An interactive session has no short id: its session_id is its key.
            short = _str(row.get("id")) if kind == "background" else None
            out["sessions"].append({
                "id": short,
                "session_id": _str(row.get("sessionId")),
                "kind": kind,
                "name": _str(row.get("name")),
                "cwd": _str(row.get("cwd")),
                "state": _str(row.get("state")),
                "status": _str(row.get("status")),  # only while the process is alive
                "pid": _pid(row.get("pid")),        # likewise
                "started_at": _iso(row.get("startedAt")),
                "claude_dir": folder,
                "links": links(folder, short) if short else None,
            })
    out["sessions"].sort(key=lambda s: s["started_at"] or "", reverse=True)
    return out


def unapproved_mcp(folder: str, cwd: str) -> list:
    """Servers in cwd's .mcp.json that this account has neither approved nor
    turned down. A session started there waits at the approval dialog, in
    state `blocked`, while `claude --bg` has already said it started."""
    try:
        with open(os.path.join(cwd, ".mcp.json"), encoding="utf-8") as f:
            names = list((json.load(f).get("mcpServers") or {}).keys())
    except (OSError, ValueError, AttributeError):
        return []
    if not names:
        return []
    cfg = (os.path.expanduser("~/.claude.json") if _is_default(folder)
           else os.path.join(folder, ".claude.json"))
    try:
        with open(cfg, encoding="utf-8") as f:
            project = (json.load(f).get("projects") or {}).get(os.path.realpath(cwd)) or {}
    except (OSError, ValueError, AttributeError):
        project = {}
    if not isinstance(project, dict) or project.get("enableAllProjectMcpServers") is True:
        return []
    decided = set(project.get("enabledMcpjsonServers") or []) | set(project.get("disabledMcpjsonServers") or [])
    return [n for n in names if n not in decided]


def start(cwd, name, prompt, model=None, folder=None, extra=()) -> dict:
    claude = find_claude()
    if not claude:
        raise Failed(NOT_FOUND)
    if not os.path.isdir(cwd or ""):
        raise Failed(f"no such folder: {cwd}")
    if not prompt or not prompt.strip():
        raise Failed("the prompt is empty")
    if prompt.startswith("-"):
        raise Failed("a prompt may not start with '-': claude would read it as an option")
    folder = folder or folders()[0]
    args = ["--bg"] + (["--model", model] if model else []) + (["-n", name] if name else [])
    args += list(extra) + [prompt]
    r = _run(claude, args, folder, ACT_TIMEOUT, cwd=cwd)
    m = BACKGROUNDED.search(r.stdout or "")
    if r.returncode != 0 or not m:
        raise Failed(f"`claude --bg` failed: {_tail(r.stderr) or _tail(r.stdout) or f'exit {r.returncode}'}")
    out = {"id": m.group(1), "name": (m.group(2) or "").strip() or name, "claude": claude,
           "claude_dir": folder, "cwd": os.path.abspath(cwd)}
    waiting = unapproved_mcp(folder, cwd)
    if waiting:
        out["notice"] = (f"{cwd}/.mcp.json names servers this account has not approved "
                         f"({', '.join(waiting)}): the session waits at the approval dialog, "
                         f"in state blocked, until they are approved in Claude Code")
    return out


def resume(session_id, cwd=None, folder=None) -> dict:
    """Wakes a stopped background session under its own id: `claude --bg
    --resume <session id>` with no other argument, run in the session's
    folder. The session is idle until a prompt reaches it."""
    claude = find_claude()
    if not claude:
        raise Failed(NOT_FOUND)
    if not SESSION_ID.match(session_id or ""):
        if SHORT_ID.match(session_id or ""):
            raise Failed(f"{session_id} is not a full session id: Claude Code would start a copy of it "
                         "under a new id. Pass its session_id, as `hub bg list` prints it")
        raise Failed(f"not a session id: {session_id!r}")
    if not folder or not cwd:
        row = next((s for s in list_sessions(all_kinds=True)["sessions"]
                    if s["session_id"] == session_id and (not folder or s["claude_dir"] == folder)), None)
        folder = folder or (row and row["claude_dir"]) or folders()[0]
        cwd = cwd or (row and row["cwd"])
        if not cwd:
            raise Failed(f"no session {session_id} is listed to say where it ran: pass --cwd")
    if not os.path.isdir(cwd):
        raise Failed(f"no such folder: {cwd}")
    r = _run(claude, ["--bg", "--resume", session_id], folder, ACT_TIMEOUT, cwd=cwd)
    m = BACKGROUNDED.search(r.stdout or "")
    if r.returncode != 0 or not m:
        raise Failed(f"`claude --bg --resume` failed: {_tail(r.stderr) or _tail(r.stdout) or f'exit {r.returncode}'}")
    copy = COPY.search(r.stderr or "")
    new = copy.group(1) if copy else None
    return {"id": m.group(1), "name": IDLE.sub("", m.group(2) or "").strip() or None, "claude": claude,
            "claude_dir": folder, "cwd": os.path.abspath(cwd),
            # A copy is a new session: its full id is known only when Claude Code says it.
            "session_id": (new if SESSION_ID.match(new or "") else None) if copy else session_id,
            "copy": bool(copy)}


def _owner(short_id: str, folder=None):
    """The folder a session belongs to: the one given, else the one that lists it."""
    if not SHORT_ID.match(short_id or ""):
        raise Failed(f"not a background session id: {short_id!r}")
    if folder:
        return folder
    for s in list_sessions()["sessions"]:
        if s["id"] == short_id:
            return s["claude_dir"]
    raise Failed(f"no background session {short_id} in {', '.join(map(discovery.tilde, folders()))}")


def act(verb: str, short_id: str, folder=None) -> dict:
    """`stop`, `rm`, or `finish` (stop, then rm)."""
    claude = find_claude()
    if not claude:
        raise Failed(NOT_FOUND)
    folder = _owner(short_id, folder)
    out = {"id": short_id, "claude_dir": folder}
    for step in (["stop", "rm"] if verb == "finish" else [verb]):
        r = _run(claude, [step, short_id], folder, ACT_TIMEOUT)
        out[step] = _tail(r.stdout) or _tail(r.stderr)
        # A session that already exited cannot be stopped; rm still removes it.
        if r.returncode != 0 and not (verb == "finish" and step == "stop"):
            raise Failed(f"`claude {step} {short_id}` failed: {_tail(r.stderr) or _tail(r.stdout)}")
    return out


NOT_FOUND = ("Claude Code's `claude` command was not found on PATH or in "
             + ", ".join(INSTALL_PLACES) + "; set TOOLWORKS_CLAUDE_BIN to it")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="hub bg", description="Claude Code background sessions, as JSON.")
    sub = p.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list", help="every background session, with its claude.ai links when bridged")
    ls.add_argument("--all-kinds", action="store_true",
                    help="interactive sessions too, each with id null and its session_id")
    s = sub.add_parser("start", help="start a background session in a folder")
    s.add_argument("--cwd", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--prompt", required=True)
    s.add_argument("--model")
    s.add_argument("--claude-dir", help="the Claude Code folder (account) to start it in; "
                                        "default the hub's own")
    s.add_argument("extra", nargs="*", help="after --: more arguments for claude")
    r = sub.add_parser("resume", help="wake a stopped background session under its own id")
    r.add_argument("session_id", help="the full session id (session_id in `hub bg list`), not the short id")
    r.add_argument("--cwd", help="the folder it ran in; default the one `hub bg list` gives")
    r.add_argument("--claude-dir")
    for verb in ("stop", "rm", "finish"):
        a = sub.add_parser(verb, help={"stop": "stop a session; its transcript is kept",
                                       "rm": "remove a session and its worktree",
                                       "finish": "stop, then remove"}[verb])
        a.add_argument("id")
        a.add_argument("--claude-dir")
    a = p.parse_args(argv)
    try:
        if a.command == "list":
            result = list_sessions(all_kinds=a.all_kinds)
        elif a.command == "start":
            result = start(a.cwd, a.name, a.prompt, a.model, a.claude_dir, a.extra)
        elif a.command == "resume":
            result = resume(a.session_id, a.cwd, a.claude_dir)
        else:
            result = act(a.command, a.id, a.claude_dir)
    except Failed as e:
        json.dump({"error": str(e)}, sys.stdout)
        print()
        return 1
    json.dump(result, sys.stdout)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
