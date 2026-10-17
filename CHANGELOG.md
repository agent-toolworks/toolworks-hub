# Changelog

One version for the hub, and a new one for every release. Add a line under
**Unreleased** with each change. `scripts/set-version X.Y.Z` moves
**Unreleased** under the new version and sets the number in `hub/version.py`
and `sources/claude/source.json` (minor for a new feature or behaviour, patch
for a fix alone). Commit, tag `vX.Y.Z`, push the tag, and publish the release
with `scripts/release-notes`:

```sh
gh release create vX.Y.Z --title vX.Y.Z --notes-file <(scripts/release-notes X.Y.Z)
```

`scripts/verify` checks that the version has a section here.

## Unreleased

## 0.4.0 — 2026-10-17

- **New: a tab on its own page.** A tab's URL plus `/full`, such as
  `#/source/code-search/full`, shows that tab alone, filling the window, with
  no top bar or tab strip; a view fills the whole height. A pop-out icon beside
  each tab opens it that way in a new browser tab.

## 0.3.1 — 2026-10-17

- **Fix: a view can ship its fonts.** The contract tells a view to bundle its
  fonts, but `.woff2` and `.woff` files were a 404. They are now served, as
  `font/woff2` and `font/woff`. (#4)

## 0.3.0 — 2026-10-17

- **New: a source's own page.** `"page": "page/index.html"` in a source's
  `source.json` was checked and then ignored. The hub now serves the source's
  `page/` folder in place and shows the page as the source's tab, the way it
  shows the code-search view, and `run call` works for it. A view, the
  source's or the hub's, may now have files in folders, names with capitals
  or `_`, and `.json` and image files. (#3)

## 0.2.0 — 2026-10-17

- **New: a tab per source.** The page was one long column of every source's
  widgets. Each source now has its own tab, with a dot for its state, and a
  last tab lists the agent-toolworks plugins that are not installed. Only the
  open tab fetches. The open tab is in the URL and remembered.
- **New: sources from your own folders.** `source_dirs` in
  `~/.config/agent-toolworks/hub.json` names folders to load sources from,
  such as a git repo of a team's sources. Each source also gets
  `TOOLWORKS_SOURCE_CONFIG`, a settings folder outside the source's own, so
  per-person settings stay out of the repo.
- **New: code-search's page is in the hub.** Search, Fleet and Questions, the
  page code-search shipped as `cs serve` until 1.30.0, is the code-search
  tab, answered through code-search's `hub-source/run call`. Hits from one
  repo show as one block however they arrive (#1).
- **New: link pages.** A JSON file in `~/.config/agent-toolworks/pages/` (or a
  folder in hub.json's `page_dirs`) is a tab of links in sections, such as
  Grafana alert views. See `docs/custom-pages.md` and
  `examples/pages/grafana-alerts.json`.
- **New: 5h and 7d quota bars in the top bar**, from token-save, with the time
  left until each window resets.
- **New: quota over time.** Charts can offer time ranges as buttons; token-save
  0.28.0's quota chart shows the 5h and 7d quota from 6h to 30d (#2).
- **Source contract 3.** Version 2 adds a stat's `resets_at`; version 3 adds a
  timeseries widget's `ranges`. The hub reads contracts 1, 2 and 3, so no
  source has to change.
- **Changed: a fixed port.** The hub always listens on 127.0.0.1:7437 (or
  hub.json's `port`), and keeps its token between starts, so
  `http://127.0.0.1:7437/` can be bookmarked.
- **New: a favicon, and the hub's version in the top bar**, linked to its
  release.

## 0.1.0 — 2026-10-17

The first release: the hub server and `scripts/hub` (`open`, `status`,
`check-source`, `stop`), source contract 1 and its checker, discovery of the
built-in Claude Code source, plugins' `hub-source/` folders and
`~/.config/agent-toolworks/sources/`, component states, session pages built
from every source that knows a session, and an example Grafana source.
