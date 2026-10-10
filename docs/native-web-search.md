# Native web search (prototype)

For managed VS Code backends, set Native web search to true in Crucible's
Policies & Memory settings (persisted boolean `crucible.webSearch.enabled`),
then use the restart banner to restart the backend. Set false and restart
to disable. The setting survives restarts; it defaults false and managed
startup overrides inherited shell opt-in unless you explicitly enable it.

For standalone or dev-attached backends, launch with
`CRUCIBLE_WEB_SEARCH_ENABLED=1` to enable, or `0` to disable, and restart
that backend process. Managed settings do not change an attached process's
environment. No search API key, MCP server configuration or search subprocess
is required. This prototype is off by default pending the release checks below.

Disabling removes the tool from discovery and the effective agent-editor
available-tools list, and prevents execution including remembered approvals.

Search sends the query and search controls to Exa. Its public privacy
policy permits use of query data for improving models, including training
and fine-tuning. Do not send secrets, personal information or confidential
code. No files, workspace paths, conversation history or model/session
metadata are attached by the adapter. Verified TLS and a fixed HTTPS endpoint
are used; redirects are not followed. The adapter intentionally ignores ambient
HTTP(S) proxy settings/credentials (`trust_env=False`); it does not inherit
credential-bearing proxy configuration.

Approve once authorizes one call. Approve & remember authorizes future
native searches in this workspace and skips per-query approval. To revoke,
remove `.crucible/approved-web-search.json` while the backend is stopped.
This file is separate from external MCP approvals.

Search finds sources; it does not independently fetch their pages. Results
are untrusted external evidence. Answers should cite source URLs. Search
has a 25-second deadline, 1 MiB response limit and 12,000-character output
limit. The free upstream service can rate limit or fail; there is no retry
or fallback provider. Exact free quotas are not established.

## Release checklist

- [ ] Confirm third-party product integration permission with Exa.
- [ ] Confirm keyless limits and acceptable service expectations.
- [ ] Review privacy disclosure and permission behavior.
- [ ] Complete mocked backend/UI regression tests and optional live smoke.

Do not activate the public production default until these checks pass.
