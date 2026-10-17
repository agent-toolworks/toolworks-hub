"use strict";
// The hub frames this page with ?source=<key>: the source whose `run call`
// answers it.
const SOURCE = new URLSearchParams(location.search).get("source") || "code-search";

// Grouping only: every tool the server lists is shown, and one not named here
// lands under "Other", so a verb added to cs-mcp appears with no change here.
const GROUPS = [
  ["Strings across repos", ["cs_uses", "cs_seam", "cs_text", "cs_calls", "cs_history", "cs_gaps"]],
  ["Symbols", ["cs_def", "cs_impls", "cs_refs", "cs_callers", "cs_callees", "cs_impact", "cs_fields", "cs_constructs"]],
  ["Config & contracts", ["cs_values", "cs_strictness", "cs_resilience"]],
  ["Packages & owners", ["cs_provides", "cs_publishes", "cs_deps", "cs_versions", "cs_owns"]],
  ["This setup", ["cs_repos", "cs_scopes", "cs_engines", "cs_doctor", "cs_which", "cs_why", "cs_changes"]],
];
// What a NEGATIVE of each kind is worth -- the MCP instructions' rule: a
// resolved no is strong, a textual no is weak. The rest: read `cs why`.
const STRENGTH = { resolved: "strong", textual: "weak", heuristic: "weak" };
const STALE_DAYS = 7;

let TOOLS = {}, REPOS = {}, current = null, inflight = null, tab = "search";
let FLEET = null, fleetSort = { key: "name", dir: 1 };
const $ = (id) => document.getElementById(id);

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false)
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}
function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
function ago(ts, now) {
  if (!ts) return "";
  const s = Math.max(0, (now || Date.now() / 1000) - ts);
  if (s < 90) return "just now";
  if (s < 5400) return Math.round(s / 60) + "m ago";
  if (s < 129600) return Math.round(s / 3600) + "h ago";
  if (s < 86400 * 60) return Math.round(s / 86400) + "d ago";
  return Math.round(s / (86400 * 30)) + "mo ago";
}
const when = (ts) => ts ? new Date(ts * 1000).toLocaleString() : "";

// Every answer comes from `run call <op> <args>` in code-search's hub-source,
// through the hub. A failure is an {error} like any refusal, never a throw,
// except when the hub itself does not answer.
async function api(op, args, signal) {
  const url = "/api/sources/" + encodeURIComponent(SOURCE) + "/call/" + op +
    "?args=" + encodeURIComponent(JSON.stringify(args || {}));
  const r = await fetch(url, { credentials: "same-origin", cache: "no-store", signal });
  const j = await r.json().catch(() => ({ error: "the hub answered HTTP " + r.status + " with no JSON" }));
  if (!r.ok && !j.error) j.error = "HTTP " + r.status;
  if (j.error && (j.details || []).length) j.error += "\n" + j.details.join("\n");
  return j;
}
const scope = () => $("scope").value;

// ---- tabs and the URL ----------------------------------------------------
// Everything lives in the fragment, so Back works and a search can be copied
// as a link -- and the query never reaches a server log.
function setTab(name) {
  tab = name;
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false");
  for (const t of ["search", "fleet", "questions"]) $("tab-" + t).hidden = t !== name;
  if (name === "fleet" && !FLEET) loadFleet();
  if (name === "search") $("q").focus();
}
function pushHash(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "" && v !== false) p.set(k, v === true ? "1" : v);
  p.set("scope", scope());
  const h = "#" + p.toString();
  if (location.hash !== h) history.pushState(null, "", h);
}
function fromLocation() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("scope") && [...$("scope").options].some((o) => o.value === p.get("scope")) && p.get("scope") !== scope()) {
    $("scope").value = p.get("scope"); scopeChanged(false);
  }
  const t = p.get("tab") || (p.get("tool") ? "questions" : "search");
  setTab(t);
  if (t === "search") {
    $("q").value = p.get("q") || "";
    $("o-regex").checked = p.get("regex") === "1";
    $("o-word").checked = p.get("word") === "1";
    $("o-src").checked = p.get("src") === "1";
    setRepo(p.get("repo") || "");
    if ($("q").value) runSearch(false); else { $("sout").replaceChildren(); $("sstatus").textContent = ""; }
  } else if (t === "questions") {
    const tool = p.get("tool");
    if (tool && TOOLS[tool]) {
      const values = {};
      for (const f of props(TOOLS[tool])) if (p.has(f.name))
        values[f.name] = f.prop.type === "boolean" ? p.get(f.name) === "1" : p.get(f.name);
      selectTool(tool, values, true);
    } else selectTool(current || "cs_uses", {}, false);
  }
}

