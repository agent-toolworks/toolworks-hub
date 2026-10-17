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

- **New: "update available" in the top bar.** The running hub asks its
  checkout's origin for release tags (`git ls-remote --tags`, with your git
  credentials, tag names only) at start and every 6 hours, and shows a newer
  release beside its own version, linked to the release notes. Set
  `"check_updates": false` in hub.json to turn it off.

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
