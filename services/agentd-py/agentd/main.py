from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from agentd.workspace_migration import migrate_legacy_dirs

# Legacy-dir migration must run before anything below creates state dirs
# (the log-file mkdir just under here would otherwise pre-create the new
# state root and permanently strand the legacy dirs' contents).
migrate_legacy_dirs(Path(os.getenv("CRUCIBLE_WORKSPACE_PATH", str(Path.cwd()))))

# Attach handlers to the agentd logger directly so --reload doesn't suppress
# them (basicConfig is a no-op when uvicorn already owns the root logger).
_agentd_logger = logging.getLogger("agentd")
_agentd_logger.setLevel(logging.INFO)
if not _agentd_logger.handlers:
    # Date included, not just the clock. The log is append-only across days and the
    # backend is restarted constantly, so a bare HH:MM:SS cannot be correlated with
    # anything dated — artifact mtimes, a file on disk, a user's report of "this
    # morning". Diagnosing a live incident meant guessing which day a line belonged
    # to, and a run that spans midnight silently reorders.
    _fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _h_stdout = logging.StreamHandler(sys.stdout)
    _h_stdout.setFormatter(_fmt)
    _agentd_logger.addHandler(_h_stdout)
    # Also write to a file so logs are tailable regardless of how the server was started.
    _log_file = Path(os.environ.get("CRUCIBLE_LOG_FILE", ".crucible/state/agentd.log"))
    _log_file.parent.mkdir(parents=True, exist_ok=True)
    _h_file = logging.FileHandler(_log_file)
    _h_file.setFormatter(_fmt)
    _agentd_logger.addHandler(_h_file)
_agentd_logger.propagate = False

from fastapi import FastAPI, Request

from agentd.api.agents_routes import build_agents_router
from agentd.api.routes import build_router
from agentd.auth import AUTH_STATE, auth_middleware, health_payload, install_auth
from agentd.domain.models import ScopePolicy, ScopeRemember, ScopeTrigger, ShellPolicy
from agentd.orchestrator.engine import AgentOrchestrator
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.patch.engine import PatchEngine
from agentd.reasoning.contracts import ReasoningEngine
from agentd.reasoning.engine import DefaultReasoningEngine
from agentd.retrieval.artifact_client import RetrievalArtifactClient
from agentd.runtime.adapters import build_evidence_adapter, build_planning_adapter
from agentd.startup import in_background
from agentd.storage.sqlite_store import SQLiteTaskStore
from agentd.validation.command_validator import CommandValidator
from agentd.workspace.shadow import ShadowWorkspaceManager

# Auth runs outermost (spec §3.3). Passed in the constructor: a later add_middleware
# would insert OUTSIDE it, which tests/test_main_auth_wiring.py guards against.
app = FastAPI(
    title="crucible agentd-py", version="0.1.0", middleware=[auth_middleware(AUTH_STATE)])

database_path = Path(os.getenv("CRUCIBLE_DB_PATH", ".crucible/state/agentd.sqlite3")).resolve()
shadow_root_path = Path(os.getenv("CRUCIBLE_SHADOW_ROOT", ".crucible/state/shadows")).resolve()
ast_cutover_mode = os.getenv("CRUCIBLE_AST_CUTOVER_MODE", "hard").strip().lower()
if ast_cutover_mode != "hard":
    msg = (
        "CRUCIBLE_AST_CUTOVER_MODE must be 'hard' for Phase 1 reliability "
        f"(received: {ast_cutover_mode!r})"
    )
    raise RuntimeError(msg)

store = SQLiteTaskStore(database_path=database_path)
raw_checkpoint_retention = os.getenv("CRUCIBLE_CHECKPOINT_RETENTION_TASKS", "20")
try:
    checkpoint_retention_tasks = int(raw_checkpoint_retention)
except ValueError:
    checkpoint_retention_tasks = 20
