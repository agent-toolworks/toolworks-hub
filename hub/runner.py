#!/usr/bin/env python3
"""Run one command of a source's `run` program, bounded, and read its JSON.

A source is somebody else's code, so nothing it does may hang or flood the
hub. Each command gets:

- its own process group, killed outright when the command ends or its time is
  up. Killing only `run` would leave a `curl` it started still running.
  A source that wants work to outlive a command is told so in
  docs/contract.md; it must not leave processes behind.
- stdout and stderr in temporary files, never pipes. A pipe that fills blocks
  the child, and reading one without a limit lets a runaway source fill
  memory. The file's size is checked before a byte of it is read.
- no stdin, and the source folder as its working directory.
- the hub's environment plus TOOLWORKS_SOURCE_CONFIG, the folder for this
  source's own settings (paths.source_config_dir). It may not exist yet.

The hub's state for a source that fails here is `broken`, shown with the
problem and the end of stderr.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contract  # noqa: E402
import paths  # noqa: E402

STDERR_TAIL = 4000  # bytes of stderr kept for display


@dataclass
class Result:
    returncode: Optional[int] = None
    stdout: Optional[bytes] = None
    stderr: str = ""
    timed_out: bool = False
    too_large: bool = False
    seconds: float = 0.0
    error: Optional[str] = None    # it never started


def run(executable: str, args: list, cwd: str, timeout: float, env=None) -> Result:
    started = time.monotonic()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen([executable, *args], cwd=cwd, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=err, start_new_session=True, env=env)
        except OSError as e:
            return Result(error=e.strerror or str(e))
        result = Result()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            result.timed_out = True
        # Also after a normal exit: anything `run` left running in the
        # background is still in the group, writing to these files.
        _kill_group(proc.pid)
        proc.wait()
        result.returncode = proc.returncode
        result.seconds = time.monotonic() - started

        size = os.fstat(out.fileno()).st_size
        if size > contract.MAX_OUTPUT:
            result.too_large = True
        else:
            out.seek(0)
            result.stdout = out.read()
        err_size = os.fstat(err.fileno()).st_size
        err.seek(max(0, err_size - STDERR_TAIL))
        result.stderr = err.read().decode("utf-8", "replace")
    return result


def call(source_dir: str, args: list, timeout: float, name: str):
    """Run `<source_dir>/run args...` as the source called `name`. Returns
    (value, None, stderr) when it printed valid JSON and exited 0, else
    (None, problem, stderr)."""
    env = dict(os.environ, TOOLWORKS_SOURCE_CONFIG=paths.source_config_dir(name))
    r = run(os.path.join(source_dir, "run"), args, source_dir, timeout, env)
    if r.error is not None:
        return None, f"did not start ({r.error})", r.stderr
    if r.timed_out:
        return None, f"took longer than {timeout:g} s and was stopped", r.stderr
    if r.too_large:
        return None, f"printed more than {contract.MAX_OUTPUT // (1024 * 1024)} MB", r.stderr
    if r.returncode != 0:
        how = (f"was killed by signal {-r.returncode}" if r.returncode < 0
               else f"exited with status {r.returncode}")
        return None, how, r.stderr
    try:
        text = r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None, "printed something that is not UTF-8", r.stderr
    if not text.strip():
        return None, "printed nothing on stdout", r.stderr
    try:
        return json.loads(text, parse_constant=_no_constant), None, r.stderr
    except ValueError as e:
        return None, f"did not print valid JSON ({e})", r.stderr


def _no_constant(name: str):
    # Python's json accepts NaN and Infinity, which are not JSON; a browser's
    # JSON.parse would refuse what the hub had accepted.
    raise ValueError(f"{name} is not valid JSON")


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
