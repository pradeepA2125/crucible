# Local native web smoke test — 2026-10-10

Built and installed this checkout with `bash scripts/dev/install-local.sh`.
The installed extension is version 0.6.0. Updated the existing managed runtime
with `uv pip install --python ~/.crucible/runtime/venv/bin/python -e services/agentd-py`
to install BeautifulSoup, markdownify and soupsieve, then reloaded VS Code.
The runtime imports the editable backend from this checkout. Both native web
flags default to enabled.

Used a fresh Crucible chat with the user's selected GPT-5.6-Terra model:

> Research the current Python asyncio.timeout behavior using official Python
> documentation. Find a source, read the relevant page, and give a brief answer
> explaining what exception a timeout raises and where it can be caught. Cite
> the source URL. This is a web-research smoke test; do not edit files or run
> shell commands.

Observed in the installed chat UI:

- The controller loaded a skill, then called native `web_search` with a query
  targeting the official Python asyncio documentation.
- The UI displayed `Web search approved: Exa.web_search` and actual Exa search
  output containing the official documentation URL and a timeout excerpt.
- The controller then called native `web_fetch` for
  `https://docs.python.org/3/library/asyncio-task.html` in Markdown format.
- The UI displayed `Web fetch approved: Web.web_fetch` and downloaded Markdown,
  including the source URL, untrusted-content notice and documentation heading.
- The controller completed its answer with an example catching `TimeoutError`
  outside the context manager and a link to the official `#timeouts` section.

The search excerpt identified Python 3.14.5 documentation while the fetched page
identified Python 3.15.0. Search index text can lag the live page; source fetching
worked and this difference reinforces checking live evidence. This verifies
native tool selection, live transport, conversion, approval plumbing and a cited
answer in the main-agent chat. It does not establish complete source coverage,
upstream quotas, or live child/team behavior; those permission paths have mocked
regression coverage.

A separate direct-client network smoke command was not executed: automatic
approval review could not complete because of its usage limit. The successful
chat run above is the live smoke evidence.

## Implicit freshness scenario

Ran a second fresh chat with GPT-5.6-Terra, using only:

> Which Node.js version should I use for a new production service today, and on
> what date does that version reach end of life? Keep it brief.

The message contains no request to research, search, browse, fetch, verify or
cite. The model independently selected native `web_search`, followed by two
native `web_fetch` calls. It fetched Node.js release information and the official
`https://raw.githubusercontent.com/nodejs/Release/main/schedule.json` in text
format to check the exact end-of-life date. The schedule call's thought was
"Verify the exact v24 end-of-life date from the official Node.js release schedule."
Its output contained downloaded JSON with the native untrusted-source wrapper.
Approved that public schedule download once; no remembered permission was added
by the test operator.

The completed answer recommended Node.js 24 (Krypton) LTS, gave 30 April 2028 as
its end-of-life date, and cited `https://nodejs.org/en/about/previous-releases`.
This is a successful observed case of web-tool selection from a time-sensitive
question without explicit browsing instructions, not a guarantee for all prompts
or models. No prompt or implementation changes were needed during this scenario.
