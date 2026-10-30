# Command Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run every agent command and terminal session inside an OS sandbox (workspace-only writes, no credential reads, host allowlist), on by default in every approval mode, with clear escalation when the wall blocks something.

**Architecture:** A new `agentd/sandbox/` package builds a per-command policy, wraps the command with Seatbelt (`sandbox-exec`) on macOS or bubblewrap on Linux, and routes network through an in-process CONNECT proxy that checks hostnames per command. Blocked results are annotated; the agent escalates with `outside_sandbox` or `allow_hosts`, approved by the existing approval paths (card / Auto judge / Full access). The on/off switch rides the approval-mode API; workspace hosts and folders live in `.crucible/`.

**Tech Stack:** Python 3.12 asyncio (agentd-py), macOS `sandbox-exec`, Linux `bwrap`, TypeScript (editor-client Zod, VS Code extension), React webview.

**Spec:** `docs/superpowers/specs/2026-10-30-command-sandbox-design.md`

## Global Constraints

- On by default in every approval mode; the user's switch is global (extension `globalState` key `crucible.sandbox.enabled`, default `true`).
- Default hosts: `registry.npmjs.org`, `registry.yarnpkg.com`, `pypi.org`, `files.pythonhosted.org`, `crates.io`, `index.crates.io`, `static.crates.io`, `proxy.golang.org`, `sum.golang.org`, `repo.maven.apache.org`, `github.com`, `api.github.com`, `codeload.github.com`, `objects.githubusercontent.com`, `raw.githubusercontent.com`.
- Remembered hosts: `<workspace>/.crucible/approved-hosts.json`. Extra writable folders: `<workspace>/.crucible/sandbox.json`. Both read-only inside the sandbox and on Auto's always-ask list.
- Writable: workspace, the command's shadow root, `$TMPDIR`, `/tmp`, `/private/tmp`, `/private/var/folders`, and caches `~/.npm`, `~/.cache`, `~/Library/Caches`, `~/.cargo/registry`, `~/.cargo/git`, `~/go/pkg/mod`, `~/.gradle/caches`, `~/.m2/repository`, `~/.pnpm-store`, `~/.bun/install/cache`, `~/.local/share/uv`, plus `sandbox.json` folders. Never `~/.cargo/bin`, `~/.local/bin`, `~/go/bin`.
- Read-only inside writable roots: `auto_rules.ALWAYS_ASK_IN_WORKSPACE` (+ `.crucible/sandbox.json`), workspace `.bashrc`, `.zshrc`, `.profile`, `.bash_profile`, `.zprofile`, `.gitconfig`. Exception: the command's own shadow.
- Unreadable: `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gcloud`, `~/.azure`, `~/.kube`, `~/.docker/config.json`, `~/.netrc`, `~/.git-credentials`, `~/.config/gh`, `~/.crucible/run`, `~/.crucible/auth`, `~/.crucible/trust.json`, `~/.claude`, `~/Library/Keychains`.
- Stripped env: names containing `KEY`, `SECRET`, `TOKEN`, `PASSWORD`, `PASSWD`, `CREDENTIAL`, `AUTH` (case-insensitive), plus `AWS_*`, `AZURE_*`, `GOOGLE_APPLICATION_CREDENTIALS`. The proxy variables set by the sandbox are exempt.
- Hosts resolving only to loopback, link-local or `169.254.169.254` are refused by the proxy unless listed explicitly. Direct loopback connect/bind is allowed.
- Full access: escalations run without asking. Sandbox unavailable: commands run unwrapped with a warning.
- Annotation text, verbatim (path/host substituted):
  - `Blocked by the sandbox: write to {path} is outside the allowed folders. To run this command outside the sandbox, call {tool} again with outside_sandbox: true and a short reason.`
  - `Blocked by the sandbox: reading {path} is not allowed. To run this command outside the sandbox, call {tool} again with outside_sandbox: true and a short reason.`
  - `Blocked by the sandbox: network access to {host} is not allowed. To allow this host, call {tool} again with allow_hosts: ["{host}"] and a short reason.`
- Remembered command rules never cover `outside_sandbox` or `allow_hosts`. `outside_sandbox` approvals are never remembered.
- Main system prompt stays byte-identical (`tests/goldens/controller_prompt_main.json`); sandbox text reaches the main agent through the payload tail.
- `subprocess.Popen` stays the PTY spawn path (no `create_subprocess_exec` + `preexec_fn`).
- UI follows `docs/design/desktop-2026-10-21/react-ui-guidelines.md`: reuse components and semantic tokens; check Dark/Light and wide/narrow.

## Review Focus