workspace_manager = ShadowWorkspaceManager(
    root_path=shadow_root_path,
    checkpoint_retention_tasks=checkpoint_retention_tasks,
)
patch_engine = PatchEngine()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


reasoning_backend = os.getenv("CRUCIBLE_REASONING_BACKEND", "openai").strip().lower()
reasoning_engine: ReasoningEngine
# Why the configured provider could not be built, if it could not. Surfaced on
# GET /v1/config so the settings UI can say what is wrong instead of the user
# meeting a dead backend. None on the scripted path and on every healthy start.
_provider_error: str | None = None
if reasoning_backend == "scripted":
    reasoning_engine = ScriptedReasoningEngine(
        plan={
            "analysis": "Scaffold run",
            "steps": [
                {
                    "id": "S1",
                    "goal": "Create scaffold file",
                    "targets": [{"path": "generated.txt", "intent": "new"}],
                    "risk": "low",
                }
            ],
            "expected_files": ["generated.txt"],
            "stop_conditions": ["validation passes"],
        },
        patches=[
            {
                "candidates": [
                    {
                        "candidate_id": "c1",
                        "patch_ops": [
                            {
                                "op": "create_file",
                                "file": "generated.txt",
                                "content": "ok",
                                "reason": "demo",
                            }
                        ],
                    }
                ]
            }
        ],
    )
else:
    from agentd.providers.factory import build_transport, resolve_model
    from agentd.providers.reasoning_effort import parse_effort
    from agentd.providers.unconfigured import build_transport_or_placeholder

    # Degrade, don't abort. A provider missing its key or base URL used to raise
    # here and kill uvicorn at import — the extension saw a 60s health timeout
    # and the user a raw traceback, with no way back: correcting the provider
    # happens in Settings, which needs a live backend to validate against. Now
    # the app boots, /v1/config reports the reason, and the existing hot-swap
    # (which builds a fresh transport) is the recovery path.
    transport, _provider_error = build_transport_or_placeholder(
        reasoning_backend, build_transport
    )
    if _provider_error is not None:
        logging.getLogger("agentd.startup").error(
            "[provider] %s is not configured: %s — the backend is running but "
            "cannot reach a model. Fix it in Crucible Settings → Provider.",
            reasoning_backend, _provider_error,
        )
    try:
        _resolved_model = resolve_model(reasoning_backend)
    except Exception as exc:  # openai_compatible raises when no model is set
        if _provider_error is None:
            _provider_error = str(exc)
        _resolved_model = ""
    reasoning_engine = DefaultReasoningEngine(
        model=_resolved_model, transport=transport
    )

validator = CommandValidator.from_env()
evidence_adapter = build_evidence_adapter(os.getenv("CRUCIBLE_EVIDENCE_ADAPTER", "generic"))
planning_adapter = build_planning_adapter(os.getenv("CRUCIBLE_PLANNING_ADAPTER", "generic"))

_semantic_index: object = None
from agentd.local_models import LocalModels  # noqa: E402

_local_models = LocalModels.from_env(os.environ)
if _local_models.enabled and (
    "CRUCIBLE_LOCAL_MODELS" in os.environ or _bool_env("CRUCIBLE_SEMANTIC_RETRIEVAL", False)
):
    try:
        from agentd.retrieval.semantic_index import SemanticIndex
        _semantic_index = SemanticIndex.from_env(local_models=_local_models)
    except ImportError:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "CRUCIBLE_SEMANTIC_RETRIEVAL=true but lancedb/sentence-transformers not installed; "
            "falling back to graph-only retrieval. "
            "Install with: pip install 'crucible-agentd[semantic]'"
        )

retrieval_client = RetrievalArtifactClient.from_env(
    evidence_adapter=evidence_adapter,
    semantic_index=_semantic_index,
)

