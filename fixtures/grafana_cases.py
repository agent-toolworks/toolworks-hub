#!/usr/bin/env python3
"""Cases for examples/grafana, run by scripts/verify against a fake Grafana.

The fake answers POST /api/ds/query on 127.0.0.1 the way Grafana does, with
two data frames, and records the Authorization header it was sent, so the
cases can check the token reached Grafana and went nowhere else. No real
Grafana, network or Keychain is involved: GRAFANA_TOKEN stands in for the
Keychain, and the cases run with HOME pointed at an empty folder.

Usage: grafana_cases.py ROOT TMPDIR
Prints `ok <what>` or `bad <what>` lines for verify's tally.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT, TMP = sys.argv[1], sys.argv[2]
RUN = os.path.join(ROOT, "examples", "grafana", "run")
T0 = 1792195200000  # ms; 2026-10-17T00:00:00Z
seen = {}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        seen["auth"] = self.headers.get("Authorization")
        seen["body"] = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if self.path != "/api/ds/query":
            self.send_response(404)
            self.end_headers()
            return
        if seen["body"]["queries"][0]["expr"] == "fail":
            body = {"results": {"A": {"error": "parse error at char 1"}}}
        else:
            frame = lambda job, vals: {"schema": {"fields": [
                {"name": "Time", "type": "time"},
                {"name": "Value", "type": "number", "labels": {"job": job}}]},
                "data": {"values": [[T0, T0 + 60000, T0 + 120000], vals]}}
            body = {"results": {"A": {"frames": [frame("api", [0.21, None, 0.25]), frame("web", [0.5, 0.4, 0.3])]}}}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d" % server.server_address[1]

home = os.path.join(TMP, "grafana-home")
os.makedirs(home, exist_ok=True)


def config(path, **kw):
    cfg = {"url": url, "datasource_uid": "uid1", "query": "up", "unit": "s"}
    cfg.update(kw)
    with open(path, "w") as f:
        json.dump(cfg, f)
    return path


def run(*args, token="tok-123", cfg=None):
    env = {"PATH": os.environ["PATH"], "HOME": home, "GRAFANA_SOURCE_CONFIG": cfg or os.path.join(TMP, "none.json")}
    if token:
        env["GRAFANA_TOKEN"] = token
    p = subprocess.run([RUN, *args], env=env, capture_output=True, text=True, timeout=30)
    try:
        value = json.loads(p.stdout) if p.stdout.strip() else None
    except ValueError:
        value = None
    return p.returncode, value, p.stderr


def check(ok, good, bad):
    print(("ok " if ok else "bad ") + (good if ok else bad))


good = config(os.path.join(TMP, "grafana.json"))
rc, d, err = run("detect", cfg=os.path.join(TMP, "none.json"))
check(d and d["state"] == "not_set_up" and "config.example.json" in d.get("fix", ""),
      "grafana: no config is not set up, and says to copy the example", "no config: %s %s" % (d, err))
rc, d, err = run("detect", cfg=good, token=None)
check(d and d["state"] == "not_set_up" and "security add-generic-password" in d.get("fix", ""),
      "grafana: no token is not set up, and names the Keychain command", "no token: %s %s" % (d, err))
seen.clear()
rc, d, err = run("detect", cfg=good)
check(d == {"state": "working"} and not seen, "grafana: detect with config and token is working, without a network call",
      "detect: %s %s seen=%s" % (d, err, seen))

rc, d, err = run("fetch", "series", cfg=good)
names = [s["name"] for s in (d or {}).get("data", {}).get("series", [])]
pts = (d or {}).get("data", {}).get("series", [{}])[0].get("points", [])
check(rc == 0 and names == ["job=api", "job=web"] and pts[0] == {"t": "2026-10-17T00:00:00Z", "v": 0.21}
      and pts[1]["v"] is None and d["data"]["unit"] == "s",
      "grafana: frames become one series per label set, a gap stays a gap", "series: %s %s" % (d, err))
check(seen.get("auth") == "Bearer tok-123" and seen.get("body", {}).get("queries", [{}])[0].get("datasource") == {"uid": "uid1"},
      "grafana: the token is sent as a bearer header, to the configured Grafana only", "request: %s" % seen)
rc, d, err = run("fetch", "latest", cfg=good)
check(rc == 0 and d["data"]["value"] == 0.25 and d["as_of"] == "2026-10-17T00:02:00Z",
      "grafana: latest is the last non-empty point, with when it was true", "latest: %s %s" % (d, err))
rc, d, err = run("fetch", "series", cfg=config(os.path.join(TMP, "grafana-fail.json"), query="fail"))
check(rc == 1 and d is None and "parse error" in err,
      "grafana: a query Grafana refuses is an error, not an empty chart", "refused query: rc=%s %s %s" % (rc, d, err))
rc, d, err = run("fetch", "series", cfg=config(os.path.join(TMP, "grafana-down.json"), url="http://127.0.0.1:9"))
check(rc == 1 and d is None and "could not query Grafana" in err,
      "grafana: an unreachable Grafana is an error, not an empty chart", "unreachable: rc=%s %s %s" % (rc, d, err))

env = dict(os.environ, HOME=home, GRAFANA_SOURCE_CONFIG=good, GRAFANA_TOKEN="tok-123")
p = subprocess.run([os.path.join(ROOT, "scripts", "hub"), "check-source", os.path.join(ROOT, "examples", "grafana")],
                   env=env, capture_output=True, text=True, timeout=120)
check(p.returncode == 0 and "0 failed" in p.stdout, "grafana: check-source passes against the fake",
      "check-source: %s" % (p.stdout[-600:] + p.stderr[-300:]))
server.shutdown()