1. **A workspace path with spaces** (this repo is `…/AI editor/…`): profiles and `bwrap` args must quote/escape; a sandboxed command in such a workspace still writes inside it. Test in Task 2 and Task 3.
2. **Symlinks**: on macOS `/tmp` → `/private/tmp` and a workspace symlink pointing at `~/` must not open a write path outside. Profiles use canonical paths. Test in Task 2.
3. **Task-path commands run in a shadow under `.crucible/state/shadows/`**, which is otherwise read-only. The command's own shadow stays writable. Test in Task 6.
4. **The user turns the sandbox off while a terminal session is running**: the session keeps the wall it started with; the next `start_session` starts unwrapped. Test in Task 6.
5. **Tools that ignore proxy variables** (`ssh`, `psql`, `nc`): they fail closed and the result says the sandbox blocked network access, not a generic connection error. Test in Task 7.

---

### Task 1: Policy, shared protected list and environment

**Files:**
- Create: `services/agentd-py/agentd/sandbox/__init__.py`, `services/agentd-py/agentd/sandbox/policy.py`, `services/agentd-py/agentd/sandbox/env.py`
- Modify: `services/agentd-py/agentd/approvals/auto_rules.py` (rename `_ALWAYS_ASK_IN_WORKSPACE` → public `ALWAYS_ASK_IN_WORKSPACE`, add `".crucible/sandbox.json"`)
- Test: `services/agentd-py/tests/test_sandbox_policy.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class SandboxPolicy: workspace: Path; writable: tuple[Path, ...]; read_only: tuple[Path, ...]; unreadable: tuple[Path, ...]; proxy_port: int | None; proxy_socket: Path | None`
  - `build_policy(*, workspace: Path, home: Path, shadow_root: Path | None, extra_writable: Sequence[Path], proxy_port: int | None, proxy_socket: Path | None = None) -> SandboxPolicy` — all paths canonical (`Path.resolve()`), read-only entries expanded from the glob list against existing workspace files plus the fixed directories (`.crucible/agents`, …), shadow_root removed from `read_only`.
  - `sandbox_env(env: Mapping[str, str], proxy_url: str | None) -> dict[str, str]` — strips secrets per Global Constraints, then sets `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and lowercase forms to `proxy_url`, and `NO_PROXY=""`.

- [ ] **Step 1: Write the failing tests**

```python
def test_policy_writable_and_never_bin(tmp_path): ...
    # writable contains workspace, shadow, every cache dir from Global Constraints;
    # no entry endswith ".cargo/bin", ".local/bin", "go/bin"
def test_policy_read_only_includes_always_ask_files(tmp_path): ...
    # AGENTS.md, .crucible/approved-hosts.json, .crucible/sandbox.json, .git/hooks, .zshrc in read_only
def test_shadow_is_writable_not_read_only(tmp_path): ...
    # shadow under workspace/.crucible/state/shadows/x in writable, not in read_only
def test_unreadable_credentials(tmp_path): ...
    # home/".ssh", home/".crucible/run", home/".crucible/auth" in unreadable; home/".crucible/runtime" not
def test_paths_are_canonical(tmp_path): ...
    # a workspace reached through a symlink is stored resolved
def test_sandbox_env_strips_secrets():
    env = sandbox_env({"GITHUB_TOKEN": "x", "OPENAI_API_KEY": "y", "AWS_REGION": "z",
                       "PATH": "/bin", "HOME": "/h"}, "http://t@127.0.0.1:9")
    assert set(env) >= {"PATH", "HOME", "HTTPS_PROXY", "https_proxy"}
    assert not {"GITHUB_TOKEN", "OPENAI_API_KEY", "AWS_REGION"} & set(env)
```

- [ ] **Step 2: Run** `cd services/agentd-py && .venv/bin/python -m pytest tests/test_sandbox_policy.py --color=no` — Expected: FAIL (module missing)
- [ ] **Step 3: Implement `build_policy`, `SandboxPolicy`, `sandbox_env`; rename the always-ask constant and update its two users in `auto_rules.py`**
- [ ] **Step 4: Run** the new file plus `tests/test_auto_hard_ask.py tests/test_auto_rules.py` — Expected: PASS
- [ ] **Step 5: Commit** `feat(sandbox): policy, protected list and environment for sandboxed commands`

### Task 2: macOS Seatbelt wrapper

**Files:**
- Create: `services/agentd-py/agentd/sandbox/seatbelt.py`
- Test: `services/agentd-py/tests/test_sandbox_seatbelt.py`

**Interfaces:**
- Consumes: `SandboxPolicy` (Task 1).
- Produces: `seatbelt_profile(policy: SandboxPolicy) -> str`; `seatbelt_argv(policy: SandboxPolicy, argv: Sequence[str]) -> list[str]` returning `["/usr/bin/sandbox-exec", "-p", profile, "--", *argv]`.
- Profile: `(deny default)` base with process/exec/fork/sysctl/mach/IPC allowances needed by ordinary tools (start from Codex's and Claude Code's published profiles), `file-read*` allowed except `unreadable` subpaths, `file-write*` only for `writable` subpaths minus `read_only` literals/subpaths, `network-outbound` only to `localhost:<proxy_port>` plus loopback, `network-bind` on loopback. Paths emitted as Seatbelt string literals with `"` and `\` escaped.

