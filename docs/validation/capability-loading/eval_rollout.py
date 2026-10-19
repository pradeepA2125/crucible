#!/usr/bin/env python3
"""Matched flag-off / flag-on rollout evaluation for on-demand MCP loading.

Starts an isolated backend (own databases, log and memory store) for one model and one
setting of CRUCIBLE_CAPABILITY_LOADING_ENABLED, drives a fixed set of chat scenarios over
HTTP/SSE, answers approval gates the way a user would (approve, or reject in the denial
scenario), and writes one JSON record per turn: provider usage from /usage, wall time,
tool calls, the loader's arguments, refusals and corrections from the backend log, and
the final answer. Correctness is judged afterwards against ground truth.

    python eval_rollout.py --model luna --flag on --suite main --workspace WS --out OUT.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))
from _backend_auth import auth_headers  # noqa: E402

PYTHON = REPO / "services/agentd-py/.venv/bin/python"

# Mirrors the managed spawn of the installed extension (buildBackendEnv + this user's
# settings), so both arms see the production prompt.
BASE_ENV = {
    "CRUCIBLE_CHAT_CONTROLLER": "1", "CRUCIBLE_TASK_SUBSYSTEM": "0",
    "CRUCIBLE_MCP_ENABLED": "1", "CRUCIBLE_SKILLS_ENABLED": "1",
    "CRUCIBLE_SKILLS_DISABLED": ",".join([
        "brainstorming", "dispatching-parallel-agents", "executing-plans",
        "finishing-a-development-branch", "receiving-code-review", "requesting-code-review",
        "subagent-driven-development", "systematic-debugging", "test-driven-development",
        "using-git-worktrees", "using-superpowers", "verification-before-completion",
        "writing-plans", "writing-skills"]),
    "CRUCIBLE_ADAPTIVE_READS_ENABLED": "true", "CRUCIBLE_COMPACT_CONTEXT_ENABLED": "true",
    "CRUCIBLE_TOOL_BATCHING_ENABLED": "true", "CRUCIBLE_EXEC_SESSIONS_ENABLED": "1",
    "CRUCIBLE_WEB_FETCH_ENABLED": "1", "CRUCIBLE_WEB_SEARCH_ENABLED": "1",
    "CRUCIBLE_MEMORY_ENABLED": "true", "CRUCIBLE_MEMORY_RECALL_V2": "true",
    "CRUCIBLE_MEMORY_CURATOR_V2": "true", "CRUCIBLE_MEMORY_COMPACT_EVIDENCE": "true",
    "CRUCIBLE_SHELL_POLICY": "allow_all", "CRUCIBLE_SCOPE_POLICY": "ask",
    "CRUCIBLE_SCOPE_TRIGGER": "any", "CRUCIBLE_STEP_REVIEW_AUTO_ACCEPT": "false",
    "CRUCIBLE_LOCAL_MODELS": "1", "CRUCIBLE_PROVIDER_MAX_RPM": "35",
    "CRUCIBLE_RIPGREP_CMD": str(Path.home() / ".crucible/runtime/bin/rg"),
}

MODELS = {
    "luna": {"CRUCIBLE_REASONING_BACKEND": "chatgpt", "CRUCIBLE_CHATGPT_MODEL": "gpt-6-luna",
             "CRUCIBLE_CHATGPT_REGISTRATION": "reg_bc8adf40a761",
             "CRUCIBLE_REASONING_EFFORT": "medium", "CRUCIBLE_MEMORY_WINDOW_TOKENS": "272000"},
    "nemotron": {"CRUCIBLE_REASONING_BACKEND": "openai_compatible",
                 "CRUCIBLE_OPENAI_COMPAT_BASE_URL": "https://integrate.api.nvidia.com/v1",
                 "CRUCIBLE_OPENAI_COMPAT_MODEL": "nvidia/nemotron-3-ultra-550b-a55b",
                 "CRUCIBLE_OPENAI_COMPAT_API_KEY": "${NVIDIA_API_KEY}",
                 "CRUCIBLE_MEMORY_WINDOW_TOKENS": "128000"},
    "qwen-local": {"CRUCIBLE_REASONING_BACKEND": "ollama",
                   "CRUCIBLE_OLLAMA_MODEL": "qwen3.6:35b-a3b-q4_K_M",
                   "CRUCIBLE_OLLAMA_NUM_CTX": "65536", "CRUCIBLE_MEMORY_WINDOW_TOKENS": "65536"},
    "gptoss-local": {"CRUCIBLE_REASONING_BACKEND": "ollama", "CRUCIBLE_OLLAMA_MODEL": "gpt-oss:20b",
                     "CRUCIBLE_OLLAMA_NUM_CTX": "65536", "CRUCIBLE_MEMORY_WINDOW_TOKENS": "65536"},
    "gemma-cloud": {"CRUCIBLE_REASONING_BACKEND": "ollama", "CRUCIBLE_OLLAMA_MODEL": "gemma4:31b-cloud",
                    "CRUCIBLE_OLLAMA_NUM_CTX": "65536", "CRUCIBLE_MEMORY_WINDOW_TOKENS": "65536"},
}

# Thread = list of user messages sent in order. Prompts are what a user would type; none
# names the capability or the loader.
SUITES = {
    "main": [
        {"name": "greeting", "turns": ["hi"]},
        {"name": "code_question", "turns": [
            "Where is the default compaction reserve (in tokens) set for the memory harness, "
            "and what is it?"]},
        {"name": "vague_browser", "turns": ["what does example.com say right now?"]},
        {"name": "browser_multistep", "turns": [
            "Go to books.toscrape.com, open the Poetry category, and tell me the first "
            "book's title and price.",
            "Open that book's page. How many copies are in stock?"]},
        {"name": "denied_gate", "deny_mcp": True, "turns": [
            "Take a screenshot of books.toscrape.com for me."]},
    ],
    # First turns of fresh threads that need a browser (or a page read), repeated with
    # --repeat to estimate how often the model finds the capability.
    "discovery": [
        {"name": "explicit_browser_count", "turns": [
            "Open books.toscrape.com in the browser and tell me how many book categories "
            "the sidebar lists."]},
        {"name": "multistep_first_turn", "turns": [
            "Go to books.toscrape.com, open the Poetry category, and tell me the first "
            "book's title and price."]},
        {"name": "screenshot", "turns": ["Take a screenshot of books.toscrape.com for me."]},
        {"name": "vague_page", "turns": ["what does example.com say right now?"]},
    ],
    "compaction": [
        {"name": "long_browser_session", "turns": [
            "Open books.toscrape.com in the browser and tell me how many book categories "
            "the sidebar lists.",
            "Open the Travel category and list the book titles there.",
            "Now the Mystery category: which book on its first page is the most expensive?",
            "Go to the Historical Fiction category and tell me the price of its first book.",
            "Click into that book and tell me its UPC.",
            "Go back to the home page in the browser. What's the title of the last book "
            "on the first page?"]},
    ],
}

TERMINAL_EVENTS = {"chat_done", "done"}


class Backend:
    def __init__(self, model: str, flag: str, run_dir: Path, workspace: Path,
                 window: str | None) -> None:
        self.run_dir = run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        state = run_dir / "state"
        env = dict(os.environ)
        env.update(BASE_ENV)
        for key, value in MODELS[model].items():
            env[key] = os.path.expandvars(value)
        env.update({
            "CRUCIBLE_WORKSPACE_PATH": str(workspace),
            "CRUCIBLE_CAPABILITY_LOADING_ENABLED": "true" if flag == "on" else "false",
            "CRUCIBLE_DB_PATH": str(state / "agentd.sqlite3"),
            "CRUCIBLE_CHAT_DB_PATH": str(state / "chat.sqlite3"),
            "CRUCIBLE_MEMORY_DB_PATH": str(state / "memory.sqlite3"),
            "CRUCIBLE_MEMORY_STORE_PATH": str(state / "memory-store"),
            "CRUCIBLE_SHADOW_ROOT": str(state / "shadows"),
            "CRUCIBLE_LOG_FILE": str(run_dir / "agentd.log"),
            "CRUCIBLE_LOG_DIR": str(run_dir / "logs"),
            "CRUCIBLE_RETRIEVAL_SNAPSHOT_PATH": str(workspace / ".crucible/index-snapshot.json"),
        })
        if window:
            env["CRUCIBLE_MEMORY_WINDOW_TOKENS"] = window
        self.log_path = run_dir / "agentd.log"
        self.proc = subprocess.Popen(
            [str(PYTHON), "-m", "agentd.serve", "--port", "0"], cwd=run_dir, env=env,
            stdout=subprocess.PIPE, stderr=open(run_dir / "stderr.log", "w"), text=True)
        assert self.proc.stdout is not None
        stdout = self.proc.stdout
        port = None
        for line in stdout:
            if line.startswith("CRUCIBLE_SERVE "):
                port = json.loads(line.split(" ", 1)[1])["port"]
                break
        if port is None:
            raise RuntimeError(f"backend exited before announcing a port; see {run_dir}/stderr.log")
        threading.Thread(target=lambda: [None for _ in stdout], daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"
        for _ in range(240):
            try:
                self.headers = auth_headers(self.base)
                urllib.request.urlopen(self._req("/v1/config"), timeout=5).read()
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("backend never became healthy")

    def _req(self, path: str, payload: object | None = None) -> urllib.request.Request:
        headers = {**getattr(self, "headers", {}), "Content-Type": "application/json"}
        data = None if payload is None else json.dumps(payload).encode()
        return urllib.request.Request(self.base + path, data=data, headers=headers)

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self._req(path), timeout=30) as response:
            return json.load(response)

    def post(self, path: str, payload: object) -> dict:
        with urllib.request.urlopen(self._req(path, payload), timeout=30) as response:
            return json.load(response)

    def stream(self, path: str, payload: object):
        return urllib.request.urlopen(self._req(path, payload), timeout=900)

    def wait_for_mcp(self, servers: set[str], timeout: float = 180) -> dict:
        deadline = time.time() + timeout
        listing: dict = {}
        while time.time() < deadline:
            listing = self.get("/v1/mcp/servers")
            states = {s["name"]: s.get("state") for s in listing.get("servers", [])}
            if all(states.get(name) == "connected" for name in servers):
                break
            time.sleep(1)
        return {s["name"]: {"state": s.get("state"), "tools": len(s.get("tools") or [])}
                for s in listing.get("servers", [])}

    def log_size(self) -> int:
        return self.log_path.stat().st_size if self.log_path.exists() else 0

    def log_since(self, offset: int) -> str:
        if not self.log_path.exists():
            return ""
        with self.log_path.open(errors="replace") as handle:
            handle.seek(offset)
            return handle.read()

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _gate_resolver(backend: Backend, thread: str, deny_mcp: bool, record: dict,
                   stop: threading.Event) -> None:
    """Answers gates as a user would while the turn streams."""
    seen: set[str] = set()
    while not stop.wait(0.4):
        try:
            live = backend.get(f"/v1/chat/threads/{thread}/live")
        except Exception:
            continue
        for gate in live.get("pending_gates") or []:
            gate_id, kind = gate.get("gate_id"), gate.get("kind")
            if gate_id in seen:
                continue
            seen.add(gate_id)
            payload = gate.get("payload") or {}
            entry = {"kind": kind, "tool": payload.get("tool"), "server": payload.get("server")}
            try:
                if kind == "mcp_tool":
                    approve = not deny_mcp
                    backend.post(f"/v1/chat/threads/{thread}/mcp-decision",
                                 {"approve": approve, "remember": False, "gate_id": gate_id})
                    entry["decision"] = "approve" if approve else "reject"
                elif kind == "command":
                    backend.post(f"/v1/chat/threads/{thread}/command-decision",
                                 {"approve": True, "remember": False, "gate_id": gate_id})
                    entry["decision"] = "approve"
                elif kind == "edit":
                    backend.post(f"/v1/chat/threads/{thread}/edit-decision",
                                 {"decision": "reject", "gate_id": gate_id})
                    entry["decision"] = "reject"
                else:
                    entry["decision"] = "left for main loop"
            except urllib.error.HTTPError as exc:
                entry["error"] = f"HTTP {exc.code}"
            record["gates"].append(entry)


def _consume(response, record: dict, start: float) -> None:
    for raw in response:
        if not raw.startswith(b"data: "):
            continue
        try:
            event = json.loads(raw[6:])
        except ValueError:
            continue
        kind, payload = event.get("type"), event.get("payload") or {}
        now = round(time.perf_counter() - start, 2)
        if kind == "tool_call" and isinstance(payload, dict):
            args = payload.get("args")
            call = {"t": now, "tool": payload.get("tool")}
            if payload.get("tool") == "load_capabilities":
                call["args"] = args
            elif isinstance(args, dict):
                call["args"] = {k: (v if len(json.dumps(v)) < 200 else "…")
                                for k, v in args.items()}
            record["tool_calls"].append(call)
        elif kind == "tool_result" and isinstance(payload, dict):
            if payload.get("is_error"):
                record["tool_errors"].append({"tool": payload.get("tool"),
                                              "output": str(payload.get("output", ""))[:300]})
        elif kind == "token_progress" and isinstance(payload, dict) and payload.get("exact"):
            if payload.get("input") is not None:
                record["request_inputs"].append(payload.get("input"))
        elif kind == "memory_compacted":
            record["compactions"].append(payload)
        elif kind == "chat_response":
            record["answer"] += str(payload.get("chunk", ""))
        if kind in TERMINAL_EVENTS:
            return


def _usage(backend: Backend, thread: str) -> dict:
    return backend.get(f"/v1/chat/threads/{thread}/usage")["main"]


def _wait_idle(backend: Backend, thread: str, timeout: float = 30) -> dict:
    deadline = time.time() + timeout
    live: dict = {}
    while time.time() < deadline:
        live = backend.get(f"/v1/chat/threads/{thread}/live")
        if not live.get("turn_active"):
            return live
        time.sleep(0.5)
    return live


def run_turn(backend: Backend, thread: str, prompt: str, deny_mcp: bool,
             turn_timeout: float) -> dict:
    record: dict = {"prompt": prompt, "tool_calls": [], "tool_errors": [], "gates": [],
                    "request_inputs": [], "compactions": [], "answer": "", "clarify": []}
    before = _usage(backend, thread)
    offset = backend.log_size()
    stop = threading.Event()
    resolver = threading.Thread(target=_gate_resolver,
                                args=(backend, thread, deny_mcp, record, stop), daemon=True)
    resolver.start()
    start = time.perf_counter()
    timer = threading.Timer(turn_timeout, lambda: backend.post(
        f"/v1/chat/threads/{thread}/stop", {}))
    timer.start()
    try:
        with backend.stream(f"/v1/chat/threads/{thread}/message",
                            {"content": prompt, "plan_mode": False, "step_review": False}) as r:
            _consume(r, record, start)
        for _ in range(2):  # a clarify gate pauses the turn; answer it like a user
            live = _wait_idle(backend, thread, 5)
            gates = [g for g in live.get("pending_gates") or [] if g.get("kind") == "clarify"]
            if not gates:
                break
            record["clarify"].append(gates[0].get("payload", {}).get("question"))
            with backend.stream(f"/v1/chat/threads/{thread}/clarify-decision",
                                {"answer": "Use your best judgment.",
                                 "gate_id": gates[0]["gate_id"]}) as r:
                _consume(r, record, start)
    except Exception as exc:  # recorded, never hidden
        record["client_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        timer.cancel()
        record["seconds"] = round(time.perf_counter() - start, 1)
        _wait_idle(backend, thread)
        stop.set()
        resolver.join(timeout=2)
    time.sleep(1.0)
    after = _usage(backend, thread)
    record["usage"] = {k: after[k] - before[k] for k in after}
    log = backend.log_since(offset)
    rejected = re.findall(r"\[controller\] (\S+) REJECTED .*", log)
    rejected_lines = re.findall(r"\[controller\] \S+ REJECTED .*", log)
    record["unloaded_refusals"] = sum("is not loaded" in line for line in rejected_lines)
    record["malformed_corrections"] = (
        len(rejected_lines) - record["unloaded_refusals"]
        + len(re.findall(r"create_controller_step raised", log)))
    record["rejected_types"] = rejected
    record["provider_retries"] = len(re.findall(r"provider unavailable|transient", log, re.I))
    record["overflow"] = len(re.findall(r"context window exceeded", log))
    if not record["answer"]:
        thread_view = backend.get(f"/v1/chat/threads/{thread}")
        agents = [m for m in thread_view.get("messages", []) if m.get("role") == "agent"]
        if agents:
            record["answer"] = str(agents[-1].get("content", ""))
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=sorted(MODELS), required=True)
    parser.add_argument("--flag", choices=["on", "off"], required=True)
    parser.add_argument("--suite", choices=sorted(SUITES), required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--window", help="Override CRUCIBLE_MEMORY_WINDOW_TOKENS")
    parser.add_argument("--only", help="Comma-separated scenario names")
    parser.add_argument("--turn-timeout", type=float, default=600)
    parser.add_argument("--repeat", type=int, default=1, help="Run each scenario N times")
    args = parser.parse_args()

    backend = Backend(args.model, args.flag, args.run_dir, args.workspace.resolve(), args.window)
    report: dict = {"model": args.model, "flag": args.flag, "suite": args.suite,
                    "window": args.window, "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                         time.gmtime()),
                    "scenarios": []}
    try:
        report["mcp"] = backend.wait_for_mcp({"playwright", "everything", "fetch"})
        report["config"] = {k: v for k, v in backend.get("/v1/config").items()
                            if k in ("provider", "mcp_enabled", "reasoning_effort")}
        only = set(args.only.split(",")) if args.only else None
        scenarios = [s for _ in range(args.repeat) for s in SUITES[args.suite]]
        for scenario in scenarios:
            if only and scenario["name"] not in only:
                continue
            thread = backend.post("/v1/chat/threads", {"workspace": str(args.workspace.resolve()),
                                                       "title": scenario["name"]})["thread_id"]
            entry = {"name": scenario["name"], "thread": thread, "turns": []}
            report["scenarios"].append(entry)
            for prompt in scenario["turns"]:
                print(f"[{args.model}/{args.flag}] {scenario['name']}: {prompt[:60]}", flush=True)
                turn = run_turn(backend, thread, prompt, scenario.get("deny_mcp", False),
                                args.turn_timeout)
                entry["turns"].append(turn)
                print(f"  -> {turn['seconds']}s usage={turn['usage']} tools="
                      f"{[c['tool'] for c in turn['tool_calls']]} answer={turn['answer'][:120]!r}",
                      flush=True)
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(report, indent=2) + "\n")
    finally:
        backend.close()
        args.out.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
