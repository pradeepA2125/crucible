# Command sandbox — design

Date: 2026-10-30. Status: implemented on `claude/command-sandbox`; the task path is not sandboxed yet. Follows Auto approval mode
(`2026-10-29-auto-approval-mode-design.md`, PR pradeepA2125/crucible#3), which this
design builds on.

## Why

Crucible's agents run shell commands on the user's machine with the user's
permissions. Auto mode decides *whether* a command should run, and its judgment
can be fooled by a well-worded injection or simply be wrong. The sandbox is a wall
that does not judge and cannot be argued with: it limits what a running command can
reach.

Real incidents set the priorities (nine documented 2025–26 cases across Cursor,
Replit, Claude Code, Gemini CLI, Antigravity, Kiro):

1. **Deleting outside the project** — the most common catastrophe (4 of 9): an
   `rm -rf` whose path became `~/` or `/` wiped a home folder, a Mac Keychain, a
   whole drive. Confining writes to the workspace stops every one of these.
2. **Wiping a production database or cloud** — the most expensive (3 of 9): a
   migration pointed at production, a database command during a code freeze, a
   cloud action with inherited credentials. A filesystem wall does nothing here;
   limiting which hosts a command can reach is the only local control that helps.
3. **Damage inside the project** (2 of 9) — no wall stops it; undo does. Out of
   scope here (see Non-goals).

Every comparable agent ships this: Claude Code (Seatbelt / bubblewrap, proxy with a
domain allowlist), Codex (Seatbelt / bubblewrap, network off by default), Cursor
(Seatbelt / Landlock, domain allowlist; agents stopped 40% less often once
sandboxed), Gemini CLI (Seatbelt profiles or a container).

## Decisions made by the user

- On by default, in every approval mode, including Full access.
- Network: by default commands reach package registries and GitHub; any other host
  needs approval — Auto's judge in Auto, the user in Ask and Accept edits.
- An approved host is remembered for this workspace.
- In Full access, a command the sandbox blocks reruns outside it without asking.
- When the sandbox cannot start (native Windows, Linux without bubblewrap),
  commands run unprotected with a visible warning.

## Goals

- A command the agent runs cannot write outside the workspace, temp and tool caches,
  cannot write the files that grant permissions or steer the agent (even inside the
  workspace), cannot read credential folders, and cannot reach hosts that are not
  allowed.
- Hitting the wall is understood by the agent: the result says it was the sandbox,
  what was blocked, and how to ask for an exception. No retry loops.
- Normal development does not notice the wall: installs from the default
  registries, tests, builds, linters, and `git status/diff/log/commit` plus
  `fetch`/`pull` of public HTTPS remotes all work without prompts. Commands that
  need the user's credentials (`git push`, `gh`, cloud CLIs, publishing) ask to run
  outside the wall.
- Fewer interruptions in Auto: sandboxed commands stop going to the judge except
  when they act outward.

## Non-goals

- Undoing changes shell commands make inside the workspace (rewind snapshots of
  shell writes). Capturing the before-state of arbitrary writes needs a filesystem
  snapshot mechanism (APFS clones, overlay mounts) and is its own design. *My call.*
- Sandboxing MCP servers, the agent's built-in file/web tools, hooks, or the
  backend itself. MCP servers need their own network and credentials and already
  have their own approval (same scope as Claude Code). *My call.*
- Containers or VMs. Startup cost and Linux-only binaries rule them out for every
  command (Cursor's reasoning). An outer container remains the answer for fully
  unattended runs.
- Native Windows enforcement (no suitable primitive yet; Cursor and Codex use WSL2).
- Protection against a determined local attacker or kernel escapes.

## Anti-goals (failure even if it "works")

- Developers turn it off because `npm install` / `uv sync` / `cargo build` break.
- The agent loops on a blocked command, or invents workarounds (copying files
  elsewhere, switching to another tool) instead of asking.
- A command can grant itself more power: editing approval files, agent
  definitions, `AGENTS.md`, VS Code tasks, git hooks or shell startup files, then
  having something unsandboxed execute them.
- Silent degradation: the sandbox is off or unavailable and the user doesn't know.

## What runs inside

All of these start through the sandbox when it is on (*my call*):

- `run_command` (`agentd/tools/shell.py::run_command`, `create_subprocess_shell`).
- Terminal sessions (`agentd/exec_sessions/pty_process.py::PtyProcess.spawn`,
  `subprocess.Popen` — keep the module's no-`preexec_fn`-on-asyncio rule).
- The same for sub-agents and team members (they share these entry points), and the
  task path's validation commands (`agentd/validation/command_validator.py`).

Process trees inherit the sandbox, so a script, `make` target or `npm run` child is
inside too.

## The wall

### Writes

Allowed:

- The workspace (the controller's frozen workspace path), and the command's own
  shadow when it runs in one (task path).
- The temp directory (`$TMPDIR`, `/tmp`, `/private/tmp`, `/var/folders/...` on macOS).
- Package and build caches outside the workspace, so installs and builds work
  (*my call*, list to refine during build): `~/.npm`, `~/.cache` (pip, uv, …),
  `~/Library/Caches`, `~/.cargo/registry`, `~/.cargo/git`, `~/go/pkg/mod`,
  `~/.gradle/caches`, `~/.m2/repository`, `~/.pnpm-store`, `~/.bun/install/cache`,
  `~/.local/share/uv` (uv's managed Pythons).
  Never their `bin` directories (`~/.cargo/bin`, `~/.local/bin`, `~/go/bin`): those
  are on `PATH` and would let a command plant a program the user runs later.
- Folders the user adds for this workspace in Settings.

Read-only even inside the workspace — the same files Auto always asks about
(`auto_rules._ALWAYS_ASK_IN_WORKSPACE`): `AGENTS.md`, `.crucible/approved-*.json`,
`.crucible/mcp.json`, `.crucible/sandbox.json`, `.crucible/{agents,skills,prompts,state}/`,
`.claude/{agents,skills}/`, `.vscode/{settings,tasks,launch}.json`,
`*.code-workspace`, `.git/hooks/`, `.git/config`. Plus shell startup files and
`.gitconfig` if they exist in the workspace. One list, shared by Auto and the
sandbox, so they can't drift. Exception: the running command's own shadow under
`.crucible/state/shadows/` stays writable on the task path.

Everything else is denied, including the rest of the home folder.

### Reads

Allowed everywhere except credential stores (*my call*; Claude Code leaves these
readable by default, Codex makes `.aws` read-only — this goes further because an
injection plus network access is how secrets leak):

`~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gcloud`, `~/.azure`, `~/.kube`,
`~/.docker/config.json`, `~/.netrc`, `~/.git-credentials`, `~/.config/gh`,
`~/.crucible/run` and `~/.crucible/auth` (backend tokens, plan sign-ins),
`~/.crucible/trust.json`, `~/.claude`, `~/Library/Keychains`. The rest of
`~/.crucible` stays readable: commands run tools from `~/.crucible/runtime/bin`.

`~/.npmrc` and `~/.pypirc` stay readable so private-registry installs work; noted
as a known exposure.

### Environment

Sandboxed commands do not inherit secrets through the environment (*my call*):
variables whose names contain `KEY`, `SECRET`, `TOKEN`, `PASSWORD`, `PASSWD`,
`CREDENTIAL` or `AUTH`, plus `AWS_*`, `AZURE_*`, `GOOGLE_APPLICATION_CREDENTIALS`, are
removed from the command's environment (on top of what `child_env()` already
strips). A command run outside the sandbox gets them back. Without this, a credential
folder block is moot: the backend's environment often carries `GITHUB_TOKEN` or a
provider key.

Consequence, intended: commands that need the user's credentials — `git push`
(SSH key or Keychain helper), `gh`, cloud CLIs, `npm publish` — find none inside the
wall and fail. The agent then asks to run them outside the sandbox, which is how
outward actions get checked.

### Network

Commands have no direct route out. A small HTTP/HTTPS proxy runs in the backend
process, outside the sandbox; the sandbox only lets commands connect to that
proxy, and `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` (and lowercase forms) point at
it. The proxy checks each hostname (CONNECT for HTTPS; no TLS inspection) against:

1. **Defaults** (*my call*): `registry.npmjs.org`, `registry.yarnpkg.com`,
   `pypi.org`, `files.pythonhosted.org`, `crates.io`, `index.crates.io`,
   `static.crates.io`, `proxy.golang.org`, `sum.golang.org`, `repo.maven.apache.org`,
   `github.com`, `api.github.com`, `codeload.github.com`,
   `objects.githubusercontent.com`, `raw.githubusercontent.com`.
2. **Remembered hosts for this workspace** — `.crucible/approved-hosts.json`
   (matches the `approved-*.json` always-ask pattern, so no command can edit it).
3. Anything else is blocked, and so is any hostname that resolves only to
   loopback, link-local or cloud-metadata addresses (`169.254.169.254`), unless
   listed explicitly.

Tools that ignore proxy variables (plain `ssh`, most database drivers, UDP) fail
closed. That is the point for the production-database class of incident; for
`git` over SSH the agent asks to run outside the sandbox.

**Loopback** (*my call*): commands may listen on and connect to loopback addresses,
so test suites and dev servers that start a local server and call it keep working.
On macOS this also reaches services already running on the host's localhost
(including the Crucible backend, which still requires its token — unreadable
inside the sandbox — and local databases); on Linux only the sandbox's own
network namespace. Noted as a known gap: a local database on the host is reachable
from a macOS sandbox.

**Linux plumbing:** with a separate network namespace the host's `localhost` proxy
is unreachable, so the proxy is exposed to the namespace through a Unix socket and
bridged to a TCP port inside it (what Claude Code does with `socat`; a small
built-in helper avoids the extra dependency — builder's choice).

## Hitting the wall

A blocked command fails as it would on a read-only disk or without network. The
backend then annotates the tool result so the agent knows it was the sandbox:

> Blocked by the sandbox: write to `/Users/u/.zshrc` is outside the allowed folders.
> To run this command outside the sandbox, call `run_command` again with
> `outside_sandbox: true` and a short `reason`.

> Blocked by the sandbox: network access to `db.prod.example.com` is not allowed.
> To allow this host, call `run_command` again with `allow_hosts: ["db.prod.example.com"]`
> and a short `reason`.

How the backend knows: the proxy records each refused host per command; for files,
the platform's denial (macOS sandbox violation report, or `EPERM`/`EROFS` on paths
outside the allowed set) is matched to the command. The exact detection is the
builder's; the requirement is that a sandbox block is never reported as an
ordinary error, and an ordinary error is never reported as a sandbox block.

The sandbox does not replace the approval modes' own gates: in Ask and Accept
edits every command still asks first (unless a remembered rule covers it), then
runs inside the wall. A remembered command rule never covers running outside the
sandbox or allowing a new host; those always get their own decision (*my call*).

Two ways to ask for an exception, both as new optional `run_command` arguments
(and `start_session`):

- **`allow_hosts`** — stay sandboxed, add hosts. Approved by:
  - Ask / Accept edits: a card "Allow network access to `<host>`?" with Allow once,
    Allow for this workspace (writes `approved-hosts.json`), Reject.
  - Auto: the judge, with the host and reason in the pending action. A judge
    approval allows the host for that one command; only a user approval is
    remembered.
  - Full access: allowed for that command without asking.
- **`outside_sandbox`** — run the whole command unsandboxed. Approved by:
  - Ask / Accept edits: a card "Run outside the sandbox?" showing what was blocked
    and the agent's reason; Allow once or Reject (never remembered — *my call*).
  - Auto: the judge, told the command is leaving the sandbox (risk rises).
  - Full access: runs without asking.

The agent is told the sandbox is on, what it allows, and how to ask, in the
`run_command` / `start_session` tool descriptions and a short note in the payload
tail for the main agent (the system prompt is golden-pinned — same pattern as
`AUTO_MODE_NOTE`). Children get it through a `<<sandbox:on>>` tagged block.

## Auto with the sandbox

Auto's decision order gains one rule (*my call — the largest behavior change here,
check it first*). When the sandbox is on and available:

1. Deny list — unchanged.
2. Always-ask writes — unchanged (the sandbox also blocks them, but asking first is
   clearer than failing).
3. **Outward actions go to the judge**: commands that publish or change something
   remote through an allowed host — `git push`, `gh` subcommands that write (pr
   create/merge/close, release, repo, api with a non-GET method), `npm publish`,
   `cargo publish`, `twine upload`, `uv publish`, `docker push`.
4. **Everything else runs sandboxed without the judge.** This is how Codex and
   Claude Code use their reviewers: only crossings get reviewed. Test runners,
   linters and builds are among the commands that stop costing a judge call.
5. `outside_sandbox` and `allow_hosts` requests go to the judge.

Typed input to a shell session (`write_stdin` into bash/zsh/…) gets the same
checks as a command line; input to REPLs and prompts runs without the judge
because the process is inside the wall.

When the sandbox is off or unavailable, Auto behaves exactly as today.

## Platforms

- **macOS:** Seatbelt, `sandbox-exec -p <generated profile>`; the profile is
  generated per command from the rules above (Chrome, Cursor, Codex and Claude Code
  all use it despite its deprecation).
- **Linux / WSL2:** bubblewrap (`bwrap`) on `PATH`. Network isolation via a new
  network namespace with only the proxy reachable. If `bwrap` is missing or user
  namespaces are disabled, the sandbox is unavailable.
- **Native Windows:** unavailable.
- Availability is probed once at startup (spawn a trivial sandboxed command) and
  reported in `/v1/config` with a reason.

## Unavailable or off

- **Off** (user turned it off in Settings): commands run as today. The approval
  menu footer says "Sandbox off".
- **Unavailable**: commands run unprotected. A one-time notice per window says why
  ("Sandbox unavailable: bubblewrap not installed — commands run unprotected"),
  and the approval menu footer keeps saying it. The agent's tool description says
  the sandbox is unavailable.

## Settings and UI

- **Settings → Sandbox** (*my call*): on/off (global, default on, like the approval
  mode), status (on / unavailable: reason), remembered hosts for this workspace
  (list with remove), extra writable folders for this workspace (add/remove).
  Workspace values live in `.crucible/sandbox.json` (always-ask, read-only in the
  sandbox); the remembered hosts in `.crucible/approved-hosts.json`.
- **Approval menu footer**: one line with the sandbox state.
- **Cards**: the two new escalation cards above, built from the existing command
  card (reuse components and tokens per `docs/design/desktop-2026-10-21/react-ui-guidelines.md`).
- **Tool pills**: a "outside sandbox" marker when a command ran unsandboxed, and
  the blocked reason on a pill whose command the sandbox stopped.
- Changing the on/off setting applies from the next command, no restart. The
  setting reaches the backend the same way the approval mode does (process
  identity and version guarded, applied on reconnect).

## Success criteria

- Each of the four "deleting outside the project" incident shapes, replayed as a
  command (`rm -rf ~/`, `rm -rf /`, recursive delete of a parent, write to
  `~/.zshrc`), is blocked by the sandbox even with the deny list disabled.
- Reading `~/.ssh/id_rsa` and `~/.aws/credentials` from a command fails.
- `curl` to a non-allowed host and a Postgres connection to a remote host fail with
  the annotated result; the agent asks instead of retrying (checked live on the
  ChatGPT plan).
- On this repo and two real workspaces: `npm ci`, `uv sync`, `pip install`,
  `cargo build`, `pytest`, `vitest`, `git fetch` work without sandbox prompts;
  `git push` asks to run outside the sandbox and then works.
- A command cannot read `GITHUB_TOKEN` or a provider key from its environment.
- In Auto, judge calls per turn drop for test/build-heavy work (measure before and
  after on the same prompts).
- macOS and Linux both covered by tests that spawn real sandboxed processes; a
  platform without support reports "unavailable" with a reason.

## Left to the builder

- Profile generation, path canonicalization (symlinks, `/private` on macOS), and
  how denials are detected and attributed to a command.
- The proxy implementation (asyncio CONNECT proxy in the backend), and how
  per-command host decisions reach it.
- Exact cache folder list, verified against real installs on both platforms.
- Interaction with `exec_sessions` lifetimes and the PTY spawn rules.
- Plan split; a sensible order is: macOS write/read wall → proxy and network rules
  → blocked-command annotations and escalation → Auto rule change → Linux →
  Settings/UI.