// ---- scope and repos -----------------------------------------------------
async function loadRepos() {
  const r = await api("repos", { scope: scope() }).catch(() => ({}));
  REPOS = r.repos || {};
  const names = Object.keys(REPOS).sort();
  $("repo-names").replaceChildren(...names.map((n) => el("option", { value: n })));
  const sel = $("srepo"), keep = sel.dataset.want || sel.value;
  sel.replaceChildren(el("option", { value: "" }, "All repos (" + names.length + ")"),
    ...names.map((n) => el("option", { value: n }, n + (REPOS[n].layer === "ticket" ? "  · your branch" : ""))));
  sel.value = names.includes(keep) ? keep : "";
}
function setRepo(name) { $("srepo").dataset.want = name; $("srepo").value = name; }
async function scopeChanged(user) {
  // A scope picked on the page goes into the URL too, or Back / reload would
  // quietly put the old one back while the page showed the new one.
  if (user) {
    const p = new URLSearchParams(location.hash.slice(1));
    p.set("scope", scope());
    history.replaceState(null, "", "#" + p.toString());
  }
  store("code-search-scope", scope());
  FLEET = null;
  await loadRepos();
  if (tab === "fleet") loadFleet();
  if (user && tab === "search" && $("q").value.trim()) runSearch(true);
}

// ---- Search tab ----------------------------------------------------------
// One box. It runs `cs text` (literal unless "regex" is ticked) and, when the
// query is a bare identifier, `cs def` beside it so definitions come first.
// The repo menu narrows INSIDE cs (--repo=), before the result cap.
const IDENT = /^[A-Za-z_$][\w$]*$/;
// Cosmetic only: the highlight compiles the query as a JavaScript RegExp, but
// the search ran in the text engine's flavour (ripgrep, or grep -E). Where they
// differ -- \<, POSIX classes, \b under --word -- a hit can show unhighlighted
// or with a different span marked. The hit list is cs's answer; the marks are not.
function highlighter(q, regex, word) {
  let re = null;
  try {
    const src = regex ? q : q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    re = new RegExp(word ? "\\b(?:" + src + ")\\b" : src, "g");
  } catch (e) { return null; }
  return (text) => {
    const out = []; let last = 0, m; re.lastIndex = 0;
    while ((m = re.exec(text)) !== null) {
      if (m[0] === "") { re.lastIndex++; continue; }
      out.push(text.slice(last, m.index), el("mark", {}, m[0])); last = m.index + m[0].length;
    }
    out.push(text.slice(last));
    return out;
  };
}

async function runSearch(push) {
  const q = $("q").value.trim();
  if (!q) return;
  const repo = $("srepo").value, regex = $("o-regex").checked, word = $("o-word").checked, src = $("o-src").checked;
  if (push) pushHash({ tab: "search", q, repo, regex, word, src });
  if (inflight) inflight.abort();
  const ctl = new AbortController(); inflight = ctl;
  const out = $("sout"), defBox = el("div"), matchBox = el("div");
  out.replaceChildren(defBox, matchBox);
  const textArgs = { pattern: q, scope: scope(), literal: !regex, word, source_only: src, in_repo: repo || undefined };
  const t0 = performance.now();
  const tick = setInterval(() => { $("sstatus").textContent = "searching… " + Math.round((performance.now() - t0) / 1000) + "s"; }, 250);
  const hl = highlighter(q, regex, word);
  const defP = !regex && IDENT.test(q)
    ? api("ask", { tool: "cs_def", arguments: { symbol: q, scope: scope(), repo: repo || undefined } }, ctl.signal)
        .then((r) => showDefs(defBox, r, q)).catch(() => {})
    : Promise.resolve();
  try {
    const res = await api("ask", { tool: "cs_text", arguments: textArgs }, ctl.signal);
    matchBox.append(el("h3", { class: "section" }, "Matches"));
    show(matchBox, res, { tool: "cs_text", hl, onDoctor: () => openQuestion("cs_doctor") });
    await defP;
    $("sstatus").textContent = res.seconds !== undefined ? "took " + res.seconds + "s" : "";
  } catch (e) {
    $("sstatus").textContent = "";
    if (e.name !== "AbortError") out.replaceChildren(el("div", { class: "box bad" }, el("strong", {}, "The hub did not answer"),
      "Is it still running? `hub open` starts it again. " + e.message));
  } finally {
    clearInterval(tick);
    if (inflight === ctl) inflight = null;
  }
}

