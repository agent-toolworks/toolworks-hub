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

## 0.14.0 — 2026-11-03

- **A Claude Code plugin.** `.claude-plugin/plugin.json` and a `/hub` command,
  which runs the plugin's own `scripts/hub open`. Install it from the
  agent-toolworks catalogue (`claude plugin install toolworks-hub@agent-toolworks`);
  a clone keeps working, with the same port, token and settings.
  `scripts/set-version` sets the plugin's version too, and `scripts/verify`
  checks that it agrees.

- README: lists the sources that ship in plugins, now including note-search
  0.2.0 and fleet-workspace 1.2.0 (repo-fleet ticket workspaces).

## 0.13.0 — 2026-11-03

- **Alerts when a session needs you.** The Claude Code tab has a Settings tab
  beside its Sessions: the hub watches every Claude Code session and plays a
  sound, shows a popup and, if asked, says it aloud when one needs your input
  (a permission prompt, a question), finishes a turn, or, in the background,
  fails; a session closing can alert too. Like agent-teller's: a sound per
  alert (the hub's tones, macOS's, or your own files), volume, repeats until
  answered, escalation to an alarm after a few minutes, quiet hours, mute for
  15 minutes or an hour, and a test alert. The hub plays the sounds (`afplay`,
  `say`); each open hub page shows the popups as browser notifications, and
  macOS does when none is open. "Keep the hub running while alerts are on"
  keeps the server up with no page open. Settings are in
  `~/.config/agent-toolworks/alerts.json`.

## 0.12.0 — 2026-11-02

- **`tab_order` in `hub.json` sets the order of the tabs** (#11), sources and
  link pages alike, and so which tab a new visitor opens:
  `{"tab_order": ["guide", "tickets"]}`. Tabs it does not name follow in the
  order they had; a source that is not working stays at the end even when
  named, and a name that matches nothing is a problem shown on the page.
  `/api/status` has the order as `tabs`, and `hub status` prints a TABS line
  and lists sources and pages in that order.

## 0.11.0 — 2026-10-26

- **The page is as wide as the window.** A tab's header is a bar across the
  top, under the tabs, instead of a card in a 1200-pixel column; the
  code-search view lost its 1400-pixel limit too.
- **Session tables.** Any table whose rows link to sessions -- Claude Code's
  Sessions and token-save's Recent sessions alike -- shows each session's
  status, its title (linking to its page) and App, Browser and Terminal
  buttons, before the source's own columns. Buttons per status filter the
  rows, with All; the table refreshes every 5 or 10 minutes, can be paused,
  and has Refresh now. See "Session tables and the session page" in
  docs/contract.md.
- **Opening a session**: App resumes it in the Claude app (`claude --desktop
  --resume`), or opens a running bridged one at its `claude://` link;
  Browser opens its claude.ai session; Terminal opens a Terminal window
  running `claude --resume` in its folder (`claude attach` for a live
  background session). `"terminal": "iTerm"` in hub.json uses iTerm.
- **The session page** has a bar with the session's title, status and the
  same buttons, then tabs: General (the Claude source), token-save, and
  Transcript (the conversation's text and tool uses, 200 entries at a time).
- **Claude Code's Sessions table** lists every live session as well as the
  20 most recently active, and has no Account, App or Web column; the
  separate Background sessions table is gone, its sessions now in this one
  with their status. The session page's General tab is one list of facts,
  the message count among them.

## 0.10.0 — 2026-10-26

- **Views are kept across tab switches** (#10). A source's view is loaded
  once and hidden, not rebuilt, when another tab is shown, so coming back
  finds it as it was left (scroll, input, state), with no new first call.
  The hub sends the view `{type: "hub:visibility", visible}` when it is
  hidden or shown, so a view can pause its polling; Refresh reloads only the
  view on screen. Up to six hidden views are kept, the least recently shown
  dropped first.
- **`hub bg list` gives each row's `pid` and `kind`** (#8), so a source can
  tell a live session from a stopped one, and `--all-kinds` adds the
  sessions started in a terminal (`kind: "interactive"`, `id` null, keyed by
  `session_id`). The default list is unchanged otherwise.
- **`hub bg resume SESSION_ID`** (#9) wakes a stopped background session
  under its own id: it runs `claude --bg --resume <full session id>` and
  nothing else, refuses a short id (which would start a copy), and prints
  `copy: true` with the new ids if Claude Code started a copy anyway.

## 0.9.0 — 2026-10-25

- **Background sessions.** `hub bg` lists Claude Code background sessions
  (`claude --bg`) from every Claude Code folder, starts one in a folder, and
  stops, removes or finishes one, printing JSON. A source runs it as
  `$TOOLWORKS_HUB bg …` from `run call`, so every source that starts sessions
  gets the same binary lookup, environment and links (#7).
- **Open a session in the Claude app or on claude.ai.** The Claude Code tab
  lists background sessions with their state, and a session bridged to
  claude.ai by Remote Control has App and Web links there and in Recent
  sessions.
- **Source contract 4: a table cell can be a link**, `{"link", "label"}`, to
  an `https://`, `http://` or `claude://` URL. The hub still reads sources
  written for 1 to 3. A `claude://` link in a view is opened from the hub's
  page, which hands it to the Claude app.

## 0.8.0 — 2026-10-23

- **A view tab fills the window.** A tab that shows a source's view lays out
  like its pop-out but keeps the hub's header and tabs: the source's bar, then
  the view across the full width and down to the bottom of the window. Only the
  view scrolls, not the hub page around it. Tabs of widgets are unchanged.

## 0.7.0 — 2026-10-23

- **Every Claude Code account on the machine.** The hub reads its own Claude
  Code folder first, then each `~/.claude*` folder that holds `projects/` (a
  second account's `~/.claude-work`, say), or the folders `hub.json`'s new
  `claude_dirs` lists; `"claude_dirs": []` turns that off. The Claude Code
  source shows sessions, sessions today and plugins from all of them, with an
  Account column when there is more than one, and a session page finds its
  transcript in any of them. A plugin installed in any folder has its source,
  the hub's own folder winning one installed in several. Every source gets
  `TOOLWORKS_CLAUDE_DIRS`, so token-save shows each account's quota (#5).
  `hub status` lists the folders.

## 0.6.0 — 2026-10-19

- **A source's stats share one compact strip.** Each stat is a single line --
  title, value, and for a percentage a bar like the top bar's, with the time
  until it resets -- and its label and exact times are in its tooltip. When
  they were fetched is said once for the strip.

## 0.5.0 — 2026-10-18

- **A timeseries chart has a grid and shows its values on hover.** Dashed
  lines mark round value steps (0, 25, 50, 75, 100 % for a share) and five
  times along the axis. Pointing at the chart marks the nearest reading with a
  line and a dot on each series, and a box gives its time and each value.

## 0.4.1 — 2026-10-17

- **Fix: a link in a view to another site opens in a new tab.** It opened in
  the view's frame, where most sites refuse to show, and the view was lost.
  The hub now opens every `http(s)` link to another site from a view in a new
  browser tab; links within the view work as before.

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
