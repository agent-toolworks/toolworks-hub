#!/usr/bin/env python3
"""Alerts: a sound, a popup and, when asked, a spoken line when a Claude Code
session needs the person -- it is waiting at a permission prompt or a
question, it finished a turn, a background session failed, or it closed.

Where it reads: Claude Code keeps a record of each running session in
sessions/<pid>.json in its folder, with `status` busy, idle or waiting, and
`waitingFor` (permission prompt, input needed...) while waiting. A background
session's jobs/<id>/state.json has its `state` (running, done, failed). The
watcher reads both every POLL_SECONDS, in every Claude Code folder the hub
reads, and alerts on a change:

- needs_input: a session went to `waiting`. It may play again, until it is
               answered, and escalate to a harder-to-miss alarm after a while
               (as agent-teller's approval alerts do).
- finished:    busy -> idle, after a turn at least `min_seconds` long; a
               background session whose state went to done.
- failed:      a background session whose state went to failed.
- exited:      a session's record went away (its process ended).

What it starts with is taken as it is: nothing already waiting when the hub
starts is alerted on.

Who plays it: the hub, with afplay (and say), so a sound plays whichever
browser tab is in front and whether or not the page has been clicked. The
popup is the page's: each open hub page asks for events (a long poll on
/api/alerts/events) and shows a browser notification; a click opens the
session's page. With no page asking, macOS shows the popup (osascript).

The settings are alerts.json in the hub's settings folder, written by the
page's Settings tab; a person's own sounds are in sounds/ beside it. Like
agent-teller's, the sounds are none, four tones made here, macOS's own
(/System/Library/Sounds) and the person's files. Alerts are noise only:
nothing here changes a session.
"""
from __future__ import annotations

import base64
import glob
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discovery  # noqa: E402
import paths  # noqa: E402

POLL_SECONDS = float(os.environ.get("HUB_ALERTS_POLL", "2"))
LISTENING_SECONDS = 40     # a page that asked for events this recently shows the popups
LONG_POLL_SECONDS = 25
KEEP_EVENTS = 50
SAME_EVENT_SECONDS = 60    # one session's same alert, twice within this, is said once
SYSTEM_SOUNDS = "/System/Library/Sounds"
LARGEST_SOUND = 10 * 1024 * 1024
SOUND_TYPES = ("wav", "aif", "aiff", "aifc", "caf", "mp3", "m4a")
SOUND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()-]{0,79}$")
CLOCK = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
TONES = {"chime": "Hub Chime", "knock": "Hub Knock", "rise": "Hub Rise", "alarm": "Hub Alarm"}

EVENTS = ("needs_input", "finished", "failed", "exited")
PLAY_CHOICES = (1, 2, 3, 0)            # 0: until answered
INTERVAL_CHOICES = (10, 30, 60, 300)
MINUTE_CHOICES = (1, 2, 5, 10, 15, 30)
MUTE_CHOICES = (15, 60)

DEFAULTS = {
    "enabled": True,
    "volume": 0.8,
    "events": {
        "needs_input": {"on": True, "sound": "tone:chime", "popup": True, "speak": False,
                        "plays": 1, "interval": 30},
        "finished": {"on": True, "sound": "tone:rise", "popup": True, "speak": False, "min_seconds": 30},
        "failed": {"on": True, "sound": "tone:knock", "popup": True, "speak": False},
        "exited": {"on": False, "sound": "none", "popup": False, "speak": False},
    },
    "escalation": {"on": False, "after_minutes": 5, "sound": "tone:alarm", "full_volume": True,
                   "speak": True, "interval": 60},
    "quiet_hours": {"on": False, "start": "22:00", "end": "08:00"},
    "keep_running": False,
}


# --- settings ------------------------------------------------------------------

def settings_path() -> str:
    return os.path.join(paths.config_dir(), "alerts.json")


def sounds_dir() -> str:
    return os.path.join(paths.config_dir(), "sounds")


def _choice(v, allowed, default):
    return v if v in allowed and not isinstance(v, bool) else default