function showDefs(box, res, q) {
  const env = res.envelope;
  if (res.error || !env) return;
  if (env.refused) {
    box.append(el("p", { class: "note" }, "Definitions not looked up: " + (env.reason || "").split("\n")[0]));
    return;
  }
  const results = env.results || [];
  if (!results.length) return;
  box.append(el("h3", { class: "section" }, "Definitions of ", el("code", {}, q)),
    el("div", { class: "answer" }, el("span", { class: "chip " + (STRENGTH[env.kind] || "other") }, env.kind || "?"),
      el("span", { class: "muted" }, "via " + (env.engine || "?") + (env.note ? " (" + env.note + ")" : ""))));
  renderHits(box, results, res.repos || {}, highlighter(q, false, true));
}

// ---- rendering an answer -------------------------------------------------
// Same order as cs-mcp's render(): provenance, refusal or kind, every warning,
// then the hits -- warnings ABOVE the hits, because below is where they get
// skimmed past.
function show(out, res, opt) {
  opt = opt || {};
  if (res.error) {
    out.append(el("div", { class: "box bad" }, el("strong", {}, "Not run"), el("pre", {}, res.error)));
    return;
  }
  if (res.text !== undefined) { out.append(el("pre", { class: "plain" }, res.text)); return; }
  const env = res.envelope;
  if (env.view) out.append(el("div", { class: "prov" }, "searching: " + env.view));
  if (env.refused) {
    out.append(el("div", { class: "box bad" },
      el("strong", {}, "REFUSED: this search did not run, so nothing is ruled out."),
      el("pre", {}, env.reason || "(no reason given)"),
      el("p", { class: "after" }, el("button", { type: "button", onclick: opt.onDoctor || (() => openQuestion("cs_doctor")) },
        "Run cs doctor"), " to see what this machine is missing and the fix.")));
    return;
  }
  const strength = STRENGTH[env.kind] || "other";
  if (env.kind) {
    const bits = [env.hits != null ? env.hits + " hit(s)" : null, env.repos != null ? env.repos + " repo(s)" : null].filter(Boolean);
    out.append(el("div", { class: "answer" },
      el("span", { class: "chip " + strength, title: "the kind of evidence behind this answer" }, env.kind),
      el("span", {}, "via ", el("b", {}, env.engine || "?"), env.note ? " (" + env.note + ")" : ""),
      bits.length ? el("span", { class: "muted" }, "· " + bits.join(" · ")) : null,
      el("button", { class: "link", type: "button", onclick: (ev) => whyInline(env.kind, ev.target) },
        "what can a " + env.kind + " answer not see?")));
  }
  const warnings = [];
  if (env.degraded) warnings.push("degraded: " + env.degraded);
  if (env.partial) warnings.push("PARTIAL: an engine timed out or files could not be read. These results are a subset of the truth, not a complete answer.");
  if (env.truncated) warnings.push("TRUNCATED: " + env.returned + " of " + env.hits + " hits shown. " +
    (opt.tool === "cs_text" ? "Pick a repo, tick whole word, or make the query longer to see the rest." : "Narrow the query to see the rest."));
  for (const e of env.engine_errors || []) warnings.push("engine error: " + e);
  const sk = env.skipped || [];
  if (sk.length) warnings.push("could not read: " + sk.slice(0, 5).join(", ") + (sk.length > 5 ? " (+" + (sk.length - 5) + " more)" : ""));
  if (env.exclusions) warnings.push("non-default exclusion list in force: " + env.exclusions);
  if (env.elided) warnings.push(env.elided.lines + " hit line(s) longer than " + env.elided.max_line +
    " bytes were cut around the match. Open the file at the line shown for the rest.");
  for (const n of env.notes || []) warnings.push(n);
  if (warnings.length) out.append(el("div", { class: "box warn" }, el("ul", { class: "warnings" }, warnings.map((w) => el("li", {}, w)))));

  const results = env.results || [];
  if (!results.length) {
    const worth = strength === "strong" ? "A resolved negative is strong evidence."
      : strength === "weak" ? "A " + env.kind + " negative is weak evidence: read what it cannot see before concluding nothing exists."
      : env.kind ? "How much this is worth depends on the kind (" + env.kind + "): read what it cannot see before concluding nothing exists." : "";
    out.append(el("div", { class: "box empty" },
      el("strong", {}, "No hits. The search ran and found nothing: this is an answer, not a failure."), worth,
      (env.hints || []).length ? el("ul", { class: "hints" }, env.hints.map((h) => el("li", {}, "try: " + h))) : null));
    return;
  }
  renderHits(out, results, res.repos || {}, opt.hl);
}