_discovery_workspace = os.getenv("CRUCIBLE_WORKSPACE_PATH", str(Path.cwd())).strip()
if _discovery_workspace:
    from agentd.retrieval.discovery_warmer import DiscoveryWarmer

    _discovery_warmer = DiscoveryWarmer(retrieval_client, _discovery_workspace)

    async def _start_discovery_warmer() -> None:
        _discovery_warmer.start()

    async def _stop_discovery_warmer() -> None:
        await asyncio.to_thread(_discovery_warmer.stop)

    app.router.add_event_handler("startup", _start_discovery_warmer)
    app.router.add_event_handler("shutdown", _stop_discovery_warmer)


async def _load_token_encoding() -> None:
    # Loaded here and nowhere else: counting code only reads it (context_metrics).
    from agentd.context_metrics import load_encoding
    await asyncio.to_thread(load_encoding)


# The first load may download the encoding, so it must not hold up /health.
app.router.add_event_handler("startup", in_background(_load_token_encoding, "token-encoding"))

if _semantic_index is not None:
    _warm_target = _semantic_index

    async def _warm_semantic_model() -> None:
        # Off the chat path: otherwise the first chat's retrieval seed loads the model.
        await asyncio.to_thread(_warm_target.warmup)  # type: ignore[attr-defined]

    app.router.add_event_handler(
        "startup", in_background(_warm_semantic_model, "semantic-model-warmup"))

