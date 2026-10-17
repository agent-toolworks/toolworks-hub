# Writing a source

A source is a folder the hub runs to get something to show: a manifest saying
what it offers, and a program that prints JSON. This guide builds one, checks
it, and installs it. [contract.md](contract.md) is the reference for every
field and shape, and [`contract/`](../contract) holds the schemas themselves.

## The smallest source that works

```sh
mkdir -p ~/.config/agent-toolworks/sources/disk
cd ~/.config/agent-toolworks/sources/disk
```

`source.json`:

```json
{
  "contract": 1,
  "name": "disk",
  "title": "Disk",
  "version": "0.1.0",
  "network": false,
  "widgets": [{"id": "free", "kind": "stat", "title": "Free on /"}]
}
```

`run`, made executable with `chmod +x run`:

```sh
#!/bin/sh
case "$1 $2" in
  "detect ") echo '{"state": "working"}' ;;
  "fetch free")
    gb=$(df -k / | awk 'NR == 2 { printf "%.1f", $4 / 1048576 }')
    echo "{\"kind\": \"stat\", \"data\": {\"value\": $gb, \"unit\": \"GB\"}}" ;;
  *) echo "unknown command: $*" >&2; exit 2 ;;
esac
```

Check it, then open the hub:

```sh
hub check-source ~/.config/agent-toolworks/sources/disk
hub open
```

That is the whole loop: write, check, look. The hub looks for sources again
every 30 seconds, or when you press Refresh.

## What `run` is asked

| Command | When | Answer with |
| --- | --- | --- |
| `run detect` | every hub start, and every 30 s while the page is open | the state; 5 s at most |
| `run fetch <widget>` | for each widget, every `refresh_seconds` | that widget's data |
| `run fetch <widget> --query <text>` | when someone searches a `search` widget | the results |
| `run show <type> <id>` | when someone opens an entity page your manifest `shows` | your section of that page |

Write the program in whatever the machine already has. Shell is fine for a
few lines. Python's standard library is a safe choice for more, because the
hub needs `python3` anyway. [`examples/grafana/run`](../examples/grafana/run)
is a complete example in Python.

## Say what state you are in

`detect` is how the hub tells *not set up* from *broken*, so a person sees
the step that fixes it, not a stack trace. Check what your source needs, and
answer with the first thing missing:

```json
{"state": "not_set_up", "reason": "no API token in the Keychain",
 "fix": "security add-generic-password -s my-source -a me -w"}
```

- `reason` is one line in words the person can act on. `fix` is the command
  or step; the hub shows it and never runs it.
- Keep `detect` offline when you can. It runs on every start, with a 5-second
  limit. Check that the config and the token exist; leave reachability to
  `fetch`.
- `working` needs nothing else. `off` is for something the person turned off
  on purpose, and is shown as a fact, not an error.

## When something fails, fail

A `fetch` that cannot reach its service must exit non-zero with the reason
on stderr. The hub keeps the last good result on screen with your error
beside it. Printing an empty table instead tells the person "there is
nothing", which is a different, false, answer. The same goes for a search:
"the search did not run" and "it found nothing" must look different.

## Secrets

Read a token at run time, from the Keychain (`security find-generic-password
-s <service> -w`) or an environment variable. Never put it in `source.json`,
in your output, or in a config file in the source folder. The hub never sees
it: it runs your program, and your program talks to the service. Set
`"network": true` if you make network calls; the hub shows it beside the
source's name.

## Sharing sources in a git repo

A team can keep its sources in one repo, a subfolder per source, and each
person points the hub at their checkout in `~/.config/agent-toolworks/hub.json`:

```json
{"source_dirs": ["~/git/team-sources"]}
```

The hub reads them in place, so a `git pull` is the update; nothing is copied
and the hub's own code is never touched. A folder in `source_dirs` that is
itself a source (it has a `source.json`) works too.

What differs from person to person stays out of the repo:

- **Settings** (a URL, a dashboard ID) go in the folder the hub names in
  `TOOLWORKS_SOURCE_CONFIG`, `~/.config/agent-toolworks/source-config/<name>/`.
  Ship a `config.example.json` in the repo and have `detect` say where to copy
  it, as [`examples/grafana`](../examples/grafana) does.
- **Secrets** go in the Keychain or an environment variable, as above.

## Linking to other pages

To make a value link to an entity's page, print a reference instead of plain
text, in a table cell, a list item's `ref`, or a search result's `ref`:

```json
{"ref": "session", "id": "2dd119b7-8360-4507-a34f-6b25a3faa753", "label": "2dd119b7"}
```

You never say which page it goes to. The hub draws it as a link when some
working source shows that entity type, and as plain text otherwise.

To add your own section to an entity's page, list the type in `shows` and
answer `run show session <id>` with widgets:

```json
{"widgets": [{"id": "cost", "kind": "stat", "title": "Cost", "data": {"value": 1.25, "unit": "USD"}}]}
```

Print `{"widgets": []}` when you know nothing about that entity, and the hub
leaves your section out. [entities.json](../contract/entities.json) lists the
types and the rule each ID follows. Version 1 has `session`.

## Shipping a source in a plugin

A Claude Code plugin can carry its source in a `hub-source/` folder at its
root. The hub finds it through the plugin's install path, so a person who
installs the plugin gets its panel with nothing to configure. token-save and
code-search do this.

- **Version:** keep `hub-source/source.json`'s version equal to the plugin's,
  and have your version check include it, so a release cannot ship a stale
  one.
- **Lookups:** find your tool's files the way the tool itself does. Import its
  own lookup if you can; token-save's source calls `history.db_path()`. If the
  hub looks in a different place from the tool, it reports *not set up* for a
  tool that works.
- **Tests:** run `hub check-source hub-source` in your test suite, with an
  environment that puts your tool in a working state. Otherwise `fetch` and
  `show` are skipped, not passed. Pass `--entity session=<id>` to check
  `show`, and give a search widget an `example_query`.