function editorLink(abs, line) {
  const tpl = $("editor").value;
  if (!tpl || !abs) return null;
  return tpl.replace("{path}", encodeURI(abs)).replace("{line}", String(line));
}

function renderHits(out, results, repos, hl) {
  // Hits arrive grouped by repo already; consecutive runs are kept in order
  // rather than re-sorted, so the page shows what cs returned, as returned.
  const loose = [], groups = [];
  for (const r of results) {
    if (!r.path) { loose.push(r.text); continue; }
    let g = groups.length && groups[groups.length - 1].repo === r.repo ? groups[groups.length - 1] : null;
    if (!g) { g = { repo: r.repo, hits: [] }; groups.push(g); }
    g.hits.push(r);
  }
  if (loose.length) out.append(el("pre", { class: "plain" }, loose.join("\n")));
  for (const g of groups) {
    const info = repos[g.repo] || REPOS[g.repo] || {};
    const rows = g.hits.map((h) => {
      const abs = info.path ? info.path + "/" + h.path : null;
      const loc = h.path + ":" + h.line;
      const href = editorLink(abs, h.line);
      return el("div", { class: "hit" },
        href ? el("a", { href, title: abs + ":" + h.line }, loc) : el("span", { title: abs || "" }, loc),
        el("span", { class: "text" }, hl ? hl(h.text) : h.text),
        el("button", { type: "button", title: "copy the absolute path:line", onclick: (ev) => copy((abs || g.repo + "/" + h.path) + ":" + h.line, ev.target) }, "copy"));
    });
    out.append(el("details", { class: "repo", open: true },
      el("summary", {}, g.repo, info.layer ? el("span", { class: "chip " + info.layer,
        title: info.layer === "ticket" ? "from your ticket workspace (your branch)" : "from the fleet (main)" }, info.layer) : null,
        el("span", { class: "count" }, g.hits.length + " hit(s)")),
      el("div", { class: "hits" }, rows)));
  }
}

async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); btn.textContent = "copied"; }
  catch (e) { btn.textContent = "copy failed"; }
  setTimeout(() => (btn.textContent = "copy"), 1200);
}

async function whyInline(kind, btn) {
  if (btn.dataset.done) return;
  btn.dataset.done = "1"; btn.textContent = "loading…";
  const res = await api("ask", { tool: "cs_why", arguments: { kind } });
  btn.replaceWith(el("pre", { class: "plain wide" }, res.text || res.error || ""));
}