def _bool(v, default):
    return v if isinstance(v, bool) else default


def _sound(v, default):
    return v if isinstance(v, str) and sound_kind(v) else default


def normalise(doc) -> dict:
    """Settings with every field present and in range: a field missing or
    wrong (an older file, a hand edit) takes its default, the rest are kept."""
    doc = doc if isinstance(doc, dict) else {}
    d = DEFAULTS
    out = {"enabled": _bool(doc.get("enabled"), d["enabled"])}
    vol = doc.get("volume")
    out["volume"] = (min(1.0, max(0.0, float(vol))) if isinstance(vol, (int, float)) and not isinstance(vol, bool)
                     else d["volume"])
    given = doc.get("events") if isinstance(doc.get("events"), dict) else {}
    out["events"] = {}
    for name in EVENTS:
        e, de = given.get(name) if isinstance(given.get(name), dict) else {}, d["events"][name]
        got = {"on": _bool(e.get("on"), de["on"]), "sound": _sound(e.get("sound"), de["sound"]),
               "popup": _bool(e.get("popup"), de["popup"]), "speak": _bool(e.get("speak"), de["speak"])}
        if name == "needs_input":
            got["plays"] = _choice(e.get("plays"), PLAY_CHOICES, de["plays"])
            got["interval"] = _choice(e.get("interval"), INTERVAL_CHOICES, de["interval"])
        if name == "finished":
            n = e.get("min_seconds")
            got["min_seconds"] = (int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) and 0 <= n <= 3600
                                  else de["min_seconds"])
        out["events"][name] = got
    e = doc.get("escalation") if isinstance(doc.get("escalation"), dict) else {}
    de = d["escalation"]
    out["escalation"] = {"on": _bool(e.get("on"), de["on"]),
                         "after_minutes": _choice(e.get("after_minutes"), MINUTE_CHOICES, de["after_minutes"]),
                         "sound": _sound(e.get("sound"), de["sound"]),
                         "full_volume": _bool(e.get("full_volume"), de["full_volume"]),
                         "speak": _bool(e.get("speak"), de["speak"]),
                         "interval": _choice(e.get("interval"), INTERVAL_CHOICES, de["interval"])}
    q = doc.get("quiet_hours") if isinstance(doc.get("quiet_hours"), dict) else {}
    dq = d["quiet_hours"]
    out["quiet_hours"] = {"on": _bool(q.get("on"), dq["on"]),
                          "start": q["start"] if isinstance(q.get("start"), str) and CLOCK.match(q["start"]) else dq["start"],
                          "end": q["end"] if isinstance(q.get("end"), str) and CLOCK.match(q["end"]) else dq["end"]}
    out["keep_running"] = _bool(doc.get("keep_running"), d["keep_running"])
    return out


def load_settings() -> dict:
    doc, _ = discovery._read_json(settings_path())
    return normalise(doc)


def save_settings(doc) -> dict:
    s = normalise(doc)
    os.makedirs(paths.config_dir(), exist_ok=True)
    tmp = f"{settings_path()}.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    os.replace(tmp, settings_path())
    return s


def in_quiet_hours(q: dict, now=None) -> bool:
    if not q.get("on") or q["start"] == q["end"]:
        return False
    t = now or datetime.now()
    minute = t.hour * 60 + t.minute
    start, end = (int(q[k][:2]) * 60 + int(q[k][3:]) for k in ("start", "end"))
    return start <= minute < end if start < end else (minute >= start or minute < end)


# --- sounds ------------------------------------------------------------------

def sound_kind(sid: str):
    """`none`, `tone`, `system` or `custom` for a sound id of that form, else
    None. A name is only ever looked up inside its one folder."""
    kind, _, name = sid.partition(":")
    if kind == "none" and not name:
        return "none"
    if kind == "tone" and name in TONES:
        return "tone"
    if kind in ("system", "custom") and name and "/" not in name and not name.startswith("."):
        return kind
    return None


