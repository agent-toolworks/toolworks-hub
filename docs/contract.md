# The source contract, version 3

A source is a folder the hub can show: a manifest saying what it offers and a
program that prints JSON. The hub's own sources and the ones you write follow
the same contract. The JSON Schemas in [`contract/`](../contract) are the
contract; this page explains them. To check a source against them:

```sh
scripts/hub check-source path/to/my-source --entity session=<a session id>
```

## The folder

```
my-source/
  source.json      the manifest
  run              any executable; prints JSON on stdout
  page/            optional: the source's own view (index.html and its files)
```

The hub looks for sources in these places only, in this order: inside the
hub, in a `hub-source/` folder inside each installed Claude Code plugin, in
`~/.config/agent-toolworks/sources/*/`, and in the folders listed in
`~/.config/agent-toolworks/hub.json` (below). It never downloads one. Names
are claimed in that order; a later source with a name already taken is shown
as broken.

### hub.json

```json
{"source_dirs": ["~/git/team-sources", "/opt/shared/hub-source"],
 "page_dirs": ["~/git/team-pages"]}
```

(`page_dirs` holds link pages, not sources: see
[custom-pages.md](custom-pages.md).)

Each folder in `source_dirs` that has a `source.json` is one source; any other
folder holds one source per subfolder (folders starting with `.` are
skipped). `~` is expanded, and a relative path is taken from the folder
`hub.json` is in. A folder that does not exist, an unknown setting or a file
that is not JSON is shown as a problem at the top of the page, and the rest
of the hub carries on.

## source.json

[`manifest.schema.json`](../contract/manifest.schema.json)

| Field | Required | Meaning |
| --- | --- | --- |
| `contract` | yes | `3` (or `2`, `1`), the contract version the source was written for. Any other value makes the source *incompatible*. |
| `name` | yes | Unique among sources: lowercase letters, digits and dashes. Used in URLs. |
| `title` | yes | Shown as the source's heading. |
| `version` | yes | The source's own version. |
| `network` | yes | Whether `run` makes network calls. Required so every source says; the hub shows it. |
| `widgets` | no | `[{id, kind, title}]`. `kind` is one of the shapes below. A `search` widget may add `example_query`, which check-source runs it with. A `timeseries` widget may add `ranges` (contract 3), such as `["6h", "24h", "7d", "30d"]`: the hub shows them as buttons, the first one first, and filters the points to the chosen range. `fetch` returns the longest range; the hub never asks for less. |
| `shows` | no | Entity types this source can add a section to, e.g. `["session"]`. Each must be in [`entities.json`](../contract/entities.json). |
| `refresh_seconds` | no | How often each widget is fetched again. Default 300, minimum 10. |
| `timeout_seconds` | no | Limit on each `fetch` and `show`. Default 10, 1 to 60. |
| `page` | no | The source's own view: an HTML file inside `page/`, such as `page/index.html`. See [Views](#views). |

Unknown fields are refused, so a misspelt field fails instead of being ignored.

## run

The hub runs `run` with one of three commands. It runs as the user, in the
source folder, with no stdin and the hub's environment plus
`TOOLWORKS_SOURCE_CONFIG`: the folder for this source's own settings,
`~/.config/agent-toolworks/source-config/<name>/`. The hub only names it; it
may not exist until the user creates it. Keep per-person settings there rather
than beside `run`, so a source shared in a git repo never holds them.

| Command | Prints | Limit |
| --- | --- | --- |
| `run detect` | the source's state ([`detect.schema.json`](../contract/detect.schema.json)) | 5 s; runs on every hub start |
| `run fetch <widget> [--query <text>]` | one widget's data ([`fetch.schema.json`](../contract/fetch.schema.json)); `--query` only for `search` | `timeout_seconds` |
| `run show <entity> <id>` | this source's section of an entity page ([`show.schema.json`](../contract/show.schema.json)) | `timeout_seconds` |

A source with a page (a *view*, below) also answers a fourth command, which
only that page uses:

| Command | Prints | Limit |
| --- | --- | --- |
| `run call <op> <json>` | a JSON object, in whatever shape the view reads | 600 s |

`<json>` is a JSON object of at most 64 KB. The hub checks the op name, the
arguments and that the answer is an object; the shape inside is between the
source and its view. A source the hub has no view for is never sent `call`.

Rules for every command:

- **Exit 0 and print one JSON value**, UTF-8, at most 5 MB. `NaN` and
  `Infinity` are not JSON and are refused.
- **A non-zero exit, a timeout, or output that breaks the schema makes the
  source *broken*.** The hub shows the problem and the last lines of stderr,
  so write diagnostics to stderr.
- **Leave nothing running.** Each command runs in its own process group, and
  the whole group is killed when the command exits or runs out of time.
- **Keep secrets inside the source.** Read a token from the Keychain or an
  environment variable; never put it in the manifest or the output.

## States

`run detect` prints `{"state", "reason", "fix", "tool_version"}`. Every state
except `working` needs a `reason`. `fix`, when given, is shown as the step that
fixes it; the hub never runs it.

| State | Use it when |
| --- | --- |
| `working` | The source can fetch. |
| `not_set_up` | The tool is installed but a setup step is missing (token-save with no history database). |
| `off` | The user turned it off on purpose (`TS_HISTORY=off`). Shown as a fact, not an error. |
| `incompatible` | The tool's own data is a version the source does not read. |
| `stale` | The tool works but an update is waiting. The hub still fetches. |
| `broken` | Something is wrong that the user should look at. |

There is no `not_installed`: a source that is not installed cannot run, so the
hub works that state out itself. When the state is not `working` (or `stale`),
the hub does not call `fetch` or `show`.

## Views

A view is one source's own page. When that source is working, its tab shows
the view, in a frame, instead of its widgets, and the view asks the source
questions through `/api/sources/<key>/call/<op>?args=<json>`, which runs
`run call`. The frame's URL carries `?source=<key>`.

A view comes from one of two places, and the first one found wins:

- **The source's own `page/` folder.** `"page": "page/index.html"` in
  `source.json` makes the hub serve `page/` in place, at
  `/sources/<key>/page/...`, while the source is working. The page ships and
  changes with the source.
- **The hub's `web/views/<source name>/`.** The page is the hub's code, so it
  changes with the hub; the source only answers. The code-search view (Search,
  Fleet and Questions over the fleet) is one: it was code-search's `cs serve`
  page before code-search 1.30.0.

Either way, files may sit in folders below the view's, and are served when
their names hold only letters, digits, `_`, `.` and `-` and do not start with
a dot, and they end in `.html`, `.js`, `.css`, `.json`, `.svg`, `.png`,
`.jpg`, `.jpeg`, `.gif`, `.webp`, `.woff2` or `.woff`. Anything else is a 404, and so is a link
that leads out of the folder. Views are served with `X-Frame-Options:
SAMEORIGIN` and a Content-Security-Policy of `default-src 'self'`, so a view
loads nothing from the network: bundle its scripts, styles and fonts, and
fetch remote data in `run call`.

A link in a view to another site (`http://` or `https://`, not the hub)
opens in a new browser tab, so the view stays in its frame; the hub does that
for every view, with no `target` needed. A link with its own `target`, a
click the page handles itself, and a page that sets `location` from script
are left alone: to open another site from script, use `window.open(url,
'_blank', 'noopener')`.

A source's page runs on the hub's origin, inside the hub's own page, so it can
do anything the hub's page can. That is no more than its `run` can already do
as you, but install a source with a page only from someone you trust.

## Widget shapes

[`widgets.schema.json`](../contract/widgets.schema.json). `fetch` prints
`{"kind", "data"}`, plus optional `as_of` (when the data was true, if not now)
and `notice` (a warning shown with the widget, such as a partial answer). The
printed `kind` must match the manifest's. Times are ISO 8601 with an offset:
`2026-10-17T09:00:00Z`, never a bare local time.