// ---- Fleet tab -----------------------------------------------------------
const COLS = [
  { key: "name", label: "Repo" },
  { key: "branch", label: "Branch" },
  { key: "committed", label: "Last commit" },
  { key: "synced", label: "Synced" },
  { key: "files", label: "Files", num: true },
  { key: "symbols", label: "Symbols", num: true },
];

async function loadFleet() {
  $("fstatus").textContent = "reading the repos…";
  $("ftable").replaceChildren();
  const r = await api("fleet", { scope: scope() }).catch((e) => ({ error: e.message }));
  if (r.error) { $("fstatus").replaceChildren(el("span", { class: "stale" }, r.error)); return; }
  FLEET = r;
  $("fstatus").textContent = "";
  renderFleet();
}

function renderFleet() {
  if (!FLEET) return;
  const f = $("ffilter").value.trim().toLowerCase();
  const rows = FLEET.repos.filter((r) => !f || r.name.toLowerCase().includes(f) || (r.branch || "").toLowerCase().includes(f));
  const { key, dir } = fleetSort;
  rows.sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
    return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
  });
  const total = FLEET.repos.length;
  $("fcount").textContent = (rows.length === total ? total : rows.length + " of " + total) + " repo(s)";
  const head = el("tr", {}, COLS.map((c) => el("th", {
    class: c.num ? "num" : null, "aria-sort": key === c.key ? (dir > 0 ? "ascending" : "descending") : null,
    onclick: () => { fleetSort = { key: c.key, dir: key === c.key ? -dir : (c.num || c.key === "committed" || c.key === "synced" ? -1 : 1) }; renderFleet(); },
  }, c.label)));
  const now = FLEET.now;
  const body = rows.map((r) => {
    const stale = r.synced && now - r.synced > STALE_DAYS * 86400;
    return el("tr", {},
      el("td", {}, el("button", { class: "link repo-name", type: "button", title: "search in " + r.name + "\n" + r.path,
        onclick: () => { setRepo(r.name); setTab("search"); pushHash({ tab: "search", repo: r.name }); } }, r.name), " ",
        el("span", { class: "chip " + r.layer, title: r.layer === "ticket" ? "your ticket workspace" : "the fleet clone" }, r.layer),
        r.web ? [" ", el("a", { href: r.web, target: "_blank", rel: "noopener", class: "muted", title: r.web }, "↗")] : null),
      el("td", {}, r.branch || el("span", { class: "muted" }, "—")),
      el("td", { class: "commit" }, r.commit ? [
        r.commit_url ? el("a", { class: "sha", href: r.commit_url, target: "_blank", rel: "noopener", title: r.commit }, r.commit.slice(0, 8))
                     : el("span", { class: "sha", title: r.commit + "\n(no web remote to link to)" }, r.commit.slice(0, 8)),
        r.subject, " ", el("span", { class: "muted", title: when(r.committed) + " · " + (r.author || "") }, "· " + ago(r.committed, now))]
        : el("span", { class: "muted" }, "no commits")),
      el("td", { title: r.synced ? (r.synced_how === "cloned" ? "cloned, never fetched since: " : "last fetch: ") + when(r.synced) : "no fetch or clone recorded" },
        r.synced ? el("span", { class: stale ? "stale" : null }, ago(r.synced, now) + (r.synced_how === "cloned" ? " (clone)" : "")) : el("span", { class: "muted" }, "unknown")),
      el("td", { class: "num" }, (r.files || 0).toLocaleString()),
      el("td", { class: "num", title: r.graph_built ? "tokensave graph built " + ago(r.graph_built, now) + " (" + when(r.graph_built) + ")" : (r.graph_error || "no tokensave graph in this repo") },
        r.symbols != null ? r.symbols.toLocaleString() : el("span", { class: "muted" }, r.graph_error ? "graph error" : "no graph")));
  });
  $("ftable").replaceChildren(el("thead", {}, head), el("tbody", {}, body));
}

