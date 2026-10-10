# Native web fetch

`web_fetch` downloads a public HTTP or HTTPS URL directly from the Python
backend. It uses `httpx`, BeautifulSoup and `markdownify`; no API key, MCP
server, `uvx`, or browser process is required. Use `web_search` to discover
sources, then `web_fetch` to read a result page.

The tool accepts `url`, optional `format` (`markdown` by default, `text`, or
`html`), and optional `timeout` in seconds (30 by default, up to 120).
It does not execute JavaScript or extract images or PDFs. Markdown and text
conversion remove scripts and styles; HTML returns the source as text.
Every result is labeled as untrusted external evidence and includes its URL.

Fetch is enabled by default. For managed VS Code backends, change Native web
fetch in Policies & Memory (`crucible.webFetch.enabled`), then restart using
the settings banner. For standalone or attached backends, set
`CRUCIBLE_WEB_FETCH_ENABLED=0` to disable, or `1` to enable, and restart that
process. Disabling removes discovery and prevents execution, including calls
with remembered approvals. Exa search has a separate enable setting and is also enabled by default.

Approval happens before DNS resolution or download. The website receives
the requested URL and your network address. Approve once permits one call;
Approve & remember permits future native fetches in this workspace. To revoke
remembered permission, remove `.crucible/approved-web-fetch.json` while the
backend is stopped. Fetch permissions are separate from search and MCP rules.
Read-only child agents cannot fetch; other children follow their tool filters
and approval policy. A child that cannot ask requires remembered permission.

Downloads use verified TLS, ignore ambient proxies, and send no cookies or
authentication headers. Credentials in URLs and private/internal destinations
are rejected. DNS answers are checked and the connection uses the checked IP
while retaining the original Host header and TLS hostname. Each redirect is
checked again. Up to five same-origin redirects are allowed; another origin
requires a separate approved fetch call.

The download limit is 5 MiB, enforced while streaming. The timeout covers DNS,
download and conversion. Compressed responses are rejected; requests ask for
identity encoding. Output is limited to 12,000 characters, including the source
notice, with an explicit truncation marker. HTTP errors and timeouts return
errors without automatic retries. Pages requiring JavaScript, login, or an
unsupported response format may therefore be unavailable.
