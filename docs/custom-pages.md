# Adding a custom page

A custom page is a tab of links, in sections, read from a JSON file. It runs
nothing and fetches nothing: it is for what you want one click away from the
hub, such as Grafana alert views, dashboards, runbooks or other hub tabs.

For a tab that shows live data, write a source instead:
[writing-a-source.md](writing-a-source.md).

## Add one

Each `*.json` file is one page. The file's name, without `.json`, is the page's
name in the hub's URL (`#/page/<name>`), so use lowercase letters, digits and
dashes.

```sh
mkdir -p ~/.config/agent-toolworks/pages
cp examples/pages/grafana-alerts.json ~/.config/agent-toolworks/pages/
```

Edit the copy, then reload the hub (or press Refresh). The page appears as a
tab after the sources. `hub status` lists it under PAGES.

## The file

```json
{
  "title": "Grafana alerts",
  "description": "Where to look when something fires.",
  "sections": [
    {
      "title": "Alerts",
      "links": [
        {"title": "Firing now", "url": "https://grafana.example.com/alerting/list?view=list&search=state:firing",
         "description": "every alert rule that is firing"},
        {"title": "Silences", "url": "https://grafana.example.com/alerting/silences"}
      ]
    },
    {
      "title": "In the hub",
      "links": [{"title": "Search the fleet", "url": "#/source/code-search"}]
    }
  ]
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `title` | yes | The tab's label and the page's heading. |
| `description` | no | One line under the heading. |
| `sections` | yes | A list of `{title, links}`, shown as cards. At least one. |
| `links` | yes | A list of `{title, url, description?}`. At least one per section. |
| `url` | yes | An `http://` or `https://` URL, which opens in a new browser tab, or `#/...`, a page of the hub itself (`#/source/<name>`, `#/page/<name>`). |

Unknown fields are refused, so a misspelt field is reported instead of being
ignored. A file that breaks the format, or a name another page already uses,
is shown as a problem at the top of the hub, with every fault named; the rest
of the hub carries on.

## A Grafana alerts page

[`examples/pages/grafana-alerts.json`](../examples/pages/grafana-alerts.json)
links to Grafana's alert list, firing alerts, silences, contact points and
notification policies. Replace `grafana.example.com` with your Grafana. If you
already have a saved alert view, a folder or a dashboard you watch, copy its
URL from the browser into a link: whatever the URL shows in Grafana is what
the link opens.

These are links, so Grafana asks you to sign in as it always does, and the hub
never sees a Grafana token. To show the firing alerts themselves inside the
hub, that is a source that calls Grafana's API, like
[`examples/grafana`](../examples/grafana).

## Sharing pages with a team

Keep the files in a git repo and have each person list its folder in
`~/.config/agent-toolworks/hub.json`:

```json
{"page_dirs": ["~/git/team-pages"]}
```

`page_dirs` sits beside `source_dirs` in the same file. The folders are read
in the order listed, after `~/.config/agent-toolworks/pages/`; when two files
have the same name, the first one found is used and the other is reported.
Pages' tabs come after the sources'; `tab_order` in `hub.json` moves the ones
it names to the front (see [hub.json](contract.md#hubjson)).
A `git pull` is the update.
