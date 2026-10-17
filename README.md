# toolworks-hub

A local, read-only web app that shows Claude Code data alongside whichever
agent-toolworks components are installed: token-save usage, code-search fleet
search, and repo-fleet workspaces. You can also add your own sources, such as a
page fed by Grafana.

It runs as you, with no admin rights, and listens on localhost only. It works
with any subset of components installed, including none, and reads each tool's
published contract rather than re-implementing the tool.

**Status:** the source contract (version 1) and its checker are in; the hub
itself is not built yet. The design covers the contract, component states,
discovery, linking between pages, configuration, security and the first
release:
[Agent Toolworks Hub — Design](https://claude.ai/code/artifact/cf0466e0-af0d-466c-a082-376bc591deb0).

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