if _local_models.enabled and reasoning_backend != "scripted":
    async def _warm_local_models() -> None:
        from agentd.memory.embedder import Embedder
        from agentd.memory.reranker import Reranker

        def warm() -> None:
            Embedder(
                os.getenv("CRUCIBLE_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
                local_models=_local_models,
            ).warmup()
            Reranker(
                os.getenv("CRUCIBLE_MEMORY_RERANKER_MODEL", "BAAI/bge-reranker-base"),
                local_models=_local_models,
                bounded=_bool_env("CRUCIBLE_MEMORY_RECALL_V2", False),
            ).warmup()

        await asyncio.to_thread(warm)

    app.router.add_event_handler(
        "startup", in_background(_warm_local_models, "local-model-warmup"))
def _scope_policy_env() -> ScopePolicy:
    raw = os.getenv("CRUCIBLE_SCOPE_POLICY", "strict").strip().lower()
    try:
        return ScopePolicy(raw)
    except ValueError:
        return ScopePolicy.STRICT


def _scope_trigger_env() -> ScopeTrigger:
    raw = os.getenv("CRUCIBLE_SCOPE_TRIGGER", "nearby").strip().lower()
    try:
        return ScopeTrigger(raw)
    except ValueError:
        return ScopeTrigger.NEARBY


def _shell_policy_env() -> ShellPolicy:
    raw = os.getenv("CRUCIBLE_SHELL_POLICY", "ask").strip().lower()
    try:
        return ShellPolicy(raw)
    except ValueError:
        return ShellPolicy.ASK


def _scope_remember_env() -> ScopeRemember:
    raw = os.getenv("CRUCIBLE_SCOPE_REMEMBER", "task").strip().lower()
    try:
        return ScopeRemember(raw)
    except ValueError:
        return ScopeRemember.TASK


from agentd.chat.controller_factory import (
    select_chat_handler,
    tasks_reachable,
    warn_if_incoherent_flags,
)
from agentd.chat.storage import ChatThreadStore
from agentd.memory.config import MemoryConfig
from agentd.memory.harness import NO_OP_HARNESS, build_memory_harness

_chat_db_path = Path(os.getenv("CRUCIBLE_CHAT_DB_PATH", ".crucible/state/chat.sqlite3")).resolve()
_chat_db_path.parent.mkdir(parents=True, exist_ok=True)
_chat_thread_store = ChatThreadStore(_chat_db_path)

# workspace_path for chat — the real repo being edited; defaults to cwd if not set
_chat_workspace_path = os.getenv("CRUCIBLE_WORKSPACE_PATH", str(Path.cwd()))
from agentd.providers.factory import MODEL_ENV_VAR

_chat_model = os.getenv(
    MODEL_ENV_VAR.get(reasoning_backend, "CRUCIBLE_OPENAI_MODEL"),
    # A ChatGPT plan's models come only from the signed-in account's catalog; a guessed
    # name would fail as an unsupported capability. Empty until one is picked.
    ("" if reasoning_backend == "chatgpt" else
     "claude-sonnet-5-5" if reasoning_backend == "claude_subscription" else "gpt-4o"),
)
# Within-run compaction for task ToolLoop steps (no-op unless CRUCIBLE_MEMORY_ENABLED;
# scripted backend has no transport). Disjoint run_id namespace (task_id) from the chat
# controller's harness — both share the one memory DB file, which sqlite handles fine.
# Not built when nothing can start a task: it would load and warm its own memory models.
_task_memory_harness = (
    build_memory_harness(
        MemoryConfig.from_env(os.environ), transport, _chat_model,
        workspace_path=_chat_workspace_path,  # enables consolidation (workspace scope)
        local_models=_local_models,
    )
    if reasoning_backend != "scripted" and tasks_reachable()
    else NO_OP_HARNESS
)

orchestrator = AgentOrchestrator(
    store=store,
    reasoning_engine=reasoning_engine,
    memory_harness=_task_memory_harness,
    validator=validator,
    patch_engine=patch_engine,
    workspace_manager=workspace_manager,
    retrieval_client=retrieval_client,
    planning_adapter=planning_adapter,
    max_attempts_per_step=_int_env("CRUCIBLE_MAX_ATTEMPTS_PER_STEP", 3),
    step_scoped_mode=_bool_env("CRUCIBLE_STEP_SCOPED_MODE", True),
    patch_candidate_count=_int_env("CRUCIBLE_PATCH_CANDIDATE_COUNT", 3),
    scope_policy=_scope_policy_env(),
    scope_trigger=_scope_trigger_env(),
    scope_remember=_scope_remember_env(),
    scope_timeout_sec=_float_env("CRUCIBLE_SCOPE_TIMEOUT_SEC", 600.0),
    shell_policy=_shell_policy_env(),
    command_decision_timeout_sec=_float_env("CRUCIBLE_COMMAND_DECISION_TIMEOUT_SEC", 0.0),
    chat_store=_chat_thread_store,
)

# scripted backend has no provider transport — both chat handlers require a real one.
# CRUCIBLE_CHAT_CONTROLLER flag-selects the new ChatController vs the legacy ChatAgent.
_chat_agent = select_chat_handler(
    workspace_path=_chat_workspace_path,
    transport=transport,  # defined for all real backends
    model=_chat_model,
    thread_store=_chat_thread_store,
    orchestrator=orchestrator,
    broadcaster=orchestrator.broadcaster,
    retrieval_client=retrieval_client,
    local_models=_local_models,
    shell_policy=_shell_policy_env(),
    command_decision_timeout_sec=_float_env("CRUCIBLE_COMMAND_DECISION_TIMEOUT_SEC", 0.0),
) if reasoning_backend != "scripted" else None

# MCP servers connect once per process at APP STARTUP, not at construction — this
# module runs synchronously at import (no event loop), and the SDK's stdio/http
# transports are async context managers held open by per-server tasks. Shutdown
# mirrors it so stdio subprocesses die with us.
_mcp_manager = getattr(_chat_agent, "_mcp_manager", None)
if _mcp_manager is not None:
    app.router.add_event_handler("startup", _mcp_manager.start)
    app.router.add_event_handler("shutdown", _mcp_manager.shutdown)

# Exec sessions: reap orphans a crashed predecessor leaked (registry file) at
# startup; kill-all + clear at shutdown. Both best-effort inside the manager.
_exec_manager = getattr(_chat_agent, "_exec_sessions", None)
if _exec_manager is not None:

    def _reap_exec_orphans() -> None:
        _exec_manager.reap_orphans()

    app.router.add_event_handler("startup", _reap_exec_orphans)
    app.router.add_event_handler("shutdown", _exec_manager.shutdown)

# Sub-agents never survive a restart (spec §11.5): fail their rows, drop their gates,
# delete their shadows. Flag-tolerant: the legacy ChatAgent has no reap.
_reap_subagents = getattr(_chat_agent, "reap_subagents", None)
if _reap_subagents is not None:
    app.router.add_event_handler("startup", _reap_subagents)

_chat_memory = getattr(_chat_agent, "_memory_harness", None)
if _chat_memory is not None:
    _curator = _chat_memory.configure_curator(
        _chat_thread_store,
        lambda thread_id: thread_id in getattr(_chat_agent, "_active_turns", {}),
    )
    if _curator is not None:
        app.router.add_event_handler("startup", _curator.start)
        app.router.add_event_handler("shutdown", _curator.stop)

warn_if_incoherent_flags(logging.getLogger("agentd.startup"))

# Hot-swap seam: one ProviderRuntime holding every live DefaultReasoningEngine.
# The legacy ChatAgent holds a raw transport (not an engine) — getattr yields None
# there; the controller path is the live one.
from agentd.providers.runtime import ProviderRuntime
from agentd.providers.window_sinks import collect_window_sinks

provider_runtime: ProviderRuntime | None = None
if reasoning_backend != "scripted":
    _engines = [reasoning_engine]
    _ctrl_engine = getattr(_chat_agent, "_reasoning", None)
    if isinstance(_ctrl_engine, DefaultReasoningEngine) and _ctrl_engine is not reasoning_engine:
        _engines.append(_ctrl_engine)
    # Both harnesses in one process compact against the same window: the task loop's
    # (_task_memory_harness) and the chat controller's own (built inside
    # select_chat_handler). A NO_OP_HARNESS accepts set_window_tokens and ignores it,
    # so neither needs a guard here. See providers/window_sinks.py for why this is a
    # separately unit-tested pure function rather than inline getattr here.
    _window_sinks = collect_window_sinks(_task_memory_harness, _chat_agent)
    provider_runtime = ProviderRuntime(
        backend=reasoning_backend,
        model=_chat_model,
        engines=_engines,
        window_sinks=_window_sinks,
        context_window=MemoryConfig.from_env(os.environ).window_tokens,
        config_error=_provider_error,
        transport=transport,
        reasoning_effort=parse_effort(os.getenv("CRUCIBLE_REASONING_EFFORT")),
    )

    async def _attach_context_runtime() -> None:
        attach = getattr(_chat_agent, 'attach_context_runtime', None)
        if attach is not None:
            attach(provider_runtime)

    app.router.add_event_handler('startup', _attach_context_runtime)
    _close_context_usage = getattr(_chat_agent, 'close_context_usage', None)
    if _close_context_usage is not None:
        app.router.add_event_handler('shutdown', _close_context_usage)

    async def _apply_startup_reasoning_effort() -> None:
        # __init__ cannot await, and apply_reasoning_effort is otherwise only reached
        # through swap(). Without this the seeded rung would be reported by
        # GET /v1/config but never sent, which is worse than not working.
        await provider_runtime.apply_reasoning_effort(provider_runtime.reasoning_effort)

    if provider_runtime.reasoning_effort is not None:
        app.router.add_event_handler("startup", _apply_startup_reasoning_effort)

    async def _probe_newline_capability() -> None:
        """Check once at boot whether strict json_schema can emit a newline.

        A grammar that forbids \\n inside string values produces valid, schema-passing
        JSON, so nothing raises and the sticky downgrade never fires — the only symptom
        is every written file arriving as one line. Measured on NVIDIA NIM. Probing at
        startup rather than lazily inside generate_json keeps it off the request path,
        matches the other capability probes, and means the first real turn does not pay
        for it. Best-effort: a failure leaves strict mode untouched.
        """
        probe = getattr(transport, "_ensure_newline_capability", None)
        if probe is None or not _resolved_model:
            return
        try:
            await probe(_resolved_model)
        except Exception:
            logging.getLogger("agentd.startup").debug(
                "newline capability probe skipped", exc_info=True)

    # In the background, not as a startup step: on a slow endpoint (NIM, measured 40-60s)
    # the probe outlasted the managed spawn's 60s /health deadline, so the extension
    # killed every backend it started. The first turn may begin before the probe ends;
    # the transport's sticky JSON-mode downgrade already covers a mid-run switch.
    app.router.add_event_handler(
        "startup", in_background(_probe_newline_capability, "newline-capability-probe"))

app.include_router(
    build_router(
        store,
        orchestrator,
        workspace_manager,
        retrieval_client,
        _chat_agent,
        provider_runtime=provider_runtime,
        mcp_manager=_mcp_manager,
        local_models=_local_models,
    )
)
# Settings › Agents (spec §10): writes .crucible/agents in THIS backend's workspace only.
app.include_router(build_agents_router(
    workspace=_chat_workspace_path,
    mcp_server_names=lambda: [s.name for s in _mcp_manager.statuses()] if _mcp_manager else []))
# The command sandbox: probed in the background (it spawns a process), Settings routes
# for this backend's workspace only.
_sandbox_runtime = getattr(_chat_agent, "sandbox_runtime", None)
if _sandbox_runtime is not None:
    from agentd.api.sandbox_routes import build_sandbox_router  # noqa: E402

    async def _start_sandbox() -> None:
        try:
            await _sandbox_runtime.start()  # probe, then the proxy
        except Exception:  # noqa: BLE001 — the runtime reports why; the backend keeps serving
            _agentd_logger.warning("[sandbox] failed to start", exc_info=True)
        _agentd_logger.info("[sandbox] %s", _sandbox_runtime.status().model_dump())

    app.router.add_event_handler("startup", in_background(_start_sandbox, "sandbox-start"))
    app.router.add_event_handler("shutdown", _sandbox_runtime.close)
    app.include_router(build_sandbox_router(_sandbox_runtime, _sandbox_runtime.store))
# Sign in with ChatGPT (spec 2026-10-06). The service is lazy: no network, no files
# touched until a sign-in or a `chatgpt` transport first needs it.
from agentd.api.chatgpt_auth_routes import build_chatgpt_auth_router  # noqa: E402
from agentd.chatgpt_auth.service import chatgpt_auth_service  # noqa: E402

app.include_router(build_chatgpt_auth_router(chatgpt_auth_service))

from agentd.api.claude_auth_routes import build_claude_auth_router  # noqa: E402
from agentd.claude_auth.service import claude_auth_service  # noqa: E402

app.include_router(build_claude_auth_router(claude_auth_service))


async def _close_claude_auth() -> None:
    from agentd.claude_auth import service as svc
    if svc._service is not None:
        await svc._service.aclose()


app.router.add_event_handler("shutdown", _close_claude_auth)


async def _close_chatgpt_auth() -> None:
    from agentd.chatgpt_auth import service as _svc

    if _svc._SERVICE is not None:  # never construct it just to close it
        await _svc._SERVICE.aclose()


app.router.add_event_handler("shutdown", _close_chatgpt_auth)
install_auth(app, AUTH_STATE)



@app.get("/v1/project/session")
async def project_session(request: Request) -> dict[str, object]:
    from agentd.project_ownership import OWNER
    return OWNER.status(OWNER.owns(request.scope["headers"]))

@app.get("/health")
async def healthcheck(request: Request) -> dict[str, object]:
    # No token required (the middleware exempts exactly GET /health); with a nonce it
    # proves the server holds the token and names its pid (spec §3.4).
    return health_payload(AUTH_STATE, request.scope["query_string"])