- [ ] **Step 1: Write the failing tests** (real processes; `pytestmark = pytest.mark.skipif(sys.platform != "darwin", …)`; all paths under a `tmp_path / "work space"` with a space and a fake home under `tmp_path`):

```python
def test_write_inside_workspace_allowed(...)          # touch ws/a.txt → rc 0
def test_write_outside_denied(...)                     # touch home/x → rc != 0, file absent
def test_read_only_file_inside_workspace_denied(...)   # echo > ws/AGENTS.md fails, content unchanged
def test_unreadable_credentials_denied(...)            # cat home/.ssh/id_rsa → rc != 0
def test_symlink_out_of_workspace_denied(...)          # ws/link -> home ; touch ws/link/x fails
def test_private_tmp_alias_writable(...)               # touch /tmp/<uuid> succeeds
def test_direct_network_denied(...)                    # python -c socket.create_connection(("1.1.1.1", 80)) fails
def test_loopback_server_and_client_allowed(...)       # python one-liner binds 127.0.0.1:0, connects, rc 0
def test_profile_escapes_quotes(tmp_path)              # a path with '"' yields a parseable profile (sandbox-exec rc 0 on `true`)
```

- [ ] **Step 2: Run** `pytest tests/test_sandbox_seatbelt.py` — Expected: FAIL
- [ ] **Step 3: Implement `seatbelt_profile` and `seatbelt_argv`**
- [ ] **Step 4: Run** — Expected: PASS on macOS (skipped elsewhere)
- [ ] **Step 5: Commit** `feat(sandbox): Seatbelt profile for macOS`

### Task 3: Linux bubblewrap wrapper and network bridge

**Files:**
- Create: `services/agentd-py/agentd/sandbox/bubblewrap.py`, `services/agentd-py/agentd/sandbox/netbridge.py`
- Modify: `.github/workflows/release.yml` (install `bubblewrap` in the test job)
- Test: `services/agentd-py/tests/test_sandbox_bubblewrap.py`

**Interfaces:**
- Consumes: `SandboxPolicy` (Task 1; uses `proxy_socket`).
- Produces: `bwrap_argv(policy: SandboxPolicy, argv: Sequence[str], *, bwrap: str, python: str) -> list[str]` — `--ro-bind / /`, `--bind` each writable root, `--ro-bind` each read-only path over it, `--tmpfs` (or empty bind) over each unreadable path, `--dev /dev --proc /proc`, `--unshare-net --unshare-pid --die-with-parent`, bind `proxy_socket`, then run `python -m agentd.sandbox.netbridge <socket> <port> -- <argv…>`.
- `netbridge.main(argv: list[str]) -> int` — inside the namespace: brings up loopback, listens on `127.0.0.1:<port>`, forwards each connection to the Unix socket, then `execvp`s the command in a child and exits with its status.

- [ ] **Step 1: Write the failing tests** — argv unit tests on every platform (`test_argv_binds_workspace_rw`, `test_argv_masks_unreadable`, `test_argv_quotes_space_path` — argv list elements, no shell joining), plus real-process tests skipped unless `shutil.which("bwrap")` and user namespaces work: the same eight behaviors as Task 2 (write in/out, read-only file, credentials, symlink, direct network denied, loopback allowed, proxy reachable through the bridge).
- [ ] **Step 2: Run** `pytest tests/test_sandbox_bubblewrap.py` — Expected: FAIL
- [ ] **Step 3: Implement `bwrap_argv` and `netbridge`; add `sudo apt-get install -y bubblewrap` to the workflow's test job**
- [ ] **Step 4: Run** — Expected: unit tests PASS locally; real-process tests PASS in CI
- [ ] **Step 5: Commit** `feat(sandbox): bubblewrap wrapper and network bridge for Linux`

### Task 4: Sandbox runtime, workspace store, availability and API