def system_sounds() -> list:
    try:
        names = os.listdir(SYSTEM_SOUNDS)
    except OSError:
        return []
    return sorted(os.path.splitext(n)[0] for n in names if not n.startswith("."))


def custom_sounds() -> list:
    try:
        names = os.listdir(sounds_dir())
    except OSError:
        return []
    return sorted(n for n in names if not n.startswith(".") and os.path.isfile(os.path.join(sounds_dir(), n)))


def choices() -> list:
    """Every sound the page offers, in order: none, the hub's, macOS's, the person's."""
    return ([{"id": "none", "title": "None", "group": ""}]
            + [{"id": f"tone:{k}", "title": v, "group": "Hub"} for k, v in TONES.items()]
            + [{"id": f"system:{n}", "title": n, "group": "macOS"} for n in system_sounds()]
            + [{"id": f"custom:{n}", "title": os.path.splitext(n)[0], "group": "Yours"} for n in custom_sounds()])


def sound_file(sid: str):
    """The file a sound plays from, or None (no sound, or its file has gone)."""
    kind = sound_kind(sid or "")
    name = sid.partition(":")[2] if kind else ""
    if kind == "tone":
        return tone_file(name)
    if kind == "system":
        for n in os.listdir(SYSTEM_SOUNDS) if os.path.isdir(SYSTEM_SOUNDS) else []:
            if os.path.splitext(n)[0] == name:
                return os.path.join(SYSTEM_SOUNDS, n)
        return None
    if kind == "custom":
        path = os.path.join(sounds_dir(), name)
        return path if os.path.isfile(path) and not os.path.islink(path) else None
    return None


def tone_file(name: str) -> str:
    """A tone's WAV file in the cache folder, made the first time it plays."""
    folder = os.path.join(paths.cache_dir(), "sounds")
    path = os.path.join(folder, f"{name}.wav")
    if not os.path.isfile(path):
        os.makedirs(folder, mode=0o700, exist_ok=True)
        tmp = f"{path}.{os.getpid()}"
        with open(tmp, "wb") as f:
            f.write(tone_wav(name))
        os.replace(tmp, path)
    return path


RATE = 22050


