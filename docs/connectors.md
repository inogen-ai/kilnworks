# Connectors — live federation with MCP servers

Kilnworks can query read-only [MCP](https://modelcontextprotocol.io) connector servers
**live, at question time**, and blend their results into an answer alongside your ingested
documents — each result cited like any other source. This is **federated search, not
ingestion**: connector data is never copied into Kilnworks' database, and every question
re-queries the connector fresh.

![Kilnworks answering from an ingested document and a live Salesforce connector, each cited separately](https://raw.githubusercontent.com/inogen-ai/kilnworks/main/docs/assets/connectors.gif)

InoGen maintains four read-only connector servers, all published on PyPI:

| System | PyPI package | Run command |
|---|---|---|
| Salesforce | `sfdc-mcp-server` | `uvx sfdc-mcp-server` |
| Microsoft 365 (SharePoint/OneDrive) | `m365-mcp-server` | `uvx m365-mcp-server` |
| ServiceNow | `snow-mcp-server` | `uvx snow-mcp-server` |
| HubSpot | `hubspot-mcp` (script `hubspot-mcp-server`) | `uvx --from hubspot-mcp hubspot-mcp-server` |

Each also works standalone in any MCP client (Claude Desktop, Claude Code) — see the
connector's own README. This guide is specifically about wiring them into Kilnworks.

## How it works

1. Install the MCP client extra into Kilnworks, plus each connector server you want.
2. Describe each connector in a JSON config file and point `KILNWORKS_CONNECTORS_CONFIG` at it.
3. Per question, the caller (or the chat UI) opts a connector in. Kilnworks spawns the
   server's stdio process **fresh for that query**, calls its search tool, adds the results
   to the answer's context, and cites them. The process exits when the query is done.

## Prerequisites

Install the MCP client library into Kilnworks' environment:

    pip install 'kilnworks[connectors]'

Then install each connector server you want, so its command is on `PATH`:

    uv tool install sfdc-mcp-server
    uv tool install m365-mcp-server
    uv tool install snow-mcp-server
    uv tool install hubspot-mcp          # provides the `hubspot-mcp-server` command

(Alternatively, skip the install and point `command` straight at `uvx …` — see the
reference below. `uv tool install` avoids re-resolving the package on every query.)

## The config file

`KILNWORKS_CONNECTORS_CONFIG` is a path to a JSON file with a `connectors` array. Each
entry:

| Field | Required | Default | Meaning |
|---|---|---|---|
| `name` | yes | — | Identifier used in `GET /connectors` and the `/ask` `connectors` list |
| `command` | yes | — | argv Kilnworks spawns as the stdio server, fresh per query |
| `env` | no | `{}` | Environment for the spawned process — the connector's credentials/config go here |
| `allowed_groups` | yes | — | Kilnworks ACL groups permitted to use this connector |
| `search_tool` | no | `search` | The connector's MCP tool to call |
| `query_arg` | no | `query` | The tool argument that receives the question text |
| `limit_arg` | no | `limit` | The tool's result-count argument (`null` if it has none) |
| `search_limit` | no | `5` | Max results to request — capped by `KILNWORKS_CONNECTOR_RESULT_LIMIT` (default 5); it can lower a connector below that cap but not raise it above (raise the env var for more) |
| `extra_args` | no | `{}` | Fixed extra arguments the tool always needs (e.g. an object type) |

**Keep secrets out of the JSON.** `env` values undergo `${VAR}` expansion against Kilnworks'
own environment when the config loads, so store the secret in Kilnworks' environment and
reference it rather than hard-coding it:

    "env": { "HUBSPOT_MCP_ACCESS_TOKEN": "${HUBSPOT_TOKEN}" }

The spawned server receives only a minimal base environment (`PATH`, `HOME`, and similar)
plus the `env` you list — never Kilnworks' own secrets like `KILNWORKS_SECRET_KEY` or the
database URL.

## Per-server setup

Each server exposes a different search tool with a different query-argument name, so
`search_tool` / `query_arg` / `extra_args` vary per connector. Use the exact values below.

### Salesforce

- **Auth:** OAuth 2.0 device-code (default) — needs a Connected App / External Client App
  consumer key in `SFDC_MCP_CLIENT_ID`. One-time interactive login (below); the token
  caches to `~/.sfdc-mcp/token_cache.json` and is reused silently afterward. For a service
  account instead, set `SFDC_MCP_AUTH=client_credentials` and add `SFDC_MCP_CLIENT_SECRET`.
- **Search tool:** `search`, query argument **`term`** (not the default `query`).

```json
{
  "name": "salesforce",
  "command": ["sfdc-mcp-server"],
  "env": {
    "SFDC_MCP_CLIENT_ID": "${SFDC_CLIENT_ID}",
    "SFDC_MCP_LOGIN_URL": "https://login.salesforce.com"
  },
  "allowed_groups": ["sales"],
  "search_tool": "search",
  "query_arg": "term",
  "search_limit": 5
}
```

**One-time login.** The device-code flow completes on the *first tool call*, not at startup:
the server returns a sign-in URL and code as the tool result and finishes the login in the
background, caching the token to `~/.sfdc-mcp/token_cache.json`. Sign in once from any MCP
client (e.g. run a `search` from Claude Desktop/Code — see the
[server README](https://github.com/inogen-ai/sfdc-mcp-server#readme)); Kilnworks' per-query
spawns then reuse the cached token. If you skip it, the first Kilnworks query that uses the
connector returns the sign-in instructions instead of data — follow them, then re-ask.

### Microsoft 365 (SharePoint / OneDrive)

- **Auth:** MSAL device-code (default; delegated `Sites.Read.All` / `Files.Read.All`) —
  needs an Entra app-registration client ID in `M365_MCP_CLIENT_ID`. One-time interactive
  login; token caches to `~/.m365-mcp/token_cache.json`. For app-only auth, set
  `M365_MCP_AUTH=client_credentials` with `M365_MCP_CLIENT_SECRET` and `M365_MCP_TENANT_ID`.
- **Search tool:** `search`, query argument **`query`** (both defaults, so they can be omitted).

```json
{
  "name": "sharepoint",
  "command": ["m365-mcp-server"],
  "env": {
    "M365_MCP_CLIENT_ID": "${M365_CLIENT_ID}"
  },
  "allowed_groups": ["staff"],
  "search_limit": 5
}
```

**One-time login.** As with Salesforce, the MSAL device-code flow completes on the first tool
call, not at startup — the sign-in URL and code come back as the tool result and the token
caches to `~/.m365-mcp/token_cache.json`. Sign in once from any MCP client (see the
[server README](https://github.com/inogen-ai/m365-mcp-server#readme)); Kilnworks reuses the
cached token afterward.

### ServiceNow

- **Auth:** static credentials, no interactive step — `SNOW_MCP_INSTANCE_URL` plus either
  basic (`SNOW_MCP_USERNAME` + `SNOW_MCP_PASSWORD`) or bearer (`SNOW_MCP_TOKEN`). Use a
  dedicated least-privilege user.
- **Search tool:** `search_knowledge` (full-text over the knowledge base), query argument
  **`query`**. (For arbitrary tables there's `query_records`, but it takes a ServiceNow
  encoded query and a `table` name — less suited to free-text federation.)

```json
{
  "name": "servicenow",
  "command": ["snow-mcp-server"],
  "env": {
    "SNOW_MCP_INSTANCE_URL": "https://dev12345.service-now.com",
    "SNOW_MCP_USERNAME": "${SNOW_USER}",
    "SNOW_MCP_PASSWORD": "${SNOW_PASSWORD}"
  },
  "allowed_groups": ["support"],
  "search_tool": "search_knowledge",
  "query_arg": "query"
}
```

No pre-auth step — the static credentials are used on each spawn.

### HubSpot

- **Auth:** static Private App bearer token in `HUBSPOT_MCP_ACCESS_TOKEN`. Set
  `HUBSPOT_MCP_BASE_URL` if you need EU data residency.
- **Search tool:** `search_records`, query argument **`query`**, and it **requires an
  `object_type`** — supply it through `extra_args`.

```json
{
  "name": "hubspot",
  "command": ["hubspot-mcp-server"],
  "env": {
    "HUBSPOT_MCP_ACCESS_TOKEN": "${HUBSPOT_TOKEN}"
  },
  "allowed_groups": ["sales"],
  "search_tool": "search_records",
  "query_arg": "query",
  "extra_args": { "object_type": "contacts" },
  "search_limit": 5
}
```

`object_type` is one of `contacts`, `companies`, `deals`, `tickets`, a custom object's
internal name, or an object-type id. One entry searches one object type — add more entries
(e.g. a second named `hubspot-deals`) for others.

No pre-auth step.

## Using connectors

List the connectors visible to the caller (filtered by `allowed_groups` against the
caller's ACL principals):

    curl -s localhost:8000/connectors -H "authorization: Bearer $TOKEN"
    # [{"name":"salesforce","status":"ready","needs_login":false}, ...]

`status` is `ready` or `down`. (The response includes a `needs_login` field for
completeness, but the bundled MCP connectors report `ready` even before a device-code login
has been done — the sign-in prompt appears on the first query, per the login notes above,
not as a status.)

Opt a connector into a question — omit `connectors` for a documents-only answer:

    curl -s localhost:8000/ask -H "authorization: Bearer $TOKEN" \
      -H 'content-type: application/json' \
      -d '{
            "question": "What renewal discount applies to Acme, and what stage is their renewal in?",
            "connectors": ["salesforce"]
          }'

The answer blends your ingested documents with the live Salesforce results and cites each —
your document as e.g. `[1] pricing.pdf`, and a connector result as `[2] salesforce: <record
title>`. In the chat UI, tick the connector in the sidebar before asking.

## Governance & security

- **Read-only.** Every connector server is read-only by construction — see each repo's README.
- **One service identity, group-gated.** A connector authenticates as its *own* single
  credential, not per Kilnworks user. Access is gated at the Kilnworks level by
  `allowed_groups` against the caller's principals — **not** by what that user could see in
  the source system. Point each connector at a least-privilege service account and scope
  `allowed_groups` to match.
- **Live, never stored.** Results are fetched per question and never written to Kilnworks'
  database.
- **Bounded and fail-soft.** `KILNWORKS_CONNECTOR_TIMEOUT` (default 8s) bounds each call; a
  slow, timed-out, or failing connector is skipped and the answer still returns from your
  documents. `KILNWORKS_CONNECTOR_RESULT_LIMIT` (default 5) caps results per connector, and
  `KILNWORKS_CONNECTOR_CONTEXT_CAP` (default 20) caps the total connector results blended
  into a single answer.

## Troubleshooting

- **A device-code connector's first query returns sign-in text instead of data** — its
  one-time login hasn't been completed. Sign in once from an MCP client (see Salesforce / M365
  above); the token caches and later queries work.
- **Connector missing from `/connectors`** — the caller's ACL principals don't intersect the
  connector's `allowed_groups`, or `KILNWORKS_CONNECTORS_CONFIG` is unset / failed to parse
  (check the API logs).
- **Empty or missing connector results** — confirm `search_tool` / `query_arg` / `extra_args`
  match the per-server values above; a wrong `query_arg` sends the question under an argument
  the tool ignores.
- **Timeouts** — raise `KILNWORKS_CONNECTOR_TIMEOUT` if a connector is legitimately slow.
