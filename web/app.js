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
    return el('div', { cls: 'stat' },
      el('p', {}, el('span', { cls: 'stat-value' }, typeof d.value === 'number' ? fmtNumber(d.value) : d.value),
        d.unit ? el('span', { cls: 'stat-unit' }, ` ${d.unit}`) : null),
      d.label ? el('p', { cls: 'muted' }, d.label) : null);
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

  timeseries(d) {
    const points = [];
    for (const s of d.series) for (const p of s.points) if (p.v != null) points.push([Date.parse(p.t), p.v]);
    if (!points.length) return el('p', { cls: 'muted' }, 'No data yet.');
    let t0 = points.reduce((m, p) => Math.min(m, p[0]), Infinity);
    let t1 = points.reduce((m, p) => Math.max(m, p[0]), -Infinity);
    let v0 = points.reduce((m, p) => Math.min(m, p[1]), 0);
    let v1 = points.reduce((m, p) => Math.max(m, p[1]), -Infinity);
    if (t0 === t1) { t0 -= 3600e3; t1 += 3600e3; }
    if (v0 === v1) v1 = v0 + 1;
    const W = 560, H = 200, L = 56, R = 16, T = 12, B = 28;
    const x = t => L + (t - t0) / (t1 - t0) * (W - L - R);
    const y = v => T + (1 - (v - v0) / (v1 - v0)) * (H - T - B);
    const unit = d.unit ? ` ${d.unit}` : '';
    const chart = svg('svg', { viewBox: `0 0 ${W} ${H}`, cls: 'chart', role: 'img',
      'aria-label': `${d.series.map(s => s.name).join(', ')} over time` },
      svg('line', { x1: L, x2: W - R, y1: y(v0), y2: y(v0), cls: 'axis' }),
      svg('text', { x: L - 6, y: y(v1) + 4, 'text-anchor': 'end', cls: 'tick' }, fmtNumber(v1) + unit),
      svg('text', { x: L - 6, y: y(v0) + 4, 'text-anchor': 'end', cls: 'tick' }, fmtNumber(v0)),
      svg('text', { x: L, y: H - 8, cls: 'tick' }, new Date(t0).toLocaleDateString()),
      svg('text', { x: W - R, y: H - 8, 'text-anchor': 'end', cls: 'tick' }, new Date(t1).toLocaleDateString()));
    d.series.forEach((s, i) => {
      let path = '', pen = false;
      for (const p of s.points) {
        if (p.v == null) { pen = false; continue; }  // a gap breaks the line
        path += `${pen ? 'L' : 'M'}${x(Date.parse(p.t)).toFixed(1)} ${y(p.v).toFixed(1)}`;
        pen = true;
      }
      if (path) chart.append(svg('path', { d: path, cls: `series series-${i % 6}` }));
    });
    const legend = d.series.length > 1
      ? el('ul', { cls: 'legend' }, d.series.map((s, i) => el('li', { cls: `series-${i % 6}` }, s.name)))
      : null;
    return el('div', {}, chart, legend);
  },
};

function renderPayload(p, body, meta) {
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
  if (p.data !== undefined && RENDER[p.kind]) body.append(RENDER[p.kind](p.data));
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

  const load = async () => {
    try { renderPayload(await api(url), body, meta); }
    catch (err) { body.replaceChildren(el('p', { cls: 'error' }, err.message)); }
  };
  load();
  timers.push(setInterval(() => { if (!document.hidden) load(); }, Math.max(10, source.refresh_seconds) * 1000));
  return card;
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
    card.append(el('iframe', { cls: 'view', title: s.title,
      src: `${s.view}?source=${encodeURIComponent(s.key)}` }));
  } else if ((s.state === 'working' || s.state === 'stale') && s.widgets.length) {
    card.append(el('div', { cls: 'widgets' }, s.widgets.map(w => widgetCard(s, w))));
  }
  return card;
}

async function status(refresh) {
  try {
    const st = await api('/api/status' + (refresh ? '?refresh=1' : ''));
    linkable = new Set(st.linkable || []);
    document.getElementById('updated').textContent =
      `hub ${st.hub.version} · checked ${new Date().toLocaleTimeString()}`;
    renderProblems(st.problems || []);
    return st;
  } catch (err) {
    showBanner(`Could not reach the hub: ${err.message}`);
    return null;
  }
}

function renderProblems(problems) {
  const box = document.getElementById('problems');
  box.replaceChildren(...problems.map(p => el('div', { cls: 'note note-broken' }, el('p', {}, p))));
  box.hidden = !problems.length;
}

// --- tabs: one per source, then what is not installed ------------------------

const NOT_INSTALLED = '#/not-installed';
const sourceHref = key => `#/source/${encodeURIComponent(key)}`;

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
  const tab = (href, label, state) => el('a', {
    cls: 'tab', href, 'aria-current': href === current ? 'page' : null,
    title: state ? STATE_LABEL[state] || state : null,
  }, state ? el('span', { cls: `dot state-${state}`, 'aria-hidden': 'true' }) : null, label);
  const tabs = orderedSources(st).map(s => tab(sourceHref(s.key), s.title, s.state));
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
  const valid = h => h && (h === NOT_INSTALLED ? st.available.length > 0
    : sources.some(s => sourceHref(s.key) === h));
  // An unknown or stale tab (a source that went away) falls back to the first.
  const hash = [wanted, remembered()].find(valid)
    || (sources.length ? sourceHref(sources[0].key) : NOT_INSTALLED);
  if (hash !== location.hash) history.replaceState(null, '', hash);
  remember(hash);
  renderTabs(st, hash);

  const panel = document.getElementById('panel');
  if (hash === NOT_INSTALLED) {
    panel.replaceChildren(el('section', { cls: 'source' },
      el('header', { cls: 'source-head' }, el('h2', {}, 'Not installed')),
      el('ul', { cls: 'available' }, st.available.map(a =>
        el('li', {}, el('strong', {}, a.name), a.description ? el('p', { cls: 'muted' }, a.description) : null,
          el('code', {}, a.install))))));
    return;
  }
  const s = sources.find(x => sourceHref(x.key) === hash);
  panel.replaceChildren(s ? sourceCard(s) : el('p', { cls: 'muted' }, 'No sources found.'));
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