**Files:**
- Create: `services/agentd-py/agentd/sandbox/runtime.py`, `services/agentd-py/agentd/sandbox/store.py`, `services/agentd-py/agentd/api/sandbox_routes.py`
- Modify: `services/agentd-py/agentd/chat/approval.py` (`ApprovalState.sandbox: SandboxStatus | None = None`; `ApprovalModeRequest.sandbox: StrictBool | None = None`), `services/agentd-py/agentd/chat/controller.py` (`set_approval_mode(..., sandbox: bool | None)`, `approval_state()`), `services/agentd-py/agentd/api/routes.py` (pass `request.sandbox`; include router), `services/agentd-py/agentd/main.py` (startup probe via `agentd/startup.py::in_background`), `services/agentd-py/tests/test_get_routes_read_only.py` (add `GET /v1/sandbox`)
- Test: `services/agentd-py/tests/test_sandbox_runtime.py`

**Interfaces:**
- Produces:
  - `class SandboxStatus(BaseModel): enabled: bool; available: bool; reason: str | None`
  - `class SandboxRuntime(workspace: Path, home: Path)`: `enabled: bool` (default `True`); `async probe() -> None`; `status() -> SandboxStatus`; `active() -> bool` (enabled and available); `wrap_argv(argv: Sequence[str], policy: SandboxPolicy) -> list[str]` (picks `seatbelt_argv` or `bwrap_argv` by platform; used by the probe and by Task 6).
  - Probe: run `true` wrapped; unavailable reasons verbatim: `"native Windows is not supported"`, `"bubblewrap is not installed"`, `"user namespaces are disabled"`, `"sandbox-exec failed: <stderr first line>"`.
  - `class WorkspaceSandboxStore(workspace: Path)`: `hosts() -> list[str]`, `add_host(host: str) -> None`, `remove_host(host: str) -> None` (`approved-hosts.json`, `{"hosts": [...]}`); `folders() -> list[Path]`, `set_folders(paths: list[Path]) -> None` (`sandbox.json`, `{"writable": [...]}`); malformed files read as empty with one warning.
  - Routes: `GET /v1/sandbox` → `{status, default_hosts, hosts, folders}`; `PUT /v1/sandbox/hosts {hosts: list[str]}`; `PUT /v1/sandbox/folders {folders: list[str]}` (folders must exist; 422 otherwise).

- [ ] **Step 1: Write the failing tests**: `test_probe_unavailable_without_bwrap(monkeypatch)`, `test_status_reports_reason`, `test_approval_mode_sets_sandbox_and_bumps_version`, `test_sandbox_omitted_keeps_value`, `test_store_round_trip_and_malformed_file`, `test_get_sandbox_route_shape`, `test_put_folders_rejects_missing_path`.
- [ ] **Step 2: Run** — Expected: FAIL
- [ ] **Step 3: Implement** (single-process asyncio: the toggle is a sync write, no await between check and set, like `set_approval_mode`)
- [ ] **Step 4: Run** the new tests plus `tests/test_approval_mode.py tests/test_get_routes_read_only.py` — Expected: PASS
- [ ] **Step 5: Commit** `feat(sandbox): runtime, availability probe, workspace store and API`

### Task 5: Network proxy with per-command leases

**Files:**
- Create: `services/agentd-py/agentd/sandbox/proxy.py`
- Test: `services/agentd-py/tests/test_sandbox_proxy.py`

