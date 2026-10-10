# Native web search (prototype)

Native web search is enabled by default. For managed VS Code backends, set
Native web search to false in Crucible's Policies & Memory settings
(persisted boolean `crucible.webSearch.enabled`), then use the restart banner
to disable it. Set true and restart to re-enable. The setting survives
restarts; managed startup uses this setting rather than inherited shell flags.

For standalone or dev-attached backends, launch with
`CRUCIBLE_WEB_SEARCH_ENABLED=1` to enable, or `0` to disable, and restart
that backend process. Managed settings do not change an attached process's
environment. No search API key, MCP server configuration or search subprocess
is required. Calls still require approval unless permission is remembered.

Disabling removes the tool from discovery and the effective agent-editor
available-tools list, and prevents execution including remembered approvals.

The controller, subagents and team members are instructed to research uncertain
external facts and details that may have changed since training. Requests to
research, search the web, look up, verify, or find current information encourage
source checks; local repository searches still use code tools. Agents should
fetch relevant result pages to verify key claims, prefer primary sources, check
dates, cite URLs and disclose verification gaps. This guidance follows each
agent's available tools and permissions. Agents without browsing capability
must report the gap rather than bypass restrictions. Team members share source
URLs and verification limits when evaluating external claims.

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

Search finds sources; use the separate native [`web_fetch`](native-web-fetch.md)
tool to download their pages. Results
are untrusted external evidence. Answers should cite source URLs. Search
has a 25-second deadline, 1 MiB response limit and 12,000-character output
limit. The free upstream service can rate limit or fail; there is no retry
or fallback provider. Exact free quotas are not established.

## Release checklist

- [ ] Confirm third-party product integration permission with Exa.
- [ ] Confirm keyless limits and acceptable service expectations.
- [ ] Review privacy disclosure and permission behavior.
- [ ] Complete mocked backend/UI regression tests and optional live smoke.

These release checks remain outstanding for the prototype.