// ---- Questions tab -------------------------------------------------------
function buildNav() {
  const nav = $("nav");
  const groups = GROUPS.map(([g, names]) => [g, names.filter((n) => TOOLS[n])]);
  const other = Object.keys(TOOLS).filter((n) => !GROUPS.some(([, ns]) => ns.includes(n)));
  if (other.length) groups.push(["Other", other]);
  for (const [g, names] of groups) {
    if (!names.length) continue;
    nav.append(el("h2", {}, g));
    for (const n of names)
      nav.append(el("button", { type: "button", "data-tool": n, title: TOOLS[n].description,
        onclick: () => { pushHash({ tab: "questions", tool: n }); selectTool(n, {}, false); } }, n.replace(/^cs_/, "")));
  }
}
function props(tool) {
  const s = tool.inputSchema || {};
  return Object.entries(s.properties || {}).filter(([k]) => k !== "scope")
    .map(([k, p]) => ({ name: k, prop: p, required: (s.required || []).includes(k) }));
}
function openQuestion(name) { setTab("questions"); pushHash({ tab: "questions", tool: name }); selectTool(name, {}, true); }

function selectTool(name, values, run) {
  const tool = TOOLS[name];
  if (!tool) return;
  current = name;
  for (const b of document.querySelectorAll("nav button"))
    b.setAttribute("aria-current", b.dataset.tool === name ? "true" : "false");
  $("title").textContent = name.replace(/^cs_/, "cs ");
  $("desc").textContent = tool.description;
  const form = $("qform");
  form.replaceChildren();
  const fields = props(tool);
  for (const f of fields.filter((f) => f.prop.type !== "boolean")) {
    form.append(el("label", { class: "field" + (fields.indexOf(f) === 0 ? " grow" : ""), title: f.prop.description },
      el("span", {}, el("b", {}, f.name), f.required ? "" : " (optional)"),
      el("input", { type: "text", name: f.name, required: f.required, placeholder: f.required ? "" : "optional",
        value: values[f.name] || "", list: /repo$/.test(f.name) ? "repo-names" : null })));
  }
  for (const f of fields.filter((f) => f.prop.type === "boolean"))
    form.append(el("label", { class: "check", title: f.prop.description },
      el("input", { type: "checkbox", name: f.name, checked: !!values[f.name] }), f.name.replace(/_/g, " ")));
  form.append(el("div", { class: "actions" },
    el("button", { class: "primary", type: "submit" }, fields.length ? "Search" : "Run"),
    el("button", { type: "button", id: "stop", hidden: true, onclick: () => inflight && inflight.abort() }, "Stop waiting")));
  if (!run) { $("qout").replaceChildren(); $("qstatus").textContent = ""; }
  const first = form.querySelector("input[type=text]");
  if (first && !run) first.focus();
  if (run) runQuestion(false);
}

function readForm() {
  const args = {};
  for (const i of $("qform").querySelectorAll("input")) {
    if (i.type === "checkbox") { if (i.checked) args[i.name] = true; }
    else if (i.value.trim() !== "") args[i.name] = i.value.trim();
  }
  return args;
}

async function runQuestion(push) {
  const tool = TOOLS[current];
  const args = readForm();
  if (push) pushHash(Object.assign({ tab: "questions", tool: current }, args));
  if ((tool.inputSchema.properties || {}).scope) args.scope = scope();
  if (inflight) inflight.abort();
  const ctl = new AbortController(); inflight = ctl;
  const t0 = performance.now();
  $("qout").replaceChildren();
  $("stop").hidden = false;
  const tick = setInterval(() => {
    $("qstatus").textContent = "running " + current.replace(/^cs_/, "cs ") + " … " +
      Math.round((performance.now() - t0) / 1000) + "s (a language server's first run can take minutes)";
  }, 250);
  try {
    const res = await api("ask", { tool: current, arguments: args }, ctl.signal);
    show($("qout"), res, { tool: current, onDoctor: () => openQuestion("cs_doctor") });
    $("qstatus").textContent = res.seconds !== undefined ? "took " + res.seconds + "s" : "";
  } catch (e) {
    if (e.name === "AbortError") {
      $("qstatus").textContent = "Stopped waiting. cs keeps running on its own until it finishes or times out; its answer is discarded.";
    } else {
      $("qout").replaceChildren(el("div", { class: "box bad" }, el("strong", {}, "The hub did not answer"),
        "Is it still running? `hub open` starts it again. " + e.message));
      $("qstatus").textContent = "";
    }
  } finally {
    clearInterval(tick);
    if (inflight === ctl) inflight = null;
    if ($("stop")) $("stop").hidden = true;
  }
}