**Interfaces:**
- Produces:
  - `@dataclass class ProxyLease: token: str; url: str; extra_hosts: frozenset[str]; refused: list[str]; allow_any: bool`
  - `class SandboxProxy`: `async start() -> None` (TCP `127.0.0.1:0`; on Linux also a Unix socket under the backend's run dir); `port: int`; `socket_path: Path | None`; `lease(workspace_hosts: Iterable[str], extra_hosts: Iterable[str] = (), allow_any: bool = False) -> ProxyLease`; `release(lease: ProxyLease) -> None`; `async close() -> None`.
  - Lease URL: `http://<token>:x@127.0.0.1:<port>`; requests without a known token get `407`.
  - Allow: host in defaults ∪ workspace ∪ extra, or `allow_any` (Full access escalation). Refuse when every resolved address is loopback/link-local/`169.254.169.254` unless the host is listed in workspace or extra. Refused hosts are appended to `lease.refused` once each and answered `403`.
  - Supports `CONNECT host:port` (tunnel, no TLS inspection) and absolute-URI plain HTTP requests.
- Resolution goes through an injectable `resolver: Callable[[str], Awaitable[list[str]]]` so tests run offline.

- [ ] **Step 1: Write the failing tests** (local upstream servers on loopback; resolver maps fake hostnames to `127.0.0.1` and the listed-host path exercises the loopback exception): `test_connect_to_default_host_tunnels`, `test_unknown_host_refused_and_recorded`, `test_extra_host_allowed_for_that_lease_only`, `test_metadata_address_refused`, `test_missing_token_407`, `test_allow_any_lease`, `test_plain_http_forward`.
- [ ] **Step 2: Run** — Expected: FAIL
- [ ] **Step 3: Implement** with `asyncio.start_server` / `start_unix_server` and bidirectional pipe tasks; each connection closed on lease release
- [ ] **Step 4: Run** — Expected: PASS
- [ ] **Step 5: Commit** `feat(sandbox): host-checking proxy with per-command leases`

### Task 6: Run commands, sessions and validation inside the wall

**Files:**
- Modify: `services/agentd-py/agentd/tools/shell.py::run_command` (new kw `sandbox: SandboxLaunch | None = None`; wrap `["/bin/sh", "-c", shell_command_line]` with `create_subprocess_exec` when set, else unchanged `create_subprocess_shell`), `services/agentd-py/agentd/exec_sessions/manager.py::start` and `pty_process.py::PtyProcess.spawn` (argv wrapped before `Popen`), `services/agentd-py/agentd/validation/command_validator.py`, `services/agentd-py/agentd/tools/registry.py` (`ToolRegistry(sandbox_runtime=…)`, builds a `SandboxLaunch` per call), `services/agentd-py/agentd/chat/controller.py::_build_registry` and the child registry (pass the runtime)
- Create: `services/agentd-py/agentd/sandbox/launch.py`
- Test: `services/agentd-py/tests/test_sandbox_launch.py`

**Interfaces:**
- Consumes: `SandboxRuntime.wrap_argv`, `build_policy`, `SandboxProxy.lease/release` (the runtime now owns a started `SandboxProxy`, started at backend startup), `WorkspaceSandboxStore.hosts/folders`, `sandbox_env`.
- Produces: `@dataclass class SandboxLaunch: argv_wrapper: Callable[[list[str]], list[str]]; env: dict[str, str]; lease: ProxyLease | None; policy: SandboxPolicy`; `SandboxRuntime.launch_for(*, shadow_root: Path | None, extra_hosts: Iterable[str] = (), allow_any: bool = False) -> SandboxLaunch | None` (None when not active). `ToolOutput.sandboxed: bool = False` and `ToolOutput.outside_sandbox: bool = False` (consumed by Tasks 7 and 12).
- Note: `create_subprocess_exec` without `preexec_fn` is safe under uvloop (the ban is on `preexec_fn`); keep `start_new_session=True`.

- [ ] **Step 1: Write the failing tests** (macOS-skipped elsewhere where they spawn): `test_run_command_write_outside_blocked`, `test_run_command_in_shadow_can_write_shadow` (Review Focus 3), `test_run_command_env_has_no_token`, `test_session_keeps_wall_after_toggle_off` (Review Focus 4: start session, flip `runtime.enabled=False`, `echo x > ~/f` typed into it still fails; a new session writes), `test_unavailable_runs_unwrapped` (runtime reports unavailable → command runs, `sandboxed` false), `test_validator_commands_wrapped`.
- [ ] **Step 2: Run** — Expected: FAIL
- [ ] **Step 3: Implement**; lease released when the command (or session) ends
- [ ] **Step 4: Run** the new tests plus `tests/test_shell*.py tests/test_exec_sessions*.py` — Expected: PASS
- [ ] **Step 5: Commit** `feat(sandbox): run commands, sessions and validation inside the sandbox`

### Task 7: Recognize and annotate blocked commands

**Files:**
- Create: `services/agentd-py/agentd/sandbox/blocks.py`
- Modify: `services/agentd-py/agentd/tools/registry.py` (after a sandboxed `run_command`), `services/agentd-py/agentd/exec_sessions/tool_source.py` (start failures and `write_stdin` output reads)
- Test: `services/agentd-py/tests/test_sandbox_blocks.py`

**Interfaces:**
- Produces: `@dataclass class SandboxBlock: kind: Literal["write", "read", "network"]; target: str`; `classify_block(output: str, exit_code: int | None, policy: SandboxPolicy, refused_hosts: Sequence[str]) -> SandboxBlock | None`; `annotate(block: SandboxBlock, tool: str) -> str` (exact Global Constraints text). Blocked results get `status="sandbox_blocked"` and the annotation prepended to the output.
- Rules: any refused host → network block (first host). Otherwise scan for `Operation not permitted`, `Read-only file system`, `Permission denied`, `EPERM`, `EROFS`, `EACCES` lines and extract the path; a path outside `writable` or inside `read_only` → write; inside `unreadable` → read. A connection failure (`Operation not permitted` on connect, `Network is unreachable`, `Could not resolve host`) with no refused host → network block with target `"the network (this tool ignores the proxy)"` (Review Focus 5). Nothing matched → `None` (never relabel an ordinary error).

- [ ] **Step 1: Write the failing tests**: `test_refused_host_wins`, `test_write_outside_detected`, `test_read_only_file_detected`, `test_credential_read_detected`, `test_proxy_ignoring_tool_reported_as_network` (fixture outputs from real `ssh`/`psql`/`nc` failures captured on macOS), `test_ordinary_permission_error_inside_workspace_not_relabelled` (chmod 000 file inside workspace → None), `test_annotation_text_exact`.
- [ ] **Step 2: Run** — Expected: FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** — Expected: PASS
- [ ] **Step 5: Commit** `feat(sandbox): tell the agent when the sandbox blocked a command`

### Task 8: Escalation arguments and approvals

**Files:**
- Modify: `services/agentd-py/agentd/tools/registry.py` (schema: `outside_sandbox: boolean`, `allow_hosts: string[]`, `reason: string` — present only when the runtime is active), `services/agentd-py/agentd/exec_sessions/tool_source.py` (same for `start_session`), `services/agentd-py/agentd/tools/approvals.py` (`CommandApprovalCallback` gains `escalation: Escalation | None = None`), `services/agentd-py/agentd/domain/models.py` (`Escalation`), `services/agentd-py/agentd/chat/controller.py` (`_command_approval_cb`, `_ask_command`, `_child_command_approval_cb`, `resolve_command`)
- Test: `services/agentd-py/tests/test_sandbox_escalation.py`

**Interfaces:**
- Consumes: `WorkspaceSandboxStore.add_host`, `SandboxRuntime.launch_for(extra_hosts=…, allow_any=…)`.
- Produces: `class Escalation(BaseModel): kind: Literal["outside_sandbox", "allow_hosts"]; hosts: list[str] = []; reason: str = ""`. Gate payload gains `escalation: {kind, hosts, reason}`. `CommandDecision.remember` on an `allow_hosts` gate means "this workspace" and calls `add_host`; on `outside_sandbox` it is ignored.
- Decisions:
  - Full access (`ALLOW_ALL`): allow; `outside_sandbox` runs unwrapped; `allow_hosts` gets a lease with those hosts (not remembered).
  - Ask / Accept edits: always the card (skip `CommandRuleStore.matches`), whatever rules exist.
  - Auto: `AutoGate.decide_escalation` (Task 9); `run` allows it for this command only (a judge never writes `approved-hosts.json`), `ask` raises the card above.
  - `dontAsk` children: Auto judge if Auto, else `ApprovalOutcome.deny("policy", reason="needs the user's approval to leave the sandbox")`.
  - Sandbox not active: the arguments are ignored and the command runs as today.

- [ ] **Step 1: Write the failing tests**: `test_auto_judge_host_approval_not_remembered`, `test_full_access_outside_runs_unwrapped`, `test_ask_mode_outside_always_cards_even_with_rule`, `test_allow_hosts_remember_writes_store`, `test_outside_sandbox_remember_ignored`, `test_allowed_host_reaches_proxy_for_that_command_only`, `test_dontask_child_denied_outside_auto`, `test_arguments_ignored_when_sandbox_off`.
- [ ] **Step 2: Run** — Expected: FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** plus `tests/test_controller_command_gate.py tests/test_auto_gate_commands.py` — Expected: PASS
- [ ] **Step 5: Commit** `feat(sandbox): escalate to new hosts or outside the sandbox through approvals`

### Task 9: Auto with the sandbox

**Files:**
- Modify: `services/agentd-py/agentd/approvals/auto_rules.py` (`is_outward`), `services/agentd-py/agentd/approvals/auto_gate.py` (`decide_command(..., sandboxed: bool = False)`, `decide_stdin(..., sandboxed: bool = False)`, new `decide_escalation`), `services/agentd-py/agentd/approvals/types.py` (`PendingActionKind` adds `"sandbox_escalation"`), `services/agentd-py/agentd/approvals/judge_policy.py` (one paragraph: leaving the sandbox or reaching a new host raises risk; a host the user named counts as authorized), `services/agentd-py/agentd/chat/controller.py` (pass `sandboxed=self._sandbox.active()`)
- Test: `services/agentd-py/tests/test_auto_sandboxed.py`

**Interfaces:**
- Produces: `is_outward(parsed: ParsedCommand) -> bool` — any segment is `git push`; `gh` with `pr create|merge|close|edit|comment|review`, `release`, `repo`, `issue create|close|edit|comment`, `api` with `-X/--method` other than `GET`; `npm|pnpm|yarn publish`; `cargo publish`; `twine upload`; `uv publish`; `docker push`. `async decide_escalation(escalation: Escalation, command: str, args: list[str], request: JudgeRequest) -> AutoDecision` — always the judge (no judge → ask).
- `decide_command` when `sandboxed`: deny list → always-ask writes → `is_outward` → judge; otherwise `run` with no judge call and no note. Not sandboxed: unchanged.
- `decide_stdin` when `sandboxed`: control keys run; shell-session input is parsed as a command line and goes through the same sandboxed order; other sessions run.

- [ ] **Step 1: Write the failing tests**: `test_sandboxed_pytest_runs_without_judge` (judge spy: 0 calls), `test_sandboxed_git_push_goes_to_judge`, `test_sandboxed_gh_api_delete_outward`, `test_sandboxed_deny_list_still_denies`, `test_sandboxed_protected_write_still_asks`, `test_unsandboxed_unchanged` (pytest → judge), `test_shell_stdin_git_push_judged_when_sandboxed`, `test_repl_stdin_runs_when_sandboxed`, `test_escalation_always_judged`.
- [ ] **Step 2: Run** — Expected: FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** plus every `tests/test_auto_*.py` — Expected: PASS
- [ ] **Step 5: Commit** `feat(approvals): Auto judges only outward actions and escalations when sandboxed`

### Task 10: Tell the agents

**Files:**
- Modify: `services/agentd-py/agentd/prompting/tagged.py` (`RenderContext.sandbox: Literal["on", "off", "unavailable"] = "off"`, tag kind `sandbox`), `services/agentd-py/agentd/tools/registry.py` (`_RUN_COMMAND_DESCRIPTION` + `<<sandbox:on>>…` block), `services/agentd-py/agentd/exec_sessions/tool_source.py` (start_session description), `services/agentd-py/agentd/chat/controller_prompts.py` (`SANDBOX_NOTE`, `SANDBOX_UNAVAILABLE_NOTE` in the payload tail as `sandbox_note`), `services/agentd-py/agentd/chat/controller_loop.py` (pass `plan_context["sandbox"]`), `services/agentd-py/agentd/chat/controller.py` (render context for children)
- Test: `services/agentd-py/tests/test_sandbox_prompts.py`

**Interfaces:**
- Produces: `SANDBOX_NOTE = "Commands run in a sandbox: they can write only inside the workspace, temp and package caches, cannot read credential folders, and reach only allowed hosts (package registries, GitHub, hosts approved for this workspace). If the sandbox blocks a command, the result says so; ask with allow_hosts or outside_sandbox and a reason instead of working around it."`; `SANDBOX_UNAVAILABLE_NOTE = "The command sandbox is unavailable on this machine, so commands run unprotected."`
- The `<<sandbox:on>>` description block states the same limits and the two arguments.

- [ ] **Step 1: Write the failing tests**: `test_main_system_prompt_golden_unchanged` (existing golden, sandbox on), `test_payload_tail_has_sandbox_note_when_on`, `test_unavailable_note`, `test_no_note_when_off`, `test_child_run_command_description_mentions_sandbox`, `test_prompt_leak_lint_still_passes`.
- [ ] **Step 2: Run** — Expected: FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** plus `tests/test_prompt_leak_lint.py tests/test_controller_prompt*.py` — Expected: PASS
- [ ] **Step 5: Commit** `feat(sandbox): tell agents the sandbox is on and how to ask`

### Task 11: Client contract and extension host

**Files:**
- Modify: `apps/editor-client/src/contracts/task-contracts.ts` (`SandboxStatusSchema`, `ApprovalState.sandbox`, `SetApprovalModeOptions.sandbox?: boolean`, `SandboxConfigSchema`, client methods), `apps/editor-client/src/client/http-backend-client.ts` (`getSandbox()`, `setSandboxHosts(hosts)`, `setSandboxFolders(folders)`, map `sandbox` both ways), `apps/vscode-extension/src/approval-mode.ts` (read `crucible.sandbox.enabled`, default `true`, send `sandbox` with every `setApprovalMode`; `setSandboxEnabled(enabled)` persists after acknowledgement), `apps/vscode-extension/src/settings-data.ts` (`settings/sandbox`, `settings/setSandboxEnabled`, `settings/setSandboxHosts`, `settings/setSandboxFolders`), `apps/vscode-extension/src/extension.ts` (one notice per window when `available` is false: `Sandbox unavailable: <reason> — commands run unprotected.`)
- Test: `apps/editor-client/src/client/http-backend-client.test.ts`, `apps/vscode-extension/test/approval-mode.test.ts`, `apps/vscode-extension/test/settings-data.test.ts`

**Interfaces:**
- Consumes: Task 4 routes and `ApprovalState.sandbox`.
- Produces (webview messages): `sandboxState { status, defaultHosts, hosts, folders }`; webview posts `setSandboxEnabled { enabled }`, `setSandboxHosts { hosts }`, `setSandboxFolders { folders }`. `ApprovalModeView.snapshot.sandbox?: { enabled, available, reason }`.

- [ ] **Step 1: Write the failing tests**: client maps `sandbox` both ways and omits it when undefined; coordinator sends saved `sandbox` on initialize and on every mode change, persists the toggle only after the backend acknowledges; older backend (no `sandbox` in state) hides the toggle; settings handler round-trips hosts/folders; unavailable notice shown once.
- [ ] **Step 2: Run** `npm run -w @crucible/editor-client test && npm run -w @crucible/editor-client build && npm run -w crucible-vscode-extension test` — Expected: FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** the same plus `npm run typecheck` — Expected: PASS
- [ ] **Step 5: Commit** `feat(extension): sandbox switch, workspace hosts and folders, unavailable notice`

### Task 12: Webview: Settings, menu line, cards and pills

**Files:**
- Create: `apps/vscode-extension/webview-ui/src/settings/sections/SandboxSection.tsx`
- Modify: `apps/vscode-extension/webview-ui/src/settings/sections/meta.ts` and `NavRail` (new "Sandbox" entry), `apps/vscode-extension/webview-ui/src/components/ApprovalMenu.tsx` (footer line), `apps/vscode-extension/webview-ui/src/components/messages/gates/CommandGate.tsx` (escalation variants), the tool pill component that renders `approvalNote` (marker), `apps/vscode-extension/webview-ui/src/types.ts`
- Test: `SandboxSection.test.tsx`, `ApprovalMenu.test.tsx`, `src/test/gates.test.tsx`, the pill's test

**Interfaces:**
- Consumes: Task 11 messages; gate payload `escalation` (Task 8); `ToolOutput.sandboxed`/`outside_sandbox` and `status="sandbox_blocked"` carried to pills (add `sandboxed`, `outsideSandbox` to `chat/tool_events.py::trace_to_tool_events` and the live `tool_result` event).
- Copy:
  - Settings: title "Sandbox"; switch "Run agent commands in a sandbox"; status line "On" / "Off" / "Unavailable: <reason> — commands run unprotected"; lists "Allowed hosts for this workspace" (defaults shown read-only, remembered hosts removable) and "Extra writable folders".
  - Menu footer: "Sandbox: on" / "Sandbox: off" / "Sandbox unavailable — commands run unprotected".
  - Cards: title "Allow network access?" with host chips, buttons Allow once / Allow for this workspace / Reject; title "Run outside the sandbox?" with the blocked reason and the agent's reason, buttons Allow once / Reject.
  - Pill marker: "outside sandbox"; blocked pill shows the annotation's first sentence.

- [ ] **Step 1: Write the failing tests** for each copy string and button set, the remember flag on "Allow for this workspace", no remember options on the outside card, and the toggle posting `setSandboxEnabled`.
- [ ] **Step 2: Run** `cd apps/vscode-extension/webview-ui && npx vitest run` — Expected: FAIL
- [ ] **Step 3: Implement** with existing components and tokens
- [ ] **Step 4: Run** the full webview suite — Expected: PASS
- [ ] **Step 5: Commit** `feat(webview): sandbox settings, menu status, escalation cards and pill markers`

### Task 13: Docs and live verification

**Files:**
- Modify: `CLAUDE.md` (a "Command sandbox" bullet group under the Reactive controller section and env/config entries), the spec's status line
- Test: none new; live run evidence recorded in the ledger

- [ ] **Step 1: Document** the package, routes, files, defaults, Auto change and the dev-test recipe (backend on a spare port, extension-development window, `crucible.backendBaseUrl` in user settings because it is machine-scoped).
- [ ] **Step 2: Run the full suites**: `cd services/agentd-py && PATH="$HOME/.crucible/runtime/bin:$PATH" .venv/bin/python -m pytest --color=no --timeout 120`; `npm run build && npm run typecheck && npm run test`; webview `npx vitest run` — Expected: all pass.
- [ ] **Step 3: Live run on macOS** (ChatGPT plan, Auto then Ask): each spec success criterion — `rm -rf ~/`-shaped command blocked with the deny list bypassed (call the runtime directly), credential reads fail, `curl` to a new host → card → "Allow for this workspace" → retry passes, `npm ci`/`uv sync`/`pytest`/`git fetch` without prompts, `git push` asks to run outside, Settings section and cards in Dark/Light at wide and 360px. Count judge calls for the same three prompts with the sandbox off and on.
- [ ] **Step 4: Commit** `docs(sandbox): document the command sandbox`
