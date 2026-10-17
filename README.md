# toolworks-hub

A local, read-only web app that shows Claude Code data alongside whichever
agent-toolworks components are installed: token-save usage, code-search fleet
search, and repo-fleet workspaces. You can also add your own sources, such as a
page fed by Grafana.

It runs as you, with no admin rights, and listens on localhost only. It works
with any subset of components installed, including none, and reads each tool's
published contract rather than re-implementing the tool.

**Status:** design. Nothing is built yet. The design covers the source contract,
component states, discovery, linking between pages, configuration, security and
the first release:
[Agent Toolworks Hub — Design](https://claude.ai/code/artifact/cf0466e0-af0d-466c-a082-376bc591deb0).

agent-teller is deliberately out of scope: it is a separate tool and runs
separately.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