```json
{"kind": "stat", "data": {"value": 4, "unit": "sessions", "label": "since midnight"}}

{"kind": "stat", "data": {"value": 93, "unit": "%", "label": "used", "resets_at": "2026-10-18T12:40:00+03:00"}}

{"kind": "timeseries", "data": {"unit": "USD", "series": [
  {"name": "cost", "points": [{"t": "2026-10-16T00:00:00Z", "v": 1.5}, {"t": "2026-10-17T00:00:00Z", "v": null}]}]}}

{"kind": "table", "data": {
  "columns": [{"id": "session", "title": "Session"}, {"id": "cost", "title": "Cost", "unit": "USD"}],
  "rows": [{"session": {"ref": "session", "id": "11111111-2222-4333-8444-555555555555"}, "cost": 0.42}]}}

{"kind": "list", "data": {"items": [{"text": "Ticket work", "detail": "2 repos"}]}}

{"kind": "search", "notice": "PARTIAL: one engine timed out", "data": {"results": [
  {"title": "history.py", "location": "scripts/lib/history.py:12", "evidence": "resolved"}]}}
```

A stat's `resets_at` (contract 2) says when a quota or window resets; the hub
shows the time left and counts down to it. Put the human wording in `label` as
well if you like, but the hub reads the time only from `resets_at`.

A table cell is a string, number, boolean, null or reference, and every key in
a row must be a column id. A search result's `evidence` says how the source
knows (code-search's *resolved* or *textual*). It is shown with the result,
because it decides how much an empty result is worth.

`show` prints `{"widgets": [{"id", "kind", "title", "data"}]}`: the same
shapes, each with its own kind. An empty list means the source knows nothing
about that entity, and the hub leaves its section out.

## References and entities

A table cell, a list item's `ref` or a search result's `ref` can link to an
entity: `{"ref": "session", "id": "…", "label": "optional text"}`. Sources link
to entities, never to each other's pages. The hub draws a reference as a link
when a working source `shows` that type, and as plain text otherwise. A click
opens the entity's page (`#/entity/session/<id>`), built from `run show` on
every working source that shows it: one section per source, in discovery
order. A source that prints an empty `widgets` list is left out; one whose
`show` fails or breaks the contract keeps its section, showing the error, so a
missing section never reads as "nothing to say".

[`entities.json`](../contract/entities.json) defines each type and the rule
its IDs follow. Version 1 defines `session`: the Claude Code session ID, which
is token-save's `session_id` and the transcript's file name under
`~/.claude/projects/`. A reference to an undefined type is drawn as plain text;
an ID that breaks its type's rule is an error.

## Versioning

- **`contract`** goes up with any change to a schema. Schemas refuse unknown
  fields in both directions, so even an added optional field would break an
  older hub. The hub shows a source written for a contract it does not read as
  *incompatible*.
- **A hub reads older contracts too**, when a version only adds fields: this
  hub implements 3 and reads 1 and 2, so a source never has to move just
  because the hub did. A source moves to a new version when it prints a field that version
  added, and must then declare it; `check-source` fails a source that prints
  `resets_at` while declaring contract 1, or lists `ranges` while declaring
  2, because an older hub would refuse it.

| Version | Adds |
| --- | --- |
| 1 | the first contract |
| 2 | a stat's `resets_at` |
| 3 | a timeseries widget's `ranges` |
- **`entities.json` `version`** goes up when a type is added. An existing
  type's ID rule never changes; that would be a contract change.

## Checking a source

`scripts/hub check-source <folder> [--entity TYPE=ID ...]` checks what the hub
relies on, in order: the manifest, `run`, `detect`, then `fetch` for every
widget and `show` for every `--entity` given. Exit status is 0 when nothing
failed, 1 when something did, 2 for bad usage.

A source that is not set up on the machine still passes, with `fetch` and
`show` reported as SKIP, not PASS. To check them in CI, give the source the
environment it needs. A search widget without `example_query`, and an entity
type without `--entity`, are skipped the same way.
