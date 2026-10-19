'use strict';
// The hub's one page. Everything a source prints reaches the page through
// textContent or attributes, never innerHTML: source output is data.

const STATE_LABEL = {
  working: 'Working', stale: 'Update waiting', not_set_up: 'Not set up', off: 'Off',
  incompatible: 'Incompatible', broken: 'Broken', not_installed: 'Not installed',
};
const SVG = 'http://www.w3.org/2000/svg';
let timers = [];
let linkable = new Set();  // entity types some working source can show

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  setAttrs(node, attrs);
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    node.append(kid instanceof Node ? kid : String(kid));
  }
  return node;
}

function svg(tag, attrs, ...kids) {
  const node = document.createElementNS(SVG, tag);
  setAttrs(node, attrs);
  for (const kid of kids.flat()) {
    if (kid == null) continue;
    node.append(kid instanceof Node ? kid : String(kid));
  }
  return node;
}

function setAttrs(node, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    node.setAttribute(k === 'cls' ? 'class' : k, v === true ? '' : String(v));
  }
}

async function api(path) {
  const r = await fetch(path, { credentials: 'same-origin', cache: 'no-store' });
  const body = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}

function ago(iso) {
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (!isFinite(s)) return '';
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

function fmtTime(iso) {
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

// 9:39 AM today, Oct 18, 9:39 AM before: short enough for a line of stats.
function fmtClock(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString(undefined, { timeStyle: 'short' })
    : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function fmtNumber(v) {
  return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function entityHref(ref) {
  return `#/entity/${encodeURIComponent(ref.ref)}/${encodeURIComponent(ref.id)}`;
}

function refText(ref, text) {
  // A link only when an installed, working source shows this type; otherwise
  // plain text, as the contract says, never a link that leads nowhere.
  const label = text || ref.label || ref.id;
  return linkable.has(ref.ref)
    ? el('a', { cls: 'ref', href: entityHref(ref), title: `${ref.ref} ${ref.id}` }, label)
    : el('span', { cls: 'ref', title: `${ref.ref} ${ref.id}` }, label);
}

function cell(v) {
  if (v == null) return el('span', { cls: 'muted' }, '—');
  if (typeof v === 'object') return refText(v);
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'number') return fmtNumber(v);
  return v;
}

// --- widget renderers --------------------------------------------------------

const RENDER = {
  stat(d) {
    const resets = d.resets_at ? new Date(d.resets_at) : null;
    return el('div', { cls: 'stat' },
      el('p', {}, el('span', { cls: 'stat-value' }, typeof d.value === 'number' ? fmtNumber(d.value) : d.value),
        d.unit ? el('span', { cls: 'stat-unit' }, ` ${d.unit}`) : null),
      d.label ? el('p', { cls: 'muted' }, d.label) : null,
      resets && !isNaN(resets) ? el('p', { cls: 'muted', title: fmtTime(d.resets_at) },
        'resets in ', el('span', { cls: 'quota-left', 'data-resets': resets.getTime() })) : null);
  },

  table(d) {
    if (!d.rows.length) return el('p', { cls: 'muted' }, 'Nothing to show.');
    const head = el('tr', {}, d.columns.map(c => el('th', {}, c.unit ? `${c.title} (${c.unit})` : c.title)));
    const rows = d.rows.map(row => el('tr', {}, d.columns.map(c =>
      el('td', { cls: typeof row[c.id] === 'number' ? 'num' : null }, cell(row[c.id])))));
    return el('div', { cls: 'table-wrap' }, el('table', {}, el('thead', {}, head), el('tbody', {}, rows)));
  },

  list(d) {
    if (!d.items.length) return el('p', { cls: 'muted' }, 'Nothing to show.');
    return el('ul', { cls: 'list' }, d.items.map(i =>
      el('li', {}, i.ref ? refText(i.ref, i.text) : el('span', {}, i.text),
        i.detail ? el('span', { cls: 'muted' }, i.detail) : null)));
  },

  search(d) {
    if (!d.results.length) return el('p', { cls: 'muted' }, 'No results.');
    return el('ul', { cls: 'results' }, d.results.map(r =>
      el('li', {},
        el('div', {}, el('strong', {}, r.ref ? refText(r.ref, r.title) : r.title),
          r.evidence ? el('span', { cls: 'badge' }, r.evidence) : null),
        r.location ? el('code', {}, r.location) : null,
        r.detail ? el('p', { cls: 'muted' }, r.detail) : null)));
  },

  // span: when the widget has ranges, [from, to] in ms -- the axis covers it
  // whether or not there are points at its start, so a gap reads as a gap.
  timeseries(d, span) {
    const inSpan = t => !span || (t >= span[0] && t <= span[1]);
    const points = [];
    for (const s of d.series) for (const p of s.points) {
      if (p.v != null && inSpan(Date.parse(p.t))) points.push([Date.parse(p.t), p.v]);
    }
    if (!points.length) return el('p', { cls: 'muted' }, span ? 'No data in this range.' : 'No data yet.');
    let t0 = span ? span[0] : points.reduce((m, p) => Math.min(m, p[0]), Infinity);
    let t1 = span ? span[1] : points.reduce((m, p) => Math.max(m, p[0]), -Infinity);
    let v0 = points.reduce((m, p) => Math.min(m, p[1]), 0);
    let v1 = points.reduce((m, p) => Math.max(m, p[1]), -Infinity);
    if (d.unit === '%' && v1 <= 100) v1 = 100;  // a share reads against the whole
    if (t0 === t1) { t0 -= 3600e3; t1 += 3600e3; }
    if (v0 === v1) v1 = v0 + 1;
    const step = niceStep(v1 - v0);
    v0 = Math.floor(v0 / step) * step;
    v1 = Math.ceil(v1 / step) * step;
    const tick = t => fmtTick(t, t1 - t0);
    const W = 560, H = 200, L = 56, R = 16, T = 12, B = 28;
    const x = t => L + (t - t0) / (t1 - t0) * (W - L - R);
    const y = v => T + (1 - (v - v0) / (v1 - v0)) * (H - T - B);
    const unit = d.unit ? ` ${d.unit}` : '';
    const chart = svg('svg', { viewBox: `0 0 ${W} ${H}`, cls: 'chart', role: 'img',
      'aria-label': `${d.series.map(s => s.name).join(', ')} over time` });
    // The grid: a line at each value step, and at each of five times.
    for (let i = 0, v = v0; v <= v1 + step / 2; v = v0 + ++i * step) {
      chart.append(svg('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), cls: i ? 'grid' : 'axis' }),
        svg('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end', cls: 'tick' }, fmtNumber(+v.toFixed(6)) + unit));
    }
    for (let i = 0; i <= 4; i++) {
      const t = t0 + i / 4 * (t1 - t0);
      chart.append(svg('line', { x1: x(t), x2: x(t), y1: T, y2: H - B, cls: 'grid' }),
        svg('text', { x: x(t), y: H - 8, 'text-anchor': i ? i === 4 ? 'end' : 'middle' : 'start', cls: 'tick' }, tick(t)));
    }
    d.series.forEach((s, i) => {
      let path = '', pen = false;
      for (const p of s.points) {
        if (!inSpan(Date.parse(p.t))) continue;
        if (p.v == null) { pen = false; continue; }  // a gap breaks the line
        path += `${pen ? 'L' : 'M'}${x(Date.parse(p.t)).toFixed(1)} ${y(p.v).toFixed(1)}`;
        pen = true;
      }
      if (path) chart.append(svg('path', { d: path, cls: `series series-${i % 6}` }));
    });
    const legend = d.series.length > 1
      ? el('ul', { cls: 'legend' }, d.series.map((s, i) => el('li', { cls: `series-${i % 6}` }, s.name)))
      : null;
    const wrap = el('div', { cls: 'chart-wrap' }, chart);
    hoverValues(wrap, chart, d, inSpan, { x, y, W, T, bottom: H - B, unit });
    return el('div', {}, wrap, legend);
  },
};

// A round step that splits a value range into about four parts: 1, 2, 2.5 or
// 5 times a power of ten (0-100 % gives 0, 25, 50, 75, 100).
function niceStep(range) {
  const raw = range / 4, mag = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
}

// Pointing at a chart shows the readings at the nearest time any series has:
// a line at that time, a dot on each series and a box with the values.
function hoverValues(wrap, chart, d, inSpan, g) {
  const byTime = new Map();
  d.series.forEach((s, i) => {
    for (const p of s.points) {
      const t = Date.parse(p.t);
      if (p.v == null || !inSpan(t)) continue;
      if (!byTime.has(t)) byTime.set(t, []);
      byTime.get(t)[i] = p.v;
    }
  });
  const times = [...byTime.keys()].sort((a, b) => a - b);
  const cross = svg('line', { y1: g.T, y2: g.bottom, cls: 'crosshair', visibility: 'hidden' });
  const dots = d.series.map((s, i) => svg('circle', { r: 3.5, cls: `dot-mark series-${i % 6}`, visibility: 'hidden' }));
  chart.append(cross, ...dots);
  const tip = el('div', { cls: 'chart-tip', hidden: true });
  wrap.append(tip);
  const hide = () => {
    tip.hidden = true;
    cross.setAttribute('visibility', 'hidden');
    dots.forEach(c => c.setAttribute('visibility', 'hidden'));
  };
  chart.addEventListener('pointerleave', hide);
  chart.addEventListener('pointermove', e => {
    const box = chart.getBoundingClientRect();
    const scale = box.width / g.W;
    const at = (e.clientX - box.left) / scale;
    let lo = 0, hi = times.length - 1;  // the first time at or right of the pointer
    while (lo < hi) { const mid = (lo + hi) >> 1; if (g.x(times[mid]) < at) lo = mid + 1; else hi = mid; }
    if (lo > 0 && at - g.x(times[lo - 1]) < g.x(times[lo]) - at) lo--;
    const t = times[lo], vals = byTime.get(t), px = g.x(t);
    cross.setAttribute('x1', px); cross.setAttribute('x2', px);
    cross.setAttribute('visibility', 'visible');
    dots.forEach((c, i) => {
      if (vals[i] == null) { c.setAttribute('visibility', 'hidden'); return; }
      c.setAttribute('cx', px); c.setAttribute('cy', g.y(vals[i]));
      c.setAttribute('visibility', 'visible');
    });
    tip.replaceChildren(el('div', { cls: 'muted' }, fmtTime(new Date(t).toISOString())),
      ...d.series.flatMap((s, i) => vals[i] == null ? []
        : [el('div', { cls: `series-${i % 6}` }, el('span', { cls: 'tip-name' }, s.name), `${fmtNumber(vals[i])}${g.unit}`)]));
    tip.hidden = false;
    const left = px * scale, right = px * scale > box.width / 2;
    tip.style.left = right ? '' : `${left + 12}px`;
    tip.style.right = right ? `${box.width - left + 12}px` : '';
    tip.style.top = `${g.T * scale}px`;
  });
}

// A time on a chart's axis: the hour up to 12 hours, the day and hour up to a
// week (24h would otherwise start and end on the same hour), the date beyond.
function fmtTick(t, spanMs) {
  const d = new Date(t);
  if (spanMs <= 12 * 3600e3) return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  if (spanMs <= 7 * 86400e3) return d.toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' });
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

// "6h" / "7d" (contract 3's ranges) in milliseconds.
function rangeMs(r) {
  const m = /^([1-9][0-9]*)([hd])$/.exec(r);
  return m ? Number(m[1]) * (m[2] === 'h' ? 3600e3 : 86400e3) : null;
}

// The range buttons above a timeseries widget that lists ranges. The choice
// is kept per widget, in this browser only.
function rangeBar(key, ranges, onPick) {
  let current = ranges[0];
  try { const saved = localStorage.getItem(key); if (ranges.includes(saved)) current = saved; } catch { /* no storage */ }
  const bar = el('div', { cls: 'ranges', role: 'group', 'aria-label': 'Time range' });
  const draw = () => bar.replaceChildren(...ranges.map(r => {
    const b = el('button', { type: 'button', 'aria-pressed': r === current ? 'true' : 'false' }, r);
    b.addEventListener('click', () => {
      current = r;
      try { localStorage.setItem(key, r); } catch { /* the choice lasts this page only */ }
      draw();
      onPick();
    });
    return b;
  }));
  draw();
  return { bar, span: () => [Date.now() - rangeMs(current), Date.now()] };
}

function renderPayload(p, body, meta, range) {
  body.replaceChildren();
  if (p.error) {
    const when = p.error.at ? ` ${ago(p.error.at)}` : '';
    const err = el('div', { cls: 'error' }, el('p', {}, `Last fetch failed${when}: ${p.error.problem}`));
    if (p.error.details && p.error.details.length) {
      err.append(el('details', {}, el('summary', {}, 'Details'), el('pre', {}, p.error.details.join('\n'))));
    }
    body.append(err);
  }
  if (p.notice) body.append(el('p', { cls: 'notice' }, p.notice));
  if (p.data !== undefined && RENDER[p.kind]) body.append(RENDER[p.kind](p.data, range && range.span()));
  tickLeft();
  meta.textContent = [p.as_of && `as of ${fmtTime(p.as_of)}`, p.fetched_at && `fetched ${ago(p.fetched_at)}`]
    .filter(Boolean).join(' · ');
}

function widgetCard(source, w) {
  const body = el('div', { cls: 'widget-body' }, el('p', { cls: 'muted' }, 'Loading…'));
  const meta = el('p', { cls: 'widget-meta muted' });
  const card = el('article', { cls: `widget widget-${w.kind}` }, el('h3', {}, w.title), body, meta);
  const url = `/api/sources/${encodeURIComponent(source.key)}/widgets/${encodeURIComponent(w.id)}`;

  if (w.kind === 'search') {
    const input = el('input', { type: 'search', placeholder: 'Search…', 'aria-label': w.title });
    const results = el('div');
    const form = el('form', { cls: 'search' }, input, el('button', { type: 'submit' }, 'Search'));
    form.addEventListener('submit', async e => {
      e.preventDefault();
      if (!input.value.trim()) return;
      results.replaceChildren(el('p', { cls: 'muted' }, 'Searching…'));
      try { renderPayload(await api(`${url}?query=${encodeURIComponent(input.value)}`), results, meta); }
      catch (err) { results.replaceChildren(el('p', { cls: 'error' }, err.message)); }
    });
    body.replaceChildren(form, results);
    return card;
  }

  // A timeseries with ranges keeps its last payload, so a new range redraws
  // at once without fetching: fetch already returned the longest range.
  let last = null;
  const range = w.kind === 'timeseries' && w.ranges
    ? rangeBar(`hub.range.${source.key}.${w.id}`, w.ranges, () => last && renderPayload(last, body, meta, range))
    : null;
  if (range) card.insertBefore(range.bar, body);
  const load = async () => {
    try { last = await api(url); renderPayload(last, body, meta, range); }
    catch (err) { body.replaceChildren(el('p', { cls: 'error' }, err.message)); }
  };
  load();
  timers.push(setInterval(() => { if (!document.hidden) load(); }, Math.max(10, source.refresh_seconds) * 1000));
  return card;
}

// A source's stat widgets share one strip, a line each at most, like the top
// bar's quota bars: a stat's label and its times go to the tooltip, and when
// they were fetched is said once, for the oldest of them.
function statStrip(source, ws) {
  const meta = el('span', { cls: 'widget-meta muted' });
  const last = new Map();
  const items = ws.map(w => {
    const item = el('div', { cls: 'stat-item' }, el('span', { cls: 'stat-title' }, w.title),
      el('span', { cls: 'muted' }, '…'));
    const url = `/api/sources/${encodeURIComponent(source.key)}/widgets/${encodeURIComponent(w.id)}`;
    const show = p => {
      last.set(w.id, p);
      statLine(item, w.title, p);
      tickLeft();
      const oldest = key => [...last.values()].map(q => q[key]).filter(Boolean)
        .sort((a, b) => Date.parse(a) - Date.parse(b))[0];
      const asOf = oldest('as_of'), fetched = oldest('fetched_at');
      meta.textContent = [asOf && `as of ${fmtClock(asOf)}`, fetched && `fetched ${ago(fetched)}`]
        .filter(Boolean).join(' · ');
      meta.title = asOf ? `as of ${fmtTime(asOf)}` : '';
    };
    const load = () => api(url).then(show, err => show({ error: { problem: err.message } }));
    load();
    timers.push(setInterval(() => { if (!document.hidden) load(); }, Math.max(10, source.refresh_seconds) * 1000));
    return item;
  });
  return el('article', { cls: 'widget widget-stats' }, ...items, meta);
}

function statLine(item, title, p) {
  const d = p.data || {};
  const resets = d.resets_at ? new Date(d.resets_at) : null;
  const hasResets = resets && !isNaN(resets);
  const pct = d.unit === '%' && typeof d.value === 'number' ? Math.max(0, Math.min(100, d.value)) : null;
  const parts = [];
  if (p.error) {
    parts.push(el('span', { cls: 'error' }, 'could not read'));
  } else if (pct != null) {
    // A percentage with a reset is a quota, coloured as the top bar's are.
    const level = !hasResets ? 'plain' : pct >= 90 ? 'bad' : pct >= 70 ? 'warn' : 'good';
    const fill = el('span', { cls: `quota-fill quota-${level}` });
    fill.style.width = `${pct}%`;
    parts.push(el('span', { cls: 'quota-track', role: 'meter', 'aria-label': title,
      'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': pct }, fill),
    el('span', { cls: 'stat-num' }, `${fmtNumber(d.value)}%`));
  } else if (d.value !== undefined) {
    parts.push(el('span', { cls: 'stat-num' }, typeof d.value === 'number' ? fmtNumber(d.value) : d.value),
      d.unit ? el('span', { cls: 'muted' }, d.unit) : null);
  }
  if (hasResets) parts.push(el('span', { cls: 'quota-left', 'data-resets': resets.getTime() }));
  if (p.notice) parts.push(el('span', { cls: 'notice', title: p.notice }, '!'));
  item.replaceChildren(el('span', { cls: 'stat-title' }, title), ...parts);
  item.title = [d.label, hasResets && `resets ${fmtTime(d.resets_at)}`,
    p.error && `could not read: ${p.error.problem}`, p.notice].filter(Boolean).join('\n');
}

function sourceCard(s) {
  const card = el('section', { cls: 'source' },
    el('header', { cls: 'source-head' },
      el('h2', {}, s.title),
      el('span', { cls: `badge state state-${s.state}` }, STATE_LABEL[s.state] || s.state),
      el('span', { cls: 'badge', title: s.plugin || s.dir }, s.origin),
      s.network ? el('span', { cls: 'badge', title: 'This source makes network calls' }, 'network') : null,
      s.version ? el('span', { cls: 'muted' }, s.version) : null));
  if (s.state !== 'working') {
    const note = el('div', { cls: `note note-${s.state}` }, el('p', {}, s.reason || STATE_LABEL[s.state]));
    if (s.fix) note.append(el('p', {}, 'Fix: ', el('code', {}, s.fix)));
    if (s.details && s.details.length) note.append(el('pre', {}, s.details.join('\n')));
    card.append(note);
  }
  if ((s.state === 'working' || s.state === 'stale') && s.view) {
    // The hub's own page for this source, in place of its widgets.
    const frame = el('iframe', { cls: 'view', title: s.title,
      src: `${s.view}?source=${encodeURIComponent(s.key)}` });
    frame.addEventListener('load', () => outsideLinksInNewTabs(frame));
    card.append(frame);
  } else if ((s.state === 'working' || s.state === 'stale') && s.widgets.length) {
    // The stats, in one strip where the first of them was.
    const stats = s.widgets.filter(w => w.kind === 'stat');
    card.append(el('div', { cls: 'widgets' }, s.widgets.map(w =>
      w.kind !== 'stat' ? widgetCard(s, w) : w === stats[0] ? statStrip(s, stats) : null)));
  }
  return card;
}

// A link in a view to another site opens in a new browser tab, not in the
// frame, where most sites refuse to show and the view would be lost. A view is
// on the hub's origin, so its clicks can be seen here; one the page handles
// itself (preventDefault), a modified click and a link with a target are left
// to the browser.
function outsideLinksInNewTabs(frame) {
  let doc;
  try { doc = frame.contentDocument; } catch { return; }  // the frame left the hub's origin
  if (!doc) return;
  doc.addEventListener('click', e => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || a.target || typeof a.href !== 'string') return;
    let url;
    try { url = new URL(a.href); } catch { return; }
    if (!/^https?:$/.test(url.protocol) || url.origin === location.origin) return;
    e.preventDefault();
    window.open(url.href, '_blank', 'noopener,noreferrer');
  });
}

async function status(refresh) {
  try {
    const st = await api('/api/status' + (refresh ? '?refresh=1' : ''));
    linkable = new Set(st.linkable || []);
    const v = document.getElementById('version');
    v.textContent = `v${st.hub.version}`;
    v.href = st.hub.release_url;
    v.title = `toolworks-hub ${st.hub.version}, source contract ${st.hub.contract}: release notes`;
    v.hidden = false;
    document.getElementById('updated').textContent = `checked ${new Date().toLocaleTimeString()}`;
    renderProblems(st.problems || []);
    renderQuota(st);
    return st;
  } catch (err) {
    showBanner(`Could not reach the hub: ${err.message}`);
    return null;
  }
}

// --- the top bar's quota bars, from token-save ------------------------------
// token-save's source reads Claude Code's 5-hour and 7-day rate-limit windows
// (its five-hour and seven-day widgets). Shown whenever token-save is working,
// whichever tab is open; hidden when it is not installed or not working.

const QUOTAS = [['five-hour', '5h'], ['seven-day', '7d']];
let quotaTimer = null, leftTimer = null;
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

// When the window resets: the stat's resets_at (contract 2). A source still
// on contract 1 -- token-save up to 0.26.0 -- says it only in the label
// ("used; resets Sat 12:40", strftime "%a %H:%M" in local time), read as the
// next such weekday and time.
function resetsOf(d) {
  const t = d.resets_at ? new Date(d.resets_at) : null;
  return t && !isNaN(t) ? t : resetsAt(d.label);
}

function resetsAt(label, now = new Date()) {
  const m = /resets (Sun|Mon|Tue|Wed|Thu|Fri|Sat) (\d{1,2}):(\d{2})/.exec(label || '');
  if (!m) return null;
  const t = new Date(now);
  t.setHours(Number(m[2]), Number(m[3]), 0, 0);
  for (let i = 0; i < 8; i++, t.setDate(t.getDate() + 1)) {
    if (WEEKDAYS[t.getDay()] === m[1] && t > now) return t;
  }
  return null;
}

// 23m, 4h5m, 2d6h: the two largest units, as the top bar has room for.
function timeLeft(ms) {
  const min = Math.max(0, Math.ceil(ms / 60000));
  const d = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60), m = min % 60;
  return d ? `${d}d${h}h` : h ? `${h}h${m}m` : `${m}m`;
}

function tickLeft() {
  for (const node of document.querySelectorAll('.quota-left[data-resets]')) {
    node.textContent = timeLeft(Number(node.dataset.resets) - Date.now());
  }
}

function renderQuota(st) {
  const box = document.getElementById('quota');
  const ts = st.sources.find(s => s.name === 'token-save');
  const ids = ts && (ts.state === 'working' || ts.state === 'stale')
    ? QUOTAS.filter(([id]) => ts.widgets.some(w => w.id === id)) : [];
  clearInterval(quotaTimer);
  clearInterval(leftTimer);
  box.hidden = !ids.length;
  if (!ids.length) return;
  const load = () => Promise.all(ids.map(([id, label]) =>
    api(`/api/sources/${encodeURIComponent(ts.key)}/widgets/${id}`)
      .then(p => quotaBar(ts, label, p), err => quotaBar(ts, label, { error: { problem: err.message } }))))
    .then(bars => { box.replaceChildren(...bars); tickLeft(); });
  load();
  quotaTimer = setInterval(() => { if (!document.hidden) load(); }, Math.max(10, ts.refresh_seconds) * 1000);
  leftTimer = setInterval(tickLeft, 30000);  // the countdown moves between fetches
}

function quotaBar(ts, label, p) {
  const d = p.data || {};
  const pct = typeof d.value === 'number' ? Math.max(0, Math.min(100, d.value)) : null;
  const level = pct == null ? 'none' : pct >= 90 ? 'bad' : pct >= 70 ? 'warn' : 'good';
  const why = p.error ? `could not read: ${p.error.problem}` : d.label || '';
  const resets = pct != null ? resetsOf(d) : null;
  const fill = el('span', { cls: `quota-fill quota-${level}` });
  if (pct != null) fill.style.width = `${pct}%`;
  return el('a', { cls: 'quota-item', href: sourceHref(ts.key),
    title: `${label} quota${pct != null ? `: ${pct}% used` : ''}${why ? ` (${why})` : ''}` },
    el('span', { cls: 'quota-label' }, label),
    el('span', { cls: 'quota-track', role: 'meter', 'aria-label': `${label} quota`,
      'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': pct }, fill),
    el('span', { cls: 'quota-value' }, pct != null ? `${pct}%` : '—'),
    resets ? el('span', { cls: 'quota-left', 'data-resets': resets.getTime(),
      title: `resets ${resets.toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })}` }) : null);
}

function renderProblems(problems) {
  const box = document.getElementById('problems');
  box.replaceChildren(...problems.map(p => el('div', { cls: 'note note-broken' }, el('p', {}, p))));
  box.hidden = !problems.length;
}

// --- tabs: one per source, then what is not installed ------------------------

const NOT_INSTALLED = '#/not-installed';
const FULL = '/full';  // a tab's hash plus this shows the tab alone, filling the window
const sourceHref = key => `#/source/${encodeURIComponent(key)}`;
const pageHref = name => `#/page/${encodeURIComponent(name)}`;

function splitFull(hash) {
  return hash && hash.endsWith(FULL) ? { tab: hash.slice(0, -FULL.length), full: true } : { tab: hash, full: false };
}

// The pop-out icon beside a tab: the tab on a page of its own, in a new browser tab.
function popout(href, label) {
  const icon = svg('svg', { viewBox: '0 0 24 24', width: 14, height: 14, 'aria-hidden': 'true' },
    svg('path', { d: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5' }));
  return el('a', { cls: 'popout', href: href + FULL, target: '_blank', rel: 'noopener',
    title: `Open ${label} in a new tab`, 'aria-label': `Open ${label} in a new tab` }, icon);
}

function remember(hash) {
  try { localStorage.setItem('hub.tab', hash); } catch { /* no storage: start at the first tab */ }
}

function remembered() {
  try { return localStorage.getItem('hub.tab'); } catch { return null; }
}

function orderedSources(st) {
  // Working sources first, so the first tab has something to show.
  const order = s => (s.state === 'working' || s.state === 'stale' ? 0 : 1);
  return [...st.sources].sort((a, b) => order(a) - order(b));
}

function renderTabs(st, current) {
  const tab = (href, label, state) => el('span', { cls: 'tab-item' }, el('a', {
    cls: 'tab', href, 'aria-current': href === current ? 'page' : null,
    title: state ? STATE_LABEL[state] || state : null,
  }, state ? el('span', { cls: `dot state-${state}`, 'aria-hidden': 'true' }) : null, label), popout(href, label));
  const tabs = orderedSources(st).map(s => tab(sourceHref(s.key), s.title, s.state));
  for (const p of st.pages || []) tabs.push(tab(pageHref(p.name), p.title));
  if (st.available.length) tabs.push(tab(NOT_INSTALLED, `Not installed (${st.available.length})`));
  document.getElementById('tabs').replaceChildren(...tabs);
  const active = document.querySelector('.tab[aria-current]');
  if (active) active.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function showView(name) {
  document.getElementById('entity').hidden = name !== 'entity';
  document.getElementById('panel').hidden = name !== 'panel';
}

function stopTimers() {
  timers.forEach(clearInterval);
  timers = [];
}

async function loadEntity(type, id, refresh) {
  stopTimers();
  document.body.classList.remove('full');
  document.title = 'Toolworks Hub';
  showView('entity');
  const view = document.getElementById('entity');
  const back = () => el('p', {}, el('a', { href: remembered() || '#/' }, '← Back'));
  view.replaceChildren(back(), el('p', { cls: 'muted' }, 'Loading…'));
  const st = await status(refresh);
  if (st) renderTabs(st, null);
  let page;
  try {
    page = await api(`/api/entities/${encodeURIComponent(type)}/${encodeURIComponent(id)}`);
  } catch (err) {
    view.replaceChildren(back(), el('p', { cls: 'error' }, err.message));
    return;
  }
  const head = el('header', { cls: 'entity-head' }, back(),
    el('h2', {}, page.title, ' ', el('code', {}, page.id)));
  const sections = page.sections.map(sec => {
    const card = el('section', { cls: 'source' }, el('header', { cls: 'source-head' }, el('h2', {}, sec.title)));
    if (sec.error) {
      card.append(el('div', { cls: 'note note-broken' }, el('p', {}, sec.error.problem),
        sec.error.details && sec.error.details.length ? el('pre', {}, sec.error.details.join('\n')) : null));
    } else {
      card.append(el('div', { cls: 'widgets' }, sec.widgets.map(w => {
        const body = el('div', { cls: 'widget-body' });
        const meta = el('p', { cls: 'widget-meta muted' });
        renderPayload(w, body, meta);
        return el('article', { cls: `widget widget-${w.kind}` }, el('h3', {}, w.title), body, meta);
      })));
    }
    return card;
  });
  view.replaceChildren(head, ...(sections.length ? sections
    : [el('p', { cls: 'muted' }, `No installed source has anything to show for this ${page.type}.`)]));
}

async function loadTab(wanted, refresh) {
  stopTimers();
  showView('panel');
  const st = await status(refresh);
  if (!st) return;
  const sources = orderedSources(st);
  const linkPages = st.pages || [];
  const valid = h => h && (h === NOT_INSTALLED ? st.available.length > 0
    : sources.some(s => sourceHref(s.key) === h) || linkPages.some(p => pageHref(p.name) === h));
  // A tab on its own page is not remembered: the hub's own page keeps its tab.
  const { tab, full } = splitFull(wanted);
  // An unknown or stale tab (a source that went away) falls back to the first.
  const hash = [tab, remembered()].find(valid)
    || (sources.length ? sourceHref(sources[0].key) : NOT_INSTALLED);
  const shown = full ? hash + FULL : hash;
  if (shown !== location.hash) history.replaceState(null, '', shown);
  if (!full) remember(hash);
  document.body.classList.toggle('full', full);
  renderTabs(st, hash);
  const title = hash === NOT_INSTALLED ? 'Not installed'
    : (linkPages.find(p => pageHref(p.name) === hash) || sources.find(s => sourceHref(s.key) === hash) || {}).title;
  document.title = full && title ? `${title} · Toolworks Hub` : 'Toolworks Hub';

  const panel = document.getElementById('panel');
  if (hash === NOT_INSTALLED) {
    panel.replaceChildren(el('section', { cls: 'source' },
      el('header', { cls: 'source-head' }, el('h2', {}, 'Not installed')),
      el('ul', { cls: 'available' }, st.available.map(a =>
        el('li', {}, el('strong', {}, a.name), a.description ? el('p', { cls: 'muted' }, a.description) : null,
          el('code', {}, a.install))))));
    return;
  }
  const page = linkPages.find(p => pageHref(p.name) === hash);
  if (page) {
    panel.replaceChildren(linkPage(page));
    return;
  }
  const s = sources.find(x => sourceHref(x.key) === hash);
  panel.replaceChildren(s ? sourceCard(s) : el('p', { cls: 'muted' }, 'No sources found.'));
}

// A link page: sections of links from a JSON file (hub/pages.py). An http(s)
// link opens in a new browser tab; a #/ link is a page of the hub.
function linkPage(p) {
  const link = l => {
    const outside = !l.url.startsWith('#/');
    return el('li', {},
      el('a', { href: l.url, target: outside ? '_blank' : null, rel: outside ? 'noopener noreferrer' : null }, l.title),
      outside ? el('span', { cls: 'muted host' }, ` ${new URL(l.url).host}`) : null,
      l.description ? el('p', { cls: 'muted' }, l.description) : null);
  };
  return el('section', { cls: 'source' },
    el('header', { cls: 'source-head' }, el('h2', {}, p.title),
      el('span', { cls: 'badge', title: p.file }, 'page')),
    p.description ? el('p', { cls: 'muted' }, p.description) : null,
    el('div', { cls: 'link-sections' }, p.sections.map(sec =>
      el('article', { cls: 'widget' }, el('h3', {}, sec.title), el('ul', { cls: 'links' }, sec.links.map(link))))));
}

function route(refresh) {
  const m = location.hash.match(/^#\/entity\/([^/]+)\/([^/]+)$/);
  if (m) return loadEntity(decodeURIComponent(m[1]), decodeURIComponent(m[2]), refresh);
  return loadTab(location.hash, refresh);
}

function showBanner(text) {
  const b = document.getElementById('banner');
  b.textContent = text;
  b.hidden = false;
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('refresh').addEventListener('click', () => route(true));
  window.addEventListener('hashchange', () => route(false));
  route(false);
  // The heartbeat keeps the server from exiting while this page is open.
  setInterval(async () => {
    try { await api('/api/ping'); }
    catch { showBanner('The hub has stopped. Run `hub open` to start it again.'); }
  }, 60000);
});