// ---- start ---------------------------------------------------------------
async function start() {
  const m = await api("meta");
  if (m.error) {
    // code-search up to 1.29.0 has no `run call`: it shipped its own page then.
    const old = /unknown command: call/.test(m.error);
    $("sout").append(el("div", { class: "box bad" }, el("strong", {}, "code-search did not answer"),
      old ? "This page needs code-search 1.30.0 or later. Update it with /plugin update code-search@agent-toolworks."
          : el("pre", {}, m.error)));
    return;
  }
  for (const t of m.tools) TOOLS[t.name] = t;
  $("version").textContent = "cs " + m.version;
  const sel = $("scope");
  // A workspace is shown by its folder name, with as many parent folders as it
  // takes to tell two apart; the full path is the value and the tooltip.
  const short = (p, n) => p.split("/").filter(Boolean).slice(-n).join("/");
  const paths = m.scopes.filter((s) => s.value !== "fleet").map((s) => s.value);
  const label = (p) => { for (let n = 1; ; n++) { const l = short(p, n);
    if (paths.filter((q) => short(q, n) === l).length === 1 || l === short(p, 99)) return l; } };
  // The hub has no working directory to start from, so the page opens on the
  // scope picked last time, else the fleet.
  const last = store("code-search-scope");
  const start = m.scopes.some((s) => s.value === last) ? last : "fleet";
  for (const s of m.scopes) {
    const here = s.value === start;
    sel.append(el("option", { value: s.value, selected: here, title: s.label },
      (s.value === "fleet" ? "fleet only (main) · " + s.repos + " repo(s)"
        : label(s.value) + " (" + s.repos + " repo(s), your branch) + fleet")));
  }
  if (!m.scopes.length) sel.append(el("option", { value: "fleet" }, "fleet"));
  const ed = store("code-search-editor");
  if (ed !== null && [...$("editor").options].some((o) => o.value === ed)) $("editor").value = ed;
  $("editor").addEventListener("change", () => store("code-search-editor", $("editor").value));
  sel.addEventListener("change", () => scopeChanged(true));
  for (const b of document.querySelectorAll(".tabs button"))
    b.addEventListener("click", () => { setTab(b.dataset.tab); pushHash(b.dataset.tab === "questions" && current ? { tab: "questions", tool: current } : { tab: b.dataset.tab }); });
  $("sform").addEventListener("submit", (ev) => { ev.preventDefault(); runSearch(true); });
  for (const id of ["o-regex", "o-word", "o-src", "srepo"])
    $(id).addEventListener("change", () => { $("srepo").dataset.want = $("srepo").value; if ($("q").value.trim()) runSearch(true); });
  $("qform").addEventListener("submit", (ev) => { ev.preventDefault(); runQuestion(true); });
  $("ffilter").addEventListener("input", renderFleet);
  $("frefresh").addEventListener("click", loadFleet);
  buildNav();
  await loadRepos();
  window.addEventListener("popstate", fromLocation);
  window.addEventListener("keydown", (ev) => {
    if (ev.key === "/" && !/input|select|textarea/i.test(document.activeElement.tagName)) {
      ev.preventDefault();
      const i = tab === "search" ? $("q") : tab === "fleet" ? $("ffilter") : $("qform").querySelector("input[type=text]");
      if (i) i.focus();
    }
  });
  fromLocation();
}

// Show the tab the URL names straight away, rather than Search for the moment
// the tool list takes to arrive; fromLocation() fills it in once it has.
{
  const p = new URLSearchParams(location.hash.slice(1));
  const t = p.get("tab") || (p.get("tool") ? "questions" : "search");
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", b.dataset.tab === t ? "true" : "false");
  for (const n of ["search", "fleet", "questions"]) $("tab-" + n).hidden = n !== t;
}
start();
