---
description: Open toolworks-hub in the browser (start it if it is not running)
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}"/scripts/hub open:*), Bash("${CLAUDE_PLUGIN_ROOT}"/scripts/hub status:*)
---

Open the hub for the person: run

```sh
"${CLAUDE_PLUGIN_ROOT}/scripts/hub" open
```

It starts the local server if it is not running and opens the page in the
browser. Reply with the address it prints without the token
(`http://127.0.0.1:7437/` by default), which the person can bookmark, and
nothing else. If it fails, run `"${CLAUDE_PLUGIN_ROOT}/scripts/hub" status`
and say what it reports.
