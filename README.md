# toolworks-hub

A local, read-only web app that shows Claude Code data alongside whichever
agent-toolworks components are installed: token-save usage, code-search fleet
search, and repo-fleet workspaces. You can also add your own sources, such as a
page fed by Grafana.

It runs as you, with no admin rights, and listens on localhost only. It works
with any subset of components installed, including none, and reads each tool's
published contract rather than re-implementing the tool.

**Status:** the source contract (version 1), its checker, and the hub server
with the built-in Claude Code source are in. token-save and code-search sources
come next, in their own repositories. The design covers the contract,
component states, discovery, linking between pages, configuration, security
and the first release:
[Agent Toolworks Hub — Design](https://claude.ai/code/artifact/cf0466e0-af0d-466c-a082-376bc591deb0).

## Running it

```sh
scripts/hub open      # start the server if needed, open it in your browser
scripts/hub status    # every source it finds and its state, in the terminal
scripts/hub stop
```

The server listens on 127.0.0.1 at a free port, answers only the browser
`hub open` gave its token to, and exits after 10 minutes with no page open. It
needs nothing but `python3`.

It finds sources in three places: the built-in Claude Code source, a
`hub-source/` folder inside each installed Claude Code plugin, and
`~/.config/agent-toolworks/sources/`. Each is shown in one of the states in
[docs/contract.md](docs/contract.md#states), and agent-toolworks plugins you
have not installed are listed with their install command.

A session in any table links to that session's page, which gathers a section
from every source that knows about it: the Claude Code source's transcript
facts, token-save's cost over the session, and so on.

## Writing a source

A source is a folder with a manifest and a program that prints JSON.
[docs/contract.md](docs/contract.md) explains it; the schemas in
[`contract/`](contract) are the contract. Check a source with:

```sh
scripts/hub check-source path/to/my-source --entity session=<a session id>
```

Put it in a tool's test suite: it exits 1 when the source breaks the contract.
[`fixtures/sources/passes`](fixtures/sources/passes) is a source that meets it.

## Verifying it

```sh
scripts/verify
```

It checks that every schema loads and uses only keywords the validator
enforces, that the validator agrees with JSON Schema where Python and JSON
differ, and that each fixture source in `fixtures/sources` passes or fails for
the reason it was built with. That includes a source that hangs and leaves a
child running: both must be stopped.

agent-teller is deliberately out of scope: it is a separate tool and runs
separately.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