def _notes(name: str) -> list:
    """(frequency, start, duration, amplitude, decay, partials) of each note.
    agent-teller's tones (ToneSynth), so the two sound alike."""
    bell = ((1, 1), (2.76, 0.28), (5.4, 0.08))
    if name == "chime":
        return [(1318.5, 0, 1.0, 0.5, 0.35, bell), (987.8, 0.2, 1.2, 0.5, 0.45, bell)]
    if name == "knock":
        return [(170, t, 0.14, 0.8, 0.035, ((1, 1), (2.3, 0.4))) for t in (0, 0.17, 0.62, 0.79)]
    if name == "rise":
        return [(f, i * 0.12, 0.7, 0.4, 0.22, bell) for i, f in enumerate((523.25, 659.25, 783.99, 1046.5))]
    square = ((1, 1), (3, 0.33), (5, 0.2))
    return [(932, (i // 3) * 0.7 + (i % 3) * 0.16, 0.11, 0.35, 1, square) for i in range(6)]


def tone_wav(name: str) -> bytes:
    notes = _notes(name)
    count = int(max(s + d for _, s, d, *_ in notes) * RATE) + 1
    samples = [0.0] * count
    for freq, start, dur, amp, decay, partials in notes:
        first, total = int(start * RATE), int(dur * RATE)
        steps = [(2 * math.pi * freq * m / RATE, level) for m, level in partials]
        for i in range(min(total, count - first)):
            t = i / RATE
            edge = min(1.0, t / 0.004, (dur - t) / 0.004)  # no click at either end
            env = max(0.0, edge) * math.exp(-t / decay)
            samples[first + i] += amp * env * sum(level * math.sin(w * i) for w, level in steps)
    peak = max(abs(s) for s in samples) or 1
    scale = 0.95 / peak if peak > 0.95 else 1
    pcm = struct.pack(f"<{count}h", *(int(max(-1, min(1, s * scale)) * 32767) for s in samples))
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)


def is_audio(data: bytes) -> bool:
    """Whether data starts like a WAV, AIFF, CAF, MP3 or M4A file."""
    h = data[:12]
    return ((h[:4] == b"RIFF" and h[8:12] == b"WAVE") or (h[:4] == b"FORM" and h[8:12] in (b"AIFF", b"AIFC"))
            or h[:4] == b"caff" or h[:3] == b"ID3" or (len(h) > 1 and h[0] == 0xFF and h[1] & 0xE0 == 0xE0)
            or h[4:8] == b"ftyp")


class Refused(Exception):
    pass


def add_sound(name, data_b64) -> str:
    """Saves a person's sound file under its name (a free one when taken) and
    returns its sound id."""
    if not isinstance(name, str) or not isinstance(data_b64, str):
        raise Refused("send the file's name and its contents")
    name = os.path.basename(name.strip())
    base, ext = os.path.splitext(name)
    if ext.lower().lstrip(".") not in SOUND_TYPES or not SOUND_NAME.match(name):
        raise Refused(f"a sound is a {', '.join(SOUND_TYPES)} file with a plain name")
    try:
        data = base64.b64decode(data_b64, validate=True)
    except ValueError:
        raise Refused("the file's contents are not base64")
    if len(data) > LARGEST_SOUND:
        raise Refused("a sound file is 10 MB at most")
    if not is_audio(data):
        raise Refused("that file is not a WAV, AIFF, CAF, MP3 or M4A sound")
    os.makedirs(sounds_dir(), exist_ok=True)
    final, n = name, 2
    while os.path.exists(os.path.join(sounds_dir(), final)):
        final, n = f"{base} {n}{ext}", n + 1
    with open(os.path.join(sounds_dir(), final), "xb") as f:
        f.write(data)
    return f"custom:{final}"


def remove_sound(sid) -> None:
    if not isinstance(sid, str) or sound_kind(sid) != "custom":
        raise Refused("only your own sounds can be removed")
    path = os.path.join(sounds_dir(), sid.partition(":")[2])
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# --- playing -----------------------------------------------------------------
# One alert plays at a time; a new one stops the one playing. Each command
# can be replaced by setting its variable, as the tests do.

def _tool(var: str, name: str):
    given = os.environ.get(var)
    if given:
        return given if os.access(given, os.X_OK) else None
    return shutil.which(name) if sys.platform == "darwin" else None


def player():
    return _tool("HUB_ALERTS_PLAYER", "afplay")


def speaker():
    return _tool("HUB_ALERTS_SPEAKER", "say")


def notifier():
    return _tool("HUB_ALERTS_NOTIFIER", "osascript")


class Player:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.gen = 0

    def play(self, sound: str, volume: float, say=None) -> bool:
        """Plays a sound, then says `say`; False when there is nothing to play it with."""
        path = sound_file(sound) if sound != "none" else None
        cmd_play, cmd_say = player(), speaker()
        steps = []
        if path and cmd_play:
            steps.append([cmd_play, "-v", f"{volume:.2f}", path])
        if say and cmd_say:
            steps.append([cmd_say, say])
        if not steps:
            return False
        with self.lock:
            self.gen += 1
            gen = self.gen
            self._stop()
        threading.Thread(target=self._run, args=(gen, steps), daemon=True).start()
        return True

    def _stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def _run(self, gen, steps):
        for cmd in steps:
            with self.lock:
                if gen != self.gen:
                    return
                try:
                    self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.DEVNULL)
                except OSError:
                    return
                proc = self.proc
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()


def macos_popup(title: str, body: str) -> None:
    cmd = notifier()
    if not cmd:
        return
    script = ["-e", "on run argv", "-e", 'display notification (item 2 of argv) with title "Toolworks Hub" '
              "subtitle (item 1 of argv)", "-e", "end run"]
    try:
        subprocess.Popen([cmd, *script, title, body], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError:
        pass


# --- reading the sessions ------------------------------------------------------

def _alive(pid) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def snapshot(folders) -> tuple:
    """({record path: session}, {job id: job}) as Claude Code's files have them now.
    A session is {sid, name, cwd, kind, status, waiting_for, since}; a job is
    {sid, name, cwd, state, detail}."""
    live, jobs = {}, {}
    for base in folders:
        for path in glob.glob(os.path.join(base, "sessions", "*.json")):
            rec = _read(path)
            if not rec or not _alive(rec.get("pid")):
                continue
            since = rec.get("statusUpdatedAt")
            live[path] = {"sid": rec.get("sessionId") if isinstance(rec.get("sessionId"), str) else None,
                          "name": rec.get("name") if isinstance(rec.get("name"), str) else None,
                          "cwd": rec.get("cwd") if isinstance(rec.get("cwd"), str) else None,
                          "kind": rec.get("kind") or "interactive",
                          "status": rec.get("status") if isinstance(rec.get("status"), str) else None,
                          "waiting_for": rec.get("waitingFor") if isinstance(rec.get("waitingFor"), str) else None,
                          "since": since / 1000 if isinstance(since, (int, float)) and not isinstance(since, bool) else None}
        for path in glob.glob(os.path.join(base, "jobs", "*", "state.json")):
            rec = _read(path)
            if not rec or not isinstance(rec.get("state"), str):
                continue
            jobs[path] = {"sid": rec.get("sessionId") if isinstance(rec.get("sessionId"), str) else None,
                          "name": rec.get("name") if isinstance(rec.get("name"), str) else None,
                          "cwd": rec.get("cwd") if isinstance(rec.get("cwd"), str) else None,
                          "state": rec["state"],
                          "detail": rec.get("detail") if isinstance(rec.get("detail"), str) else None}
    return live, jobs


def changes(before: dict, after: dict, jobs_before: dict, jobs_after: dict, turns: dict, now: float) -> list:
    """The alerts between two snapshots: [(event, session, extra)]. `turns`
    holds when each session's turn started (record path -> time), kept
    across calls."""
    out = []
    for path, s in after.items():
        was = before.get(path)
        st, old = s["status"], was and was["status"]
        if st == "busy" and old not in ("busy", "waiting"):
            turns[path] = s["since"] or now
        if st == "waiting" and old != "waiting" and was is not None:
            out.append(("needs_input", s, {"reason": s["waiting_for"]}))
        if st == "idle" and old in ("busy", "waiting"):
            started = turns.pop(path, None)
            out.append(("finished", s, {"seconds": (s["since"] or now) - started if started else None}))
    for path, was in before.items():
        if path not in after:
            turns.pop(path, None)
            out.append(("exited", was, {}))
    for path, j in jobs_after.items():
        old = jobs_before.get(path)
        if old is None or old["state"] == j["state"]:
            continue
        if j["state"] == "done":
            out.append(("finished", j, {"seconds": None, "background": True}))
        elif j["state"] == "failed":
            out.append(("failed", j, {"reason": j["detail"]}))
    return out


# --- the watcher ---------------------------------------------------------------

def _label(s: dict) -> str:
    return s.get("name") or (os.path.basename(s["cwd"]) if s.get("cwd") else None) or "A session"


def _duration(seconds) -> str:
    if seconds is None:
        return ""
    m = int(seconds // 60)
    return f"{int(seconds)} s" if m < 1 else f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


def describe(event: str, s: dict, extra: dict) -> tuple:
    """(title, body) of an alert."""
    name = _label(s)
    where = discovery.tilde(s["cwd"]) if s.get("cwd") else ""
    if event == "needs_input":
        return f"{name} needs you", " · ".join(filter(None, [extra.get("reason") or "waiting for you", where]))
    if event == "escalated":
        return (f"{name} is still waiting",
                " · ".join(filter(None, [f"for {_duration(extra.get('seconds'))}", extra.get("reason"), where])))
    if event == "finished":
        took = _duration(extra.get("seconds"))
        what = "background session done" if extra.get("background") else "waiting for your reply"
        return f"{name} finished", " · ".join(filter(None, [took and f"after {took}", what, where]))
    if event == "failed":
        return f"{name} failed", " · ".join(filter(None, [extra.get("reason"), where]))
    if event == "test":
        return "Test alert", "This is how an alert looks and sounds."
    return f"{name} closed", where


class Watcher:
    def __init__(self, folders=None):
        self.folders = folders
        self.settings = load_settings()
        self.muted_until = None
        self.player = Player()
        self.cond = threading.Condition()
        self.events = []         # the last KEEP_EVENTS alerts, oldest first
        self.next_id = 1
        self.last_listen = -LISTENING_SECONDS  # when a page last asked for events (monotonic may start at 0)
        self.sessions, self.jobs, self.turns = {}, {}, {}
        self.waiting = {}        # record path -> {at, plays, last, escalated, reason}
        self.recent = {}         # (event, session id) -> when it was last alerted
        self.started = False
        self.stop = threading.Event()

    # The settings, as the page reads and writes them.
    def state(self) -> dict:
        muted = self.muted_until if self.muted_until and self.muted_until > time.time() else None
        return {"settings": self.settings, "sounds": choices(), "muted_until": muted,
                "mute_choices": list(MUTE_CHOICES), "watching": len(self.sessions),
                "waiting": len(self.waiting), "can_play": bool(player()), "can_speak": bool(speaker()),
                "sounds_dir": discovery.tilde(sounds_dir()), "settings_file": discovery.tilde(settings_path()),
                "quiet_now": in_quiet_hours(self.settings["quiet_hours"]),
                "recent": [e for e in reversed(self.events) if e["event"] != "test"][:20],
                "choices": {"plays": list(PLAY_CHOICES), "interval": list(INTERVAL_CHOICES),
                            "after_minutes": list(MINUTE_CHOICES)}}

    def update(self, doc) -> dict:
        if not isinstance(doc, dict):
            raise Refused("send the settings")
        self.settings = save_settings(doc)
        return self.state()

    def mute(self, minutes) -> dict:
        if minutes not in (0, *MUTE_CHOICES):
            raise Refused(f"mute for {', '.join(map(str, MUTE_CHOICES))} minutes, or 0 to unmute")
        self.muted_until = time.time() + minutes * 60 if minutes else None
        return self.state()

    def keep_running(self) -> bool:
        return self.settings["enabled"] and self.settings["keep_running"]

    # Running.
    def start(self):
        if not self.started:
            self.started = True
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        folders = self.folders if self.folders is not None else discovery.claude_dirs()[0]
        self.sessions, self.jobs = snapshot(folders)
        while not self.stop.wait(POLL_SECONDS):
            try:
                self.tick(folders, time.time())
            except Exception as e:  # never let one bad read end the watching
                print(f"hub: alerts: {e}", file=sys.stderr, flush=True)

    def tick(self, folders, now: float):
        live, jobs = snapshot(folders)
        found = changes(self.sessions, live, self.jobs, jobs, self.turns, now)
        self.sessions, self.jobs = live, jobs
        for path in [p for p in self.waiting if live.get(p, {}).get("status") != "waiting"]:
            del self.waiting[path]
        for event, s, extra in found:
            if event == "needs_input":
                path = next((p for p, x in live.items() if x is s), None)
                if path:
                    self.waiting[path] = {"at": now, "plays": 1, "last": now, "escalated": False,
                                          "reason": extra.get("reason")}
            self.alert(event, s, extra, now)
        self._repeat(now)

    def _repeat(self, now: float):
        """A session still waiting plays again, and escalates after a while."""
        cfg = self.settings
        if not cfg["enabled"]:
            return
        ev, esc = cfg["events"]["needs_input"], cfg["escalation"]
        for path, w in self.waiting.items():
            s = self.sessions.get(path)
            if not s:
                continue
            if esc["on"] and ev["on"] and not w["escalated"] and now - w["at"] >= esc["after_minutes"] * 60:
                w["escalated"], w["last"] = True, now
                self.alert("escalated", s, {"reason": w["reason"], "seconds": now - w["at"]}, now)
            elif w["escalated"]:
                if now - w["last"] >= esc["interval"]:
                    w["last"] = now
                    self._sound(esc["sound"], 1.0 if esc["full_volume"] else cfg["volume"], None)
            elif ev["on"] and (ev["plays"] == 0 or w["plays"] < ev["plays"]) and now - w["last"] >= ev["interval"]:
                w["plays"], w["last"] = w["plays"] + 1, now
                self._sound(ev["sound"], cfg["volume"], None)

    def _quiet(self) -> bool:
        return in_quiet_hours(self.settings["quiet_hours"])

    def _muted(self) -> bool:
        return bool(self.muted_until and self.muted_until > time.time())

    def _sound(self, sound, volume, say):
        if not self._muted() and not self._quiet():
            self.player.play(sound, volume, say)

    def alert(self, event: str, s: dict, extra: dict, now: float, force=False):
        """Plays, speaks and posts one alert, as the settings say."""
        cfg = self.settings
        if event == "escalated":
            e, esc = cfg["events"]["needs_input"], cfg["escalation"]
            how = {"sound": esc["sound"], "popup": e["popup"], "speak": esc["speak"],
                   "volume": 1.0 if esc["full_volume"] else cfg["volume"]}
        elif event == "test":
            how = dict(extra, volume=cfg["volume"])
        else:
            e = cfg["events"][event]
            if not force and (not cfg["enabled"] or not e["on"]):
                return None
            if event == "finished" and not force and extra.get("seconds") is not None \
                    and extra["seconds"] < e["min_seconds"]:
                return None
            how = dict(e, volume=cfg["volume"])
        key = (event, s.get("sid"))
        if event not in ("test", "escalated") and s.get("sid") and now - self.recent.get(key, -1e9) < SAME_EVENT_SECONDS:
            return None
        self.recent[key] = now
        title, body = describe(event, s, extra)
        # Muted: listed on the page, and nothing else.
        loud = force or not self._muted()
        if loud:
            if force:
                self.player.play(how["sound"], how["volume"], title if how["speak"] else None)
            else:
                self._sound(how["sound"], how["volume"], title if how["speak"] else None)
        popup = how["popup"] and loud
        if popup and time.monotonic() - self.last_listen > LISTENING_SECONDS:
            macos_popup(title, body)
        return self._post({"event": event, "title": title, "body": body, "session": s.get("sid"),
                           "popup": popup, "at": now})

    def test(self, sound=None) -> dict:
        e = self.settings["events"]["needs_input"]
        return self.alert("test", {}, {"sound": sound if isinstance(sound, str) and sound_kind(sound) else e["sound"],
                                       "popup": True, "speak": e["speak"]}, time.time(), force=True)

    def preview(self, sound, volume=None) -> bool:
        if not isinstance(sound, str) or not sound_kind(sound):
            raise Refused("not a sound")
        vol = volume if isinstance(volume, (int, float)) and not isinstance(volume, bool) else self.settings["volume"]
        return self.player.play(sound, min(1.0, max(0.0, float(vol))))

    # The page's long poll.
    def _post(self, e: dict) -> dict:
        with self.cond:
            e["id"] = self.next_id
            self.next_id += 1
            self.events.append(e)
            del self.events[:-KEEP_EVENTS]
            self.cond.notify_all()
        return e

    def wait_events(self, after: int, timeout: float = LONG_POLL_SECONDS) -> dict:
        """The events after id `after`, waiting up to `timeout` for one. A
        page that has none yet (after < 0) gets only where the list is."""
        self.last_listen = time.monotonic()
        end = time.monotonic() + timeout
        with self.cond:
            # A page that counted past the end saw a hub before this one.
            if after < 0 or after > self.next_id - 1:
                return {"events": [], "last": self.next_id - 1}
            while True:
                new = [e for e in self.events if e["id"] > after]
                left = end - time.monotonic()
                if new or left <= 0:
                    break
                self.cond.wait(min(left, 5))
            self.last_listen = time.monotonic()
            return {"events": new, "last": self.next_id - 1}
