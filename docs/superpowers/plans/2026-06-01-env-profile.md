# Workspace-level Env Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a workspace-level env profile that lives at `<workspace>/.crucible/state/env_profile.json`, is built once via deterministic probe + one LLM `draft_conventions` call, is read by the agent through a new `read_env_profile` tool, and auto-reinstalls on mid-task manifest edits — with no env state machine.

**Architecture:** Profile is built lazily on first task per workspace; persisted as JSON; refreshed when stale (age > 30d) or via explicit API. `PatchEngine` sets `task.execution_state.pending_install_for_scope` when a manifest is written; `ToolLoop` consumes the flag to run the ecosystem's `install_command` before the next `run_command`. No new task status; no env SM.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest + pytest-asyncio. Aligns with the existing `agentd-py` patterns (tool-loop ReAct, ScriptedReasoningEngine for tests, ShadowWorkspaceManager).

**Spec:** [docs/superpowers/specs/2026-06-01-env-profile-design.md](../specs/2026-06-01-env-profile-design.md)

---

## Task 1: Add EnvProfile / EnvEcosystemEntry schemas and pending_install_for_scope field

**Files:**
- Modify: `services/agentd-py/agentd/domain/models.py` (insert after `TaskExecutionState` around line 209)
- Test: `services/agentd-py/tests/test_env_profile_models.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `services/agentd-py/tests/test_env_profile_models.py`:

```python
"""Pydantic model tests for EnvProfile / EnvEcosystemEntry."""
from datetime import datetime, timezone

from agentd.domain.models import (
    EnvEcosystemEntry,
    EnvProfile,
    TaskExecutionState,
)


def test_env_ecosystem_entry_scope_key_combines_ecosystem_and_subdir():
    entry = EnvEcosystemEntry(
        ecosystem="python",
        subdir="services/agentd-py",
        manifest_path="services/agentd-py/pyproject.toml",
        package_manager="uv",
        install_command="uv sync",
        interpreter_or_runner=".venv/bin/python",
        test_command="pytest",
        declared_dependencies_top=["fastapi>=0.116.0"],
        notes=None,
    )
    assert entry.scope_key == "python:services/agentd-py"


def test_env_ecosystem_entry_workspace_root_subdir_becomes_empty_scope():
    entry = EnvEcosystemEntry(
        ecosystem="node",
        subdir="",
        manifest_path="package.json",
        package_manager="npm",
        install_command="npm ci",
        interpreter_or_runner=None,
        test_command="npm test",
        declared_dependencies_top=[],
        notes=None,
    )
    assert entry.scope_key == "node:"


def test_env_profile_roundtrips_through_json():
    profile = EnvProfile(
        workspace_root="/tmp/ws",
        built_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        bootstrap_needed=False,
        ecosystems=[
            EnvEcosystemEntry(
                ecosystem="python",
                subdir="",
                manifest_path="pyproject.toml",
                package_manager="uv",
                install_command="uv sync",
                interpreter_or_runner=".venv/bin/python",
                test_command="pytest",
                declared_dependencies_top=["pydantic>=2"],
                notes=None,
            )
        ],
        conventions_notes="uses uv",
        diagnostics=[],
    )
    raw = profile.model_dump_json()
    round = EnvProfile.model_validate_json(raw)
    assert round.ecosystems[0].package_manager == "uv"
    assert round.bootstrap_needed is False


def test_task_execution_state_has_pending_install_default_none():
    state = TaskExecutionState()
    assert state.pending_install_for_scope is None


def test_task_execution_state_pending_install_accepts_string():
    state = TaskExecutionState(pending_install_for_scope="python:services/agentd-py")
    assert state.pending_install_for_scope == "python:services/agentd-py"
```

- [ ] **Step 2: Run test to verify it fails**

```
cd services/agentd-py && source .venv/bin/activate
pytest tests/test_env_profile_models.py -v
```

Expected: ImportError — `EnvProfile`, `EnvEcosystemEntry` not in `agentd.domain.models`.

- [ ] **Step 3: Add the schemas**

In `services/agentd-py/agentd/domain/models.py`, insert AFTER the `TaskExecutionState` class (line ~210) and update the existing `TaskExecutionState` to include the new field:

```python
class TaskExecutionState(BaseModel):
    current_step_id: str | None = None
    step_checkpoints: dict[str, str] = Field(default_factory=dict)
    delta_replan_requests: list[DeltaReplanRequest] = Field(default_factory=list)
    delta_replans_used: int = 0
    auto_approved_scope_files: list[str] = Field(default_factory=list)
    pending_scope_request: ScopeExtensionRequest | None = None
    pending_step_review: StepReviewPayload | None = None
    pending_command_request: CommandApprovalRequest | None = None
    approved_commands: list[CommandRule] = Field(default_factory=list)
    pending_install_for_scope: str | None = None   # NEW: ecosystem scope_key needing setup_env before next run_command
```

Then add the new classes:

```python
class EnvEcosystemEntry(BaseModel):
    """One ecosystem-scope in an EnvProfile.

    Identified by (ecosystem, subdir). The scope_key property is the
    deterministic key used by manifest-write auto-sync.
    """
    ecosystem: Literal["python", "node", "rust", "go"]
    subdir: str                              # relative to workspace; "" = root
    manifest_path: str                       # relative to workspace
    package_manager: str                     # "uv" | "pip" | "npm" | "yarn" | "pnpm" | "cargo" | "go"
    install_command: str                     # ready for setup_env (e.g. "uv sync")
    interpreter_or_runner: str | None        # rel path (e.g. ".venv/bin/python")
    test_command: str | None                 # rel cmd used with subdir as cwd (e.g. "pytest")
    declared_dependencies_top: list[str] = Field(default_factory=list)  # top ~20 manifest deps verbatim
    notes: str | None = None                 # LLM-supplied quirks

    @property
    def scope_key(self) -> str:
        return f"{self.ecosystem}:{self.subdir}"


class EnvProfile(BaseModel):
    """Workspace-level env conventions persisted at <workspace>/.crucible/state/env_profile.json."""
    workspace_root: str
    built_at: datetime
    bootstrap_needed: bool = False           # probe found nothing usable; agent falls back to find_binary/init_workspace
    ecosystems: list[EnvEcosystemEntry] = Field(default_factory=list)
    conventions_notes: str | None = None     # short free-form summary from the LLM
    diagnostics: list[str] = Field(default_factory=list)  # probe warnings
```

Add `Literal` and `datetime` to existing imports at the top of the file:

```python
from datetime import datetime
from typing import Literal
```

(Check if already present — `datetime` is typically already imported; `Literal` may need adding to the `typing` import.)

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_env_profile_models.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Run full test suite to confirm no regressions**

```
pytest -q
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/domain/models.py services/agentd-py/tests/test_env_profile_models.py
git commit -m "feat(env-profile): add EnvProfile/EnvEcosystemEntry schemas + pending_install_for_scope"
```

---

## Task 2: EcosystemProbe — deterministic file/manifest probe

**Files:**
- Create: `services/agentd-py/agentd/env/__init__.py`
- Create: `services/agentd-py/agentd/env/probe.py`
- Test: `services/agentd-py/tests/test_env_probe.py`

- [ ] **Step 1: Write the failing tests**

Create `services/agentd-py/tests/test_env_probe.py`:

```python
"""Tests for EcosystemProbe — pure filesystem scan, no LLM."""
import asyncio
from pathlib import Path

import pytest

from agentd.env.probe import EcosystemProbe, ProbeResult


@pytest.mark.asyncio
async def test_probe_finds_python_manifest_at_root(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = \"demo\"\nversion = \"0\"\ndependencies = [\"fastapi\"]\n"
    )
    result = await EcosystemProbe.scan(tmp_path)
    pythons = [e for e in result.ecosystems if e.ecosystem == "python"]
    assert len(pythons) == 1
    assert pythons[0].subdir == ""
    assert pythons[0].manifest_path == "pyproject.toml"
    assert "fastapi" in pythons[0].manifest_text


@pytest.mark.asyncio
async def test_probe_finds_python_in_monorepo_subdir(tmp_path: Path):
    sub = tmp_path / "services" / "agentd-py"
    sub.mkdir(parents=True)
    (sub / "pyproject.toml").write_text("[project]\nname = \"sub\"\nversion = \"0\"\n")
    result = await EcosystemProbe.scan(tmp_path)
    pythons = [e for e in result.ecosystems if e.ecosystem == "python"]
    assert pythons[0].subdir == "services/agentd-py"


@pytest.mark.asyncio
async def test_probe_finds_node_and_rust_in_same_workspace(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"name": "demo", "version": "1.0.0"}')
    (tmp_path / "Cargo.toml").write_text(
        "[package]\nname = \"demo\"\nversion = \"0.1.0\"\nedition = \"2021\"\n"
    )
    result = await EcosystemProbe.scan(tmp_path)
    ecos = sorted(e.ecosystem for e in result.ecosystems)
    assert ecos == ["node", "rust"]


@pytest.mark.asyncio
async def test_probe_bare_workspace_returns_empty(tmp_path: Path):
    result = await EcosystemProbe.scan(tmp_path)
    assert result.ecosystems == []
    assert result.workspace_tree  # tree still listed even when empty


@pytest.mark.asyncio
async def test_probe_records_lockfile_presence(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"name": "demo", "version": "1.0.0"}')
    (tmp_path / "package-lock.json").write_text('{}')
    result = await EcosystemProbe.scan(tmp_path)
    node = result.ecosystems[0]
    assert "package-lock.json" in node.lockfiles_present
    assert "yarn.lock" not in node.lockfiles_present


@pytest.mark.asyncio
async def test_probe_flags_setuptools_flat_layout_risk(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = \"demo\"\nversion = \"0\"\n"
        "[build-system]\nrequires = [\"setuptools>=68\"]\nbuild-backend = \"setuptools.build_meta\"\n"
    )
    (tmp_path / "agentd").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "workspaces").mkdir()
    result = await EcosystemProbe.scan(tmp_path)
    assert any("SETUPTOOLS_FLAT_LAYOUT_RISK" in d for d in result.diagnostics)


@pytest.mark.asyncio
async def test_probe_skips_node_modules_and_venv_dirs(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"name": "outer"}')
    (tmp_path / "node_modules" / "inner").mkdir(parents=True)
    (tmp_path / "node_modules" / "inner" / "package.json").write_text('{"name": "skip-me"}')
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "pyproject.toml").write_text("[project]\nname=\"x\"\nversion=\"0\"\n")
    result = await EcosystemProbe.scan(tmp_path)
    nodes = [e for e in result.ecosystems if e.ecosystem == "node"]
    pythons = [e for e in result.ecosystems if e.ecosystem == "python"]
    assert len(nodes) == 1  # outer only
    assert pythons == []    # .venv excluded
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_env_probe.py -v
```

Expected: ImportError on `agentd.env.probe`.

- [ ] **Step 3: Create the env package and probe module**

Create `services/agentd-py/agentd/env/__init__.py` (empty):

```python
"""Workspace-level env profile: probe, build, store, read."""
```

Create `services/agentd-py/agentd/env/probe.py`:

```python
"""Deterministic ecosystem probe — pure filesystem, no LLM, no decisions."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path


# Manifest filename → ecosystem
_MANIFEST_TO_ECOSYSTEM: dict[str, str] = {
    "pyproject.toml": "python",
    "package.json": "node",
    "Cargo.toml": "rust",
    "go.mod": "go",
}

# Per-ecosystem lockfiles to record presence of
_LOCKFILES_BY_ECOSYSTEM: dict[str, tuple[str, ...]] = {
    "python": ("uv.lock", "poetry.lock", "requirements.txt", "Pipfile.lock"),
    "node": ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"),
    "rust": ("Cargo.lock",),
    "go": ("go.sum",),
}

# Directory basenames to NEVER descend into when walking the workspace.
_EXCLUDE_DIRS = frozenset({
    ".git", ".venv", "venv", ".env", "node_modules",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "target", "dist", "build", ".tox", ".nox",
    ".crucible/state", ".crucible", ".worktrees", ".tmp",
})

# Walk no deeper than this many directory levels under workspace_root.
_MAX_DEPTH = 3

# Cap manifest file size to read in full (bytes). Avoids reading e.g. 10MB
# package.json by accident; LLM payload stays bounded.
_MAX_MANIFEST_BYTES = 64 * 1024


@dataclass
class EcosystemFacts:
    ecosystem: str            # "python" | "node" | "rust" | "go"
    subdir: str               # rel to workspace_root; "" = root
    manifest_path: str        # rel to workspace_root
    manifest_text: str        # full text up to _MAX_MANIFEST_BYTES
    top_level_dirs: list[str] # directories adjacent to the manifest (1 level)
    lockfiles_present: list[str]


@dataclass
class ProbeResult:
    workspace_root: str
    ecosystems: list[EcosystemFacts] = field(default_factory=list)
    workspace_tree: list[str] = field(default_factory=list)  # rel paths, capped
    package_managers_on_path: dict[str, str] = field(default_factory=dict)
    language_runtimes_on_path: dict[str, str] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


class EcosystemProbe:
    """Deterministic workspace probe. No LLM. Returns facts only."""

    @classmethod
    async def scan(cls, workspace_root: Path) -> ProbeResult:
        workspace_root = workspace_root.resolve()
        result = ProbeResult(workspace_root=str(workspace_root))

        # 1. Walk and find manifests (bounded depth, exclusions applied).
        manifests = cls._walk_manifests(workspace_root)
        for manifest_abs in manifests:
            ecosystem = _MANIFEST_TO_ECOSYSTEM[manifest_abs.name]
            rel_manifest = str(manifest_abs.relative_to(workspace_root))
            rel_subdir = str(manifest_abs.parent.relative_to(workspace_root))
            if rel_subdir == ".":
                rel_subdir = ""
            try:
                text = manifest_abs.read_text(errors="replace")[:_MAX_MANIFEST_BYTES]
            except OSError as exc:
                result.diagnostics.append(
                    f"MANIFEST_READ_FAILED:{rel_manifest}:{exc}"
                )
                continue

            top_dirs = [
                p.name for p in sorted(manifest_abs.parent.iterdir())
                if p.is_dir() and p.name not in _EXCLUDE_DIRS
            ]
            locks = [
                lf for lf in _LOCKFILES_BY_ECOSYSTEM.get(ecosystem, ())
                if (manifest_abs.parent / lf).exists()
            ]

            result.ecosystems.append(EcosystemFacts(
                ecosystem=ecosystem,
                subdir=rel_subdir,
                manifest_path=rel_manifest,
                manifest_text=text,
                top_level_dirs=top_dirs,
                lockfiles_present=locks,
            ))

            cls._diagnose(ecosystem, text, top_dirs, rel_manifest, result.diagnostics)

        # 2. Workspace tree (3 levels), capped to ~80 entries.
        result.workspace_tree = cls._workspace_tree(workspace_root, cap=80)

        # 3. PMs and runtimes on PATH (best-effort, silent on miss).
        result.package_managers_on_path = await cls._which_many(
            ["uv", "pip", "pip3", "npm", "yarn", "pnpm", "cargo", "go", "poetry", "rustup"]
        )
        result.language_runtimes_on_path = await cls._which_many(
            ["python3", "python", "node", "rustc", "go"]
        )

        return result

    @classmethod
    def _walk_manifests(cls, root: Path) -> list[Path]:
        manifests: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            depth = Path(dirpath).relative_to(root).parts
            if len(depth) > _MAX_DEPTH:
                dirnames.clear()
                continue
            # Prune excluded dirs in-place so os.walk skips them.
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
            for name in filenames:
                if name in _MANIFEST_TO_ECOSYSTEM:
                    manifests.append(Path(dirpath) / name)
        return manifests

    @classmethod
    def _workspace_tree(cls, root: Path, *, cap: int) -> list[str]:
        entries: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            depth = Path(dirpath).relative_to(root).parts
            if len(depth) > _MAX_DEPTH:
                dirnames.clear()
                continue
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
            rel = Path(dirpath).relative_to(root)
            for d in dirnames:
                entries.append(str(rel / d))
                if len(entries) >= cap:
                    return entries
        return entries

    @classmethod
    async def _which_many(cls, names: list[str]) -> dict[str, str]:
        async def one(name: str) -> tuple[str, str | None]:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "which", name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
                if proc.returncode == 0:
                    return name, stdout.decode().strip()
            except (TimeoutError, FileNotFoundError):
                pass
            return name, None

        results = await asyncio.gather(*[one(n) for n in names])
        return {n: p for n, p in results if p}

    @classmethod
    def _diagnose(
        cls,
        ecosystem: str,
        text: str,
        top_dirs: list[str],
        rel_manifest: str,
        diagnostics: list[str],
    ) -> None:
        if ecosystem == "python":
            # setuptools flat-layout risk: build-backend=setuptools, multiple top-level
            # dirs, no [tool.setuptools.packages.find].
            if (
                "build-backend = \"setuptools.build_meta\"" in text
                and "[tool.setuptools.packages.find]" not in text
                and len([d for d in top_dirs if not d.startswith(".")]) >= 2
            ):
                diagnostics.append(
                    f"SETUPTOOLS_FLAT_LAYOUT_RISK:{rel_manifest}:"
                    f"multiple top-level dirs {top_dirs} and no packages.find stanza"
                )
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_env_probe.py -v
```

Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/env/__init__.py services/agentd-py/agentd/env/probe.py services/agentd-py/tests/test_env_probe.py
git commit -m "feat(env-profile): EcosystemProbe deterministic workspace scan"
```

---

## Task 3: EnvProfileStore — JSON read/write/staleness

**Files:**
- Create: `services/agentd-py/agentd/env/profile_store.py`
- Test: `services/agentd-py/tests/test_env_profile_store.py`

- [ ] **Step 1: Write the failing tests**

Create `services/agentd-py/tests/test_env_profile_store.py`:

```python
"""Tests for EnvProfileStore — JSON read/write/staleness."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentd.domain.models import EnvProfile, EnvEcosystemEntry
from agentd.env.profile_store import EnvProfileStore


def _make_profile(ws: Path, built_at: datetime | None = None) -> EnvProfile:
    return EnvProfile(
        workspace_root=str(ws),
        built_at=built_at or datetime.now(timezone.utc),
        bootstrap_needed=False,
        ecosystems=[EnvEcosystemEntry(
            ecosystem="python", subdir="", manifest_path="pyproject.toml",
            package_manager="uv", install_command="uv sync",
            interpreter_or_runner=".venv/bin/python", test_command="pytest",
            declared_dependencies_top=["pydantic>=2"], notes=None,
        )],
        conventions_notes=None, diagnostics=[],
    )


def test_read_missing_returns_none(tmp_path: Path):
    store = EnvProfileStore()
    assert store.read(tmp_path) is None


def test_write_then_read_roundtrip(tmp_path: Path):
    store = EnvProfileStore()
    p = _make_profile(tmp_path)
    store.write(tmp_path, p)
    got = store.read(tmp_path)
    assert got is not None
    assert got.ecosystems[0].package_manager == "uv"


def test_write_creates_agentd_dir_if_missing(tmp_path: Path):
    store = EnvProfileStore()
    store.write(tmp_path, _make_profile(tmp_path))
    assert (tmp_path / ".crucible/state" / "env_profile.json").is_file()


def test_is_stale_returns_true_when_missing(tmp_path: Path):
    store = EnvProfileStore()
    assert store.is_stale(tmp_path) is True


def test_is_stale_returns_false_for_fresh_profile(tmp_path: Path):
    store = EnvProfileStore()
    store.write(tmp_path, _make_profile(tmp_path))
    assert store.is_stale(tmp_path) is False


def test_is_stale_returns_true_for_old_profile(tmp_path: Path):
    store = EnvProfileStore(max_age_days=30)
    old = datetime.now(timezone.utc) - timedelta(days=31)
    store.write(tmp_path, _make_profile(tmp_path, built_at=old))
    # Also backdate file mtime so the disk-check matches.
    pth = tmp_path / ".crucible/state" / "env_profile.json"
    os.utime(pth, (old.timestamp(), old.timestamp()))
    assert store.is_stale(tmp_path) is True


def test_read_returns_none_on_corrupted_json(tmp_path: Path):
    (tmp_path / ".crucible/state").mkdir()
    (tmp_path / ".crucible/state" / "env_profile.json").write_text("{not valid json")
    store = EnvProfileStore()
    assert store.read(tmp_path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_env_profile_store.py -v
```

Expected: ImportError on `agentd.env.profile_store`.

- [ ] **Step 3: Implement the store**

Create `services/agentd-py/agentd/env/profile_store.py`:

```python
"""Persist EnvProfile as JSON at <workspace>/.crucible/state/env_profile.json."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentd.domain.models import EnvProfile

_PROFILE_REL_PATH = Path(".crucible/state") / "env_profile.json"


class EnvProfileStore:
    """Read/write EnvProfile + age-based staleness check."""

    def __init__(self, *, max_age_days: int = 30) -> None:
        self._max_age = timedelta(days=max_age_days)

    @staticmethod
    def path_for(workspace: Path) -> Path:
        return workspace / _PROFILE_REL_PATH

    def read(self, workspace: Path) -> EnvProfile | None:
        p = self.path_for(workspace)
        if not p.is_file():
            return None
        try:
            return EnvProfile.model_validate_json(p.read_text())
        except (json.JSONDecodeError, ValueError):
            return None

    def write(self, workspace: Path, profile: EnvProfile) -> None:
        p = self.path_for(workspace)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(profile.model_dump_json(indent=2))

    def is_stale(self, workspace: Path) -> bool:
        p = self.path_for(workspace)
        if not p.is_file():
            return True
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        return (datetime.now(timezone.utc) - mtime) > self._max_age
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_env_profile_store.py -v
```

Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/env/profile_store.py services/agentd-py/tests/test_env_profile_store.py
git commit -m "feat(env-profile): EnvProfileStore JSON persistence + staleness"
```

---

## Task 4: draft_conventions reasoning contract + scripted engine + prompts

**Files:**
- Modify: `services/agentd-py/agentd/reasoning/contracts.py`
- Modify: `services/agentd-py/agentd/reasoning/engine.py`
- Modify: `services/agentd-py/agentd/orchestrator/scripted_engine.py`
- Create: `services/agentd-py/agentd/reasoning/env_prompts.py`
- Test: `services/agentd-py/tests/test_draft_conventions_contract.py`

- [ ] **Step 1: Write the failing tests**

Create `services/agentd-py/tests/test_draft_conventions_contract.py`:

```python
"""Test the scripted engine's draft_conventions and prompt builder."""
import pytest

from agentd.env.probe import EcosystemFacts, ProbeResult
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
from agentd.reasoning.env_prompts import (
    DRAFT_CONVENTIONS_SYSTEM_PROMPT,
    DRAFT_CONVENTIONS_RESPONSE_SCHEMA,
    build_draft_conventions_payload,
)


def _probe_with_python(workspace: str = "/tmp/ws") -> ProbeResult:
    return ProbeResult(
        workspace_root=workspace,
        ecosystems=[EcosystemFacts(
            ecosystem="python",
            subdir="",
            manifest_path="pyproject.toml",
            manifest_text="[project]\nname=\"demo\"\nversion=\"0\"\ndependencies=[\"fastapi\"]\n",
            top_level_dirs=["agentd"],
            lockfiles_present=["uv.lock"],
        )],
        workspace_tree=["agentd", "tests"],
        package_managers_on_path={"uv": "/usr/local/bin/uv"},
        language_runtimes_on_path={"python3": "/usr/bin/python3"},
        diagnostics=[],
    )


def test_draft_conventions_payload_includes_manifest_text():
    probe = _probe_with_python()
    payload = build_draft_conventions_payload(probe)
    s = str(payload)
    assert "[project]" in s
    assert "fastapi" in s
    assert "uv.lock" in s


def test_draft_conventions_response_schema_has_required_fields():
    schema = DRAFT_CONVENTIONS_RESPONSE_SCHEMA
    assert schema["type"] == "object"
    props = schema["properties"]
    assert "ecosystems" in props
    assert "conventions_notes" in props
    eco_props = props["ecosystems"]["items"]["properties"]
    for f in (
        "ecosystem", "subdir", "manifest_path", "package_manager",
        "install_command", "interpreter_or_runner", "test_command",
        "declared_dependencies_top", "notes",
    ):
        assert f in eco_props, f"missing {f} in entry schema"


@pytest.mark.asyncio
async def test_scripted_engine_draft_conventions_returns_canned_response():
    canned = {
        "ecosystems": [{
            "ecosystem": "python", "subdir": "", "manifest_path": "pyproject.toml",
            "package_manager": "uv", "install_command": "uv sync",
            "interpreter_or_runner": ".venv/bin/python", "test_command": "pytest",
            "declared_dependencies_top": ["fastapi"], "notes": None,
        }],
        "conventions_notes": "uses uv",
    }
    engine = ScriptedReasoningEngine(
        plans=[], patches=[], tool_steps=[], planning_steps=[],
        draft_conventions_responses=[canned],
    )
    out = await engine.draft_conventions(probe=_probe_with_python())
    assert out["ecosystems"][0]["package_manager"] == "uv"
    assert out["conventions_notes"] == "uses uv"


@pytest.mark.asyncio
async def test_scripted_engine_raises_when_no_canned_response():
    engine = ScriptedReasoningEngine(
        plans=[], patches=[], tool_steps=[], planning_steps=[],
        draft_conventions_responses=[],
    )
    with pytest.raises(RuntimeError, match="no draft_conventions response"):
        await engine.draft_conventions(probe=_probe_with_python())
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_draft_conventions_contract.py -v
```

Expected: ImportError on `agentd.reasoning.env_prompts` + `draft_conventions_responses` kwarg unknown.

- [ ] **Step 3: Create `agentd/reasoning/env_prompts.py`**

```python
"""Prompts + JSON schema for the single draft_conventions LLM call."""
from __future__ import annotations

from agentd.env.probe import ProbeResult

DRAFT_CONVENTIONS_SYSTEM_PROMPT = """\
You are a build-system expert. The user gives you a deterministic probe of a
software workspace: discovered manifests, lockfiles, top-level dirs, and which
package managers / language runtimes are on PATH.

Decide for each ecosystem-scope:
- which package manager to use (uv vs pip; npm vs yarn vs pnpm; cargo; go)
- the exact install command (e.g. "uv sync", "npm ci")
- the project's interpreter or binary-runner path RELATIVE to the workspace
  root (e.g. "services/agentd-py/.venv/bin/python"); null if not applicable
- the test command (e.g. "pytest", "vitest run", "cargo test"); null if you
  cannot infer one
- the top ~20 declared dependencies (verbatim strings from the manifest)
- short notes about quirks for this scope

Prefer the manifest's evidence over PATH presence. Be concrete; avoid
hedging. If a scope has no clear PM (e.g. no lockfile and ambiguous
manifest), still pick one and explain in `notes`.

Output STRICTLY conforming to the response schema.
"""

DRAFT_CONVENTIONS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "ecosystems": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ecosystem": {"type": "string", "enum": ["python", "node", "rust", "go"]},
                    "subdir": {"type": "string"},
                    "manifest_path": {"type": "string"},
                    "package_manager": {"type": "string"},
                    "install_command": {"type": "string"},
                    "interpreter_or_runner": {"type": ["string", "null"]},
                    "test_command": {"type": ["string", "null"]},
                    "declared_dependencies_top": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "notes": {"type": ["string", "null"]},
                },
                "required": [
                    "ecosystem", "subdir", "manifest_path", "package_manager",
                    "install_command", "interpreter_or_runner", "test_command",
                    "declared_dependencies_top", "notes",
                ],
            },
        },
        "conventions_notes": {"type": ["string", "null"]},
    },
    "required": ["ecosystems", "conventions_notes"],
}


def build_draft_conventions_payload(probe: ProbeResult) -> dict:
    """Build the user payload for the LLM. Include rich context per project rule:
    raw manifest text, lockfiles, top-level dirs, runtimes/PMs on PATH, workspace tree."""
    return {
        "workspace_root": probe.workspace_root,
        "workspace_tree": probe.workspace_tree,
        "package_managers_on_path": probe.package_managers_on_path,
        "language_runtimes_on_path": probe.language_runtimes_on_path,
        "diagnostics": probe.diagnostics,
        "ecosystems": [
            {
                "ecosystem": e.ecosystem,
                "subdir": e.subdir,
                "manifest_path": e.manifest_path,
                "manifest_text": e.manifest_text,
                "top_level_dirs": e.top_level_dirs,
                "lockfiles_present": e.lockfiles_present,
            }
            for e in probe.ecosystems
        ],
    }
```

- [ ] **Step 4: Add `draft_conventions` to the ReasoningEngine protocol**

In `services/agentd-py/agentd/reasoning/contracts.py`, add to the `ReasoningEngine` Protocol class:

```python
    async def draft_conventions(self, *, probe: "ProbeResult") -> dict:
        """Single structured LLM call that returns the env_profile body
        (ecosystems + conventions_notes)."""
        ...
```

Add the import at the top of the file (guarded if it creates a circular):

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from agentd.env.probe import ProbeResult
```

- [ ] **Step 5: Implement `draft_conventions` in `ReasoningEngineImpl`**

In `services/agentd-py/agentd/reasoning/engine.py`, add the method to `ReasoningEngineImpl`:

```python
async def draft_conventions(self, *, probe):
    from agentd.reasoning.env_prompts import (
        DRAFT_CONVENTIONS_RESPONSE_SCHEMA,
        DRAFT_CONVENTIONS_SYSTEM_PROMPT,
        build_draft_conventions_payload,
    )
    payload = build_draft_conventions_payload(probe)
    raw = await self._transport.generate_json(
        system=DRAFT_CONVENTIONS_SYSTEM_PROMPT,
        user=payload,
        schema=DRAFT_CONVENTIONS_RESPONSE_SCHEMA,
    )
    return raw
```

(Adjust transport call name to match the existing provider abstraction — look at how `create_planning_step` calls the transport in the same file and use the same method name.)

- [ ] **Step 6: Add `draft_conventions` to `ScriptedReasoningEngine`**

In `services/agentd-py/agentd/orchestrator/scripted_engine.py`, update `__init__` and add the method:

```python
def __init__(
    self,
    plans=None,
    patches=None,
    tool_steps=None,
    planning_steps=None,
    draft_conventions_responses=None,  # NEW
):
    self._plans = list(plans or [])
    self._patches = list(patches or [])
    self._tool_steps = list(tool_steps or [])
    self._planning_steps = list(planning_steps or [])
    self._draft_conventions = list(draft_conventions_responses or [])  # NEW
```

Add the method:

```python
async def draft_conventions(self, *, probe) -> dict:
    if not self._draft_conventions:
        raise RuntimeError("no draft_conventions response scripted")
    return self._draft_conventions.pop(0)
```

- [ ] **Step 7: Run tests**

```
pytest tests/test_draft_conventions_contract.py -v
```

Expected: 4 PASS.

- [ ] **Step 8: Run full suite to confirm no regressions**

```
pytest -q
```

Expected: all previous tests still pass (the new method on `ReasoningEngineImpl` is not called yet; the new `ScriptedReasoningEngine` kwarg is optional).

- [ ] **Step 9: Commit**

```bash
git add services/agentd-py/agentd/reasoning/contracts.py services/agentd-py/agentd/reasoning/engine.py services/agentd-py/agentd/reasoning/env_prompts.py services/agentd-py/agentd/orchestrator/scripted_engine.py services/agentd-py/tests/test_draft_conventions_contract.py
git commit -m "feat(env-profile): draft_conventions reasoning contract + scripted impl + prompts"
```

---

## Task 5: EnvProfileBuilder — composes probe + LLM call

**Files:**
- Create: `services/agentd-py/agentd/env/profile_builder.py`
- Test: `services/agentd-py/tests/test_env_profile_builder.py`

- [ ] **Step 1: Write the failing tests**

Create `services/agentd-py/tests/test_env_profile_builder.py`:

```python
"""Tests for EnvProfileBuilder — composes probe + draft_conventions."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentd.env.profile_builder import EnvProfileBuilder
from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine


@pytest.mark.asyncio
async def test_build_returns_profile_from_probe_and_llm(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname=\"demo\"\nversion=\"0\"\ndependencies=[\"fastapi\"]\n"
    )
    canned = {
        "ecosystems": [{
            "ecosystem": "python", "subdir": "", "manifest_path": "pyproject.toml",
            "package_manager": "uv", "install_command": "uv sync",
            "interpreter_or_runner": ".venv/bin/python", "test_command": "pytest",
            "declared_dependencies_top": ["fastapi"], "notes": None,
        }],
        "conventions_notes": "uses uv",
    }
    engine = ScriptedReasoningEngine(draft_conventions_responses=[canned])
    builder = EnvProfileBuilder(reasoner=engine)
    profile = await builder.build(tmp_path)

    assert profile.workspace_root == str(tmp_path.resolve())
    assert profile.bootstrap_needed is False
    assert len(profile.ecosystems) == 1
    assert profile.ecosystems[0].install_command == "uv sync"
    assert profile.conventions_notes == "uses uv"
    assert isinstance(profile.built_at, datetime)


@pytest.mark.asyncio
async def test_build_on_bare_workspace_sets_bootstrap_needed(tmp_path: Path):
    engine = ScriptedReasoningEngine(draft_conventions_responses=[])
    builder = EnvProfileBuilder(reasoner=engine)
    profile = await builder.build(tmp_path)

    assert profile.bootstrap_needed is True
    assert profile.ecosystems == []
    assert "no manifests" in (" ".join(profile.diagnostics)).lower()


@pytest.mark.asyncio
async def test_build_passes_diagnostics_through_to_profile(tmp_path: Path):
    # Trigger the SETUPTOOLS_FLAT_LAYOUT_RISK diagnostic via the probe.
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname=\"demo\"\nversion=\"0\"\n"
        "[build-system]\nrequires=[\"setuptools>=68\"]\n"
        "build-backend=\"setuptools.build_meta\"\n"
    )
    (tmp_path / "agentd").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "workspaces").mkdir()
    canned = {
        "ecosystems": [{
            "ecosystem": "python", "subdir": "", "manifest_path": "pyproject.toml",
            "package_manager": "uv", "install_command": "uv sync",
            "interpreter_or_runner": ".venv/bin/python", "test_command": "pytest",
            "declared_dependencies_top": [], "notes": "flat-layout — added find stanza",
        }],
        "conventions_notes": None,
    }
    engine = ScriptedReasoningEngine(draft_conventions_responses=[canned])
    builder = EnvProfileBuilder(reasoner=engine)
    profile = await builder.build(tmp_path)

    assert any("SETUPTOOLS_FLAT_LAYOUT_RISK" in d for d in profile.diagnostics)


@pytest.mark.asyncio
async def test_build_retries_once_on_llm_error_then_marks_bootstrap_needed(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname=\"x\"\nversion=\"0\"\n")

    class BrokenEngine:
        calls = 0
        async def draft_conventions(self, *, probe):
            BrokenEngine.calls += 1
            raise RuntimeError("boom")

    builder = EnvProfileBuilder(reasoner=BrokenEngine())
    profile = await builder.build(tmp_path)
    assert BrokenEngine.calls == 2     # one retry
    assert profile.bootstrap_needed is True
    assert any("convention drafting failed" in d for d in profile.diagnostics)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_env_profile_builder.py -v
```

Expected: ImportError on `agentd.env.profile_builder`.

- [ ] **Step 3: Implement the builder**

Create `services/agentd-py/agentd/env/profile_builder.py`:

```python
"""Composes EcosystemProbe + draft_conventions LLM call → EnvProfile."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from agentd.domain.models import EnvEcosystemEntry, EnvProfile
from agentd.env.probe import EcosystemProbe, ProbeResult


class _Reasoner(Protocol):
    async def draft_conventions(self, *, probe: ProbeResult) -> dict: ...


class EnvProfileBuilder:
    """Build an EnvProfile via deterministic probe + one LLM call.

    Failure mode: any unrecoverable error in the LLM call yields a
    `bootstrap_needed=True` profile with a diagnostic; the caller (orchestrator)
    still persists it so the agent uses find_binary/init_workspace going forward.
    """

    def __init__(self, *, reasoner: _Reasoner) -> None:
        self._reasoner = reasoner

    async def build(self, workspace_root: Path) -> EnvProfile:
        probe = await EcosystemProbe.scan(workspace_root)

        now = datetime.now(timezone.utc)

        # No manifests → no LLM call.
        if not probe.ecosystems:
            return EnvProfile(
                workspace_root=probe.workspace_root,
                built_at=now,
                bootstrap_needed=True,
                ecosystems=[],
                conventions_notes=None,
                diagnostics=[*probe.diagnostics, "no manifests found in workspace"],
            )

        # LLM call: try once + one retry on any exception.
        last_err: Exception | None = None
        decision: dict | None = None
        for attempt in range(2):
            try:
                decision = await self._reasoner.draft_conventions(probe=probe)
                break
            except Exception as exc:  # noqa: BLE001 — we surface the message below
                last_err = exc

        if decision is None:
            return EnvProfile(
                workspace_root=probe.workspace_root,
                built_at=now,
                bootstrap_needed=True,
                ecosystems=[],
                conventions_notes=None,
                diagnostics=[
                    *probe.diagnostics,
                    f"convention drafting failed: {last_err}",
                ],
            )

        entries = [EnvEcosystemEntry(**e) for e in decision.get("ecosystems", [])]
        return EnvProfile(
            workspace_root=probe.workspace_root,
            built_at=now,
            bootstrap_needed=False,
            ecosystems=entries,
            conventions_notes=decision.get("conventions_notes"),
            diagnostics=list(probe.diagnostics),
        )
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_env_profile_builder.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agentd-py/agentd/env/profile_builder.py services/agentd-py/tests/test_env_profile_builder.py
git commit -m "feat(env-profile): EnvProfileBuilder composes probe + draft_conventions"
```

---

## Task 6: read_env_profile tool + registry + teaching block

**Files:**
- Create: `services/agentd-py/agentd/tools/env_profile.py`
- Modify: `services/agentd-py/agentd/tools/registry.py`
- Modify: `services/agentd-py/agentd/reasoning/tool_prompts.py`
- Test: `services/agentd-py/tests/test_env_profile_tool.py`

- [ ] **Step 1: Write the failing tests**

Create `services/agentd-py/tests/test_env_profile_tool.py`:

```python
"""Tests for the read_env_profile tool + registry exposure."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentd.domain.models import EnvEcosystemEntry, EnvProfile
from agentd.env.profile_store import EnvProfileStore
from agentd.tools.registry import ToolRegistry


def _write_profile(workspace: Path) -> EnvProfile:
    profile = EnvProfile(
        workspace_root=str(workspace),
        built_at=datetime.now(timezone.utc),
        bootstrap_needed=False,
        ecosystems=[EnvEcosystemEntry(
            ecosystem="python", subdir="", manifest_path="pyproject.toml",
            package_manager="uv", install_command="uv sync",
            interpreter_or_runner=".venv/bin/python", test_command="pytest",
            declared_dependencies_top=["pydantic>=2"], notes=None,
        )],
        conventions_notes="uses uv", diagnostics=[],
    )
    EnvProfileStore().write(workspace, profile)
    return profile


@pytest.mark.asyncio
async def test_read_env_profile_returns_json_when_present(tmp_path: Path):
    _write_profile(tmp_path)
    reg = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    out = await reg.execute("read_env_profile", {})
    assert not out.is_error
    parsed = json.loads(out.output)
    assert parsed["ecosystems"][0]["package_manager"] == "uv"


@pytest.mark.asyncio
async def test_read_env_profile_returns_friendly_message_when_absent(tmp_path: Path):
    reg = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    out = await reg.execute("read_env_profile", {})
    assert not out.is_error
    assert "not yet built" in out.output


def test_read_env_profile_in_explore_definitions(tmp_path: Path):
    reg = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    names = {d.name for d in reg.definitions(phase="explore")}
    assert "read_env_profile" in names


def test_read_env_profile_in_verify_definitions(tmp_path: Path):
    reg = ToolRegistry(shadow_root=tmp_path, real_workspace_path=tmp_path)
    names = {d.name for d in reg.definitions(phase="verify")}
    assert "read_env_profile" in names
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_env_profile_tool.py -v
```

Expected: failures — tool not registered, no module to dispatch to.

- [ ] **Step 3: Create the tool wrapper**

Create `services/agentd-py/agentd/tools/env_profile.py`:

```python
"""read_env_profile tool — exposes the workspace EnvProfile to the agent."""
from __future__ import annotations

from pathlib import Path

from agentd.env.profile_store import EnvProfileStore
from agentd.tools.registry import ToolOutput


async def read_env_profile(*, real_workspace: Path) -> ToolOutput:
    profile = EnvProfileStore().read(real_workspace)
    if profile is None:
        return ToolOutput(
            output="profile not yet built; proceed without it",
            is_error=False,
        )
    return ToolOutput(output=profile.model_dump_json(indent=2), is_error=False)
```

- [ ] **Step 4: Register the tool in `tools/registry.py`**

In `services/agentd-py/agentd/tools/registry.py`, add the tool definition in `definitions()` for BOTH `explore` and `verify` phases (it's helpful in both). Add to the base `tools` list (shared across phases) inside `definitions()`:

```python
ToolDefinition(
    name="read_env_profile",
    description=(
        "Return the workspace's env profile (JSON). Tells you the package "
        "manager, install command, interpreter path, and test command per "
        "ecosystem. Always call this before guessing python/node/cargo "
        "commands. The 'interpreter_or_runner' field is the binary to call "
        "directly — don't try to activate a venv (it won't persist across "
        "tool calls)."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
),
```

In the `execute()` dispatch (before the `if name == "search_semantic":` block, anywhere is fine — pick after the existing `init_workspace` branch in `verify`-only tools):

```python
if name == "read_env_profile":
    from agentd.tools.env_profile import read_env_profile
    return await read_env_profile(real_workspace=self._real_workspace_path)
```

- [ ] **Step 5: Add the teaching block to `tool_prompts.py`**

In `services/agentd-py/agentd/reasoning/tool_prompts.py`, find the `BINARY DISCOVERY` section in `TOOL_LOOP_SYSTEM_PROMPT` and add an `ENV PROFILE` block right before it:

```
ENV PROFILE (consult this BEFORE guessing interpreter/test/install commands):
  1. Call read_env_profile.
  2. Use entries[i].interpreter_or_runner directly as the command path
     (e.g. "services/agentd-py/.venv/bin/python"). Do NOT try to source
     activate — tool calls do not persist shell state.
  3. Use entries[i].test_command verbatim with entries[i].subdir as cwd.
  4. If the profile is bootstrap_needed=true or 'not yet built', fall back
     to find_binary / setup_env / init_workspace as before.

```

- [ ] **Step 6: Run tests**

```
pytest tests/test_env_profile_tool.py -v
```

Expected: 4 PASS.

- [ ] **Step 7: Run full suite to confirm no regressions**

```
pytest -q
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add services/agentd-py/agentd/tools/env_profile.py services/agentd-py/agentd/tools/registry.py services/agentd-py/agentd/reasoning/tool_prompts.py services/agentd-py/tests/test_env_profile_tool.py
git commit -m "feat(env-profile): read_env_profile tool + registry + teaching block"
```

---

## Task 7: Orchestrator `_ensure_env_profile` hook + workspace asyncio lock

**Files:**
- Modify: `services/agentd-py/agentd/orchestrator/engine.py`
- Test: `services/agentd-py/tests/test_orchestrator_env_ensure.py`

- [ ] **Step 1: Write the failing tests**

Create `services/agentd-py/tests/test_orchestrator_env_ensure.py`:

```python
"""Tests for AgentOrchestrator._ensure_env_profile hook."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentd.domain.models import EnvEcosystemEntry, EnvProfile
from agentd.env.profile_store import EnvProfileStore


@pytest.mark.asyncio
async def test_ensure_builds_profile_when_missing(tmp_path: Path):
    # Test via the orchestrator's _ensure_env_profile method directly to avoid
    # spinning up the full task pipeline.
    from agentd.orchestrator.engine import AgentOrchestrator
    from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
    from agentd.storage.in_memory import InMemoryTaskStore
    from agentd.workspace.shadow import ShadowWorkspaceManager

    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname=\"x\"\nversion=\"0\"\ndependencies=[\"fastapi\"]\n"
    )
    canned = {
        "ecosystems": [{
            "ecosystem": "python", "subdir": "", "manifest_path": "pyproject.toml",
            "package_manager": "uv", "install_command": "uv sync",
            "interpreter_or_runner": ".venv/bin/python", "test_command": "pytest",
            "declared_dependencies_top": ["fastapi"], "notes": None,
        }],
        "conventions_notes": None,
    }
    reasoner = ScriptedReasoningEngine(draft_conventions_responses=[canned])
    orch = AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoner=reasoner,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )
    await orch._ensure_env_profile(tmp_path)
    assert EnvProfileStore().read(tmp_path) is not None


@pytest.mark.asyncio
async def test_ensure_reuses_fresh_profile_skips_llm(tmp_path: Path):
    """A fresh profile must not trigger another draft_conventions call."""
    from agentd.orchestrator.engine import AgentOrchestrator
    from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
    from agentd.storage.in_memory import InMemoryTaskStore
    from agentd.workspace.shadow import ShadowWorkspaceManager

    EnvProfileStore().write(tmp_path, EnvProfile(
        workspace_root=str(tmp_path),
        built_at=datetime.now(timezone.utc),
        bootstrap_needed=False, ecosystems=[], conventions_notes=None, diagnostics=[],
    ))
    # No canned response — would raise if called.
    reasoner = ScriptedReasoningEngine(draft_conventions_responses=[])
    orch = AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoner=reasoner,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )
    await orch._ensure_env_profile(tmp_path)  # must not raise


@pytest.mark.asyncio
async def test_ensure_is_serialized_under_concurrent_calls(tmp_path: Path):
    """Two concurrent _ensure_env_profile calls on the same workspace
    should only build the profile once."""
    from agentd.orchestrator.engine import AgentOrchestrator
    from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
    from agentd.storage.in_memory import InMemoryTaskStore
    from agentd.workspace.shadow import ShadowWorkspaceManager

    (tmp_path / "pyproject.toml").write_text("[project]\nname=\"x\"\nversion=\"0\"\n")

    calls = 0
    class CountingReasoner:
        async def draft_conventions(self, *, probe):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)  # ensure overlap
            return {"ecosystems": [{
                "ecosystem": "python", "subdir": "", "manifest_path": "pyproject.toml",
                "package_manager": "uv", "install_command": "uv sync",
                "interpreter_or_runner": ".venv/bin/python", "test_command": "pytest",
                "declared_dependencies_top": [], "notes": None,
            }], "conventions_notes": None}

    orch = AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoner=CountingReasoner(),
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )
    await asyncio.gather(
        orch._ensure_env_profile(tmp_path),
        orch._ensure_env_profile(tmp_path),
    )
    assert calls == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_orchestrator_env_ensure.py -v
```

Expected: AttributeError — `_ensure_env_profile` not on AgentOrchestrator.

- [ ] **Step 3: Implement the hook**

In `services/agentd-py/agentd/orchestrator/engine.py`:

Add at module top:

```python
from agentd.env.profile_builder import EnvProfileBuilder
from agentd.env.profile_store import EnvProfileStore
```

Add to `AgentOrchestrator.__init__`:

```python
self._env_profile_store = EnvProfileStore()
self._env_profile_locks: dict[str, asyncio.Lock] = {}
```

Add the method (note the SSE broadcasts; the existing orchestrator already holds a `self._broadcaster` from prior verify-phase work — match its event-emit signature):

```python
async def _ensure_env_profile(self, workspace_root: Path) -> None:
    """Build the workspace env profile if missing or stale. Idempotent and
    serialized per-workspace via an asyncio lock."""
    key = str(workspace_root.resolve())
    lock = self._env_profile_locks.setdefault(key, asyncio.Lock())
    async with lock:
        store = self._env_profile_store
        if not store.is_stale(workspace_root):
            return

        # SSE: building started
        await self._broadcaster.broadcast(
            channel_id=key,
            event="env_profile_building",
            payload={"workspace_root": key},
        )

        builder = EnvProfileBuilder(reasoner=self._reasoner)
        profile = await builder.build(workspace_root)
        store.write(workspace_root, profile)

        # SSE: built
        await self._broadcaster.broadcast(
            channel_id=key,
            event="env_profile_built",
            payload={
                "ecosystems_count": len(profile.ecosystems),
                "bootstrap_needed": profile.bootstrap_needed,
            },
        )
```

If `self._broadcaster.broadcast` has a different signature in your codebase (positional vs kwargs, sync vs async), adapt to match. The contract is: two events, one at start and one at end, with the payloads shown.

Then add `await self._ensure_env_profile(<workspace>)` near the top of BOTH `run_task` and `resume_task` (look for where the workspace path is first available; typically right after the task record is loaded). Place it BEFORE any shadow preparation or planning kicks off:

```python
async def run_task(self, task: TaskRecord) -> None:
    await self._ensure_env_profile(Path(task.workspace_path))
    # ... existing body
```

(`resume_task` and `resume_from_execute` get the same line, using the same `task.workspace_path`.)

- [ ] **Step 4: Run tests**

```
pytest tests/test_orchestrator_env_ensure.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Run full suite**

```
pytest -q
```

Expected: existing orchestrator tests still pass. If any orchestrator test fails because the scripted engine is now expected to provide a `draft_conventions_responses` entry, add `draft_conventions_responses=[]` to that test's engine construction (the empty-profile path is safe: `_ensure_env_profile` writes a `bootstrap_needed=true` profile with no LLM call).

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/orchestrator/engine.py services/agentd-py/tests/test_orchestrator_env_ensure.py
git commit -m "feat(env-profile): orchestrator _ensure_env_profile lazy-build hook + workspace lock"
```

---

## Task 8: PatchEngine manifest-write detection

**Files:**
- Modify: `services/agentd-py/agentd/patch/engine.py`
- Test: `services/agentd-py/tests/test_patch_manifest_detection.py`

- [ ] **Step 1: Write the failing tests**

Create `services/agentd-py/tests/test_patch_manifest_detection.py`:

```python
"""Tests for PatchEngine detecting manifest writes and surfacing scope_key."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentd.domain.models import EnvEcosystemEntry, EnvProfile, PatchOp
from agentd.env.profile_store import EnvProfileStore
from agentd.patch.engine import PatchEngine


def _write_python_profile(workspace: Path) -> None:
    EnvProfileStore().write(workspace, EnvProfile(
        workspace_root=str(workspace),
        built_at=datetime.now(timezone.utc),
        bootstrap_needed=False,
        ecosystems=[EnvEcosystemEntry(
            ecosystem="python", subdir="services/agentd-py",
            manifest_path="services/agentd-py/pyproject.toml",
            package_manager="uv", install_command="uv sync",
            interpreter_or_runner=".venv/bin/python", test_command="pytest",
            declared_dependencies_top=[], notes=None,
        )],
        conventions_notes=None, diagnostics=[],
    ))


@pytest.mark.asyncio
async def test_apply_op_detects_pyproject_write_and_returns_scope_key(tmp_path: Path):
    _write_python_profile(tmp_path)
    (tmp_path / "services" / "agentd-py").mkdir(parents=True)
    (tmp_path / "services" / "agentd-py" / "pyproject.toml").write_text(
        "[project]\nname=\"x\"\nversion=\"0\"\n"
    )
    engine = PatchEngine(shadow_root=tmp_path, real_workspace=tmp_path)
    op = PatchOp(
        op="search_replace",
        target_file="services/agentd-py/pyproject.toml",
        before="version=\"0\"",
        after="version=\"1\"",
    )
    result = await engine.apply_op(op)
    assert result.manifest_changed_scope_key == "python:services/agentd-py"


@pytest.mark.asyncio
async def test_apply_op_non_manifest_returns_none(tmp_path: Path):
    _write_python_profile(tmp_path)
    (tmp_path / "services" / "agentd-py" / "agentd").mkdir(parents=True)
    (tmp_path / "services" / "agentd-py" / "agentd" / "x.py").write_text("a = 1\n")
    engine = PatchEngine(shadow_root=tmp_path, real_workspace=tmp_path)
    op = PatchOp(
        op="search_replace",
        target_file="services/agentd-py/agentd/x.py",
        before="a = 1",
        after="a = 2",
    )
    result = await engine.apply_op(op)
    assert result.manifest_changed_scope_key is None


@pytest.mark.asyncio
async def test_apply_op_manifest_outside_known_scopes_returns_none(tmp_path: Path):
    # Profile has python at services/agentd-py only; a write at apps/foo/package.json
    # is unrecognized (no node scope in the profile).
    _write_python_profile(tmp_path)
    (tmp_path / "apps" / "foo").mkdir(parents=True)
    (tmp_path / "apps" / "foo" / "package.json").write_text("{}")
    engine = PatchEngine(shadow_root=tmp_path, real_workspace=tmp_path)
    op = PatchOp(
        op="create_file",
        target_file="apps/foo/package.json",
        content="{}",
    )
    result = await engine.apply_op(op)
    assert result.manifest_changed_scope_key is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_patch_manifest_detection.py -v
```

Expected: AttributeError — `manifest_changed_scope_key` not on the result type.

- [ ] **Step 3: Modify `PatchEngine.apply_op` to detect manifest writes**

Open `services/agentd-py/agentd/patch/engine.py` and:

1. At the top, add the import:

```python
from agentd.env.profile_store import EnvProfileStore
```

2. Add a constant near the top:

```python
_MANIFEST_BASENAMES = {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
```

3. Find the dataclass/Pydantic model that `apply_op` returns (search for `class .*Result` or follow the return type). Add the new optional field:

```python
manifest_changed_scope_key: str | None = None
```

4. In `apply_op`, AFTER the op is successfully applied (right before returning the result), insert:

```python
result.manifest_changed_scope_key = self._resolve_manifest_scope_key(op.target_file)
```

5. Add the helper method on `PatchEngine`:

```python
def _resolve_manifest_scope_key(self, target_file: str) -> str | None:
    """If target_file is a manifest covered by the workspace env_profile,
    return its scope_key (ecosystem:subdir). Else None."""
    from pathlib import PurePosixPath

    rel = PurePosixPath(target_file)
    if rel.name not in _MANIFEST_BASENAMES:
        return None

    profile = EnvProfileStore().read(self._real_workspace)
    if profile is None:
        return None

    for entry in profile.ecosystems:
        if entry.manifest_path == str(rel):
            return entry.scope_key
    return None
```

(If `_real_workspace` isn't already a field on `PatchEngine`, accept it via `__init__` and store it. Match the existing constructor pattern in the file.)

- [ ] **Step 4: Run tests**

```
pytest tests/test_patch_manifest_detection.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Run full suite**

```
pytest -q
```

Expected: all green. If any existing PatchEngine test fails because its result type now has a new field, those tests should not break — the field has a default of `None`.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/patch/engine.py services/agentd-py/tests/test_patch_manifest_detection.py
git commit -m "feat(env-profile): PatchEngine manifest-write detection surfaces scope_key"
```

---

## Task 9: ToolLoop auto-sync hook before run_command

**Files:**
- Modify: `services/agentd-py/agentd/tools/loop.py`
- Test: `services/agentd-py/tests/test_manifest_autosync.py`

- [ ] **Step 1: Write the failing test**

Create `services/agentd-py/tests/test_manifest_autosync.py`:

```python
"""Tests for ToolLoop auto-running setup_env after a manifest change."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from agentd.domain.models import (
    EnvEcosystemEntry, EnvProfile, TaskExecutionState,
)
from agentd.env.profile_store import EnvProfileStore


def _python_profile(workspace: Path) -> None:
    EnvProfileStore().write(workspace, EnvProfile(
        workspace_root=str(workspace),
        built_at=datetime.now(timezone.utc),
        bootstrap_needed=False,
        ecosystems=[EnvEcosystemEntry(
            ecosystem="python", subdir="services/agentd-py",
            manifest_path="services/agentd-py/pyproject.toml",
            package_manager="uv", install_command="uv sync",
            interpreter_or_runner=".venv/bin/python", test_command="pytest",
            declared_dependencies_top=[], notes=None,
        )],
        conventions_notes=None, diagnostics=[],
    ))


@pytest.mark.asyncio
async def test_loop_auto_runs_setup_env_when_pending_flag_set(tmp_path: Path):
    """Before run_command executes, if pending_install_for_scope is set,
    the loop calls setup_env with the profile's install_command + subdir.
    Flag is cleared after."""
    from agentd.tools.loop import maybe_run_pending_install

    _python_profile(tmp_path)
    state = TaskExecutionState(
        pending_install_for_scope="python:services/agentd-py"
    )

    called_with = {}
    async def fake_setup_env(*, command, shadow_root, real_workspace, cwd=None, **_):
        called_with["command"] = command
        called_with["cwd"] = cwd
        from agentd.tools.registry import ToolOutput
        return ToolOutput(output="ok", is_error=False)

    with patch("agentd.tools.env.setup_env", fake_setup_env):
        await maybe_run_pending_install(
            execution_state=state,
            shadow_root=tmp_path,
            real_workspace=tmp_path,
        )

    assert called_with["command"] == "uv sync"
    assert called_with["cwd"] == "services/agentd-py"
    assert state.pending_install_for_scope is None


@pytest.mark.asyncio
async def test_loop_skips_when_no_pending_install(tmp_path: Path):
    from agentd.tools.loop import maybe_run_pending_install
    _python_profile(tmp_path)
    state = TaskExecutionState(pending_install_for_scope=None)

    called = []
    async def fake_setup_env(*a, **k):
        called.append(1)
        from agentd.tools.registry import ToolOutput
        return ToolOutput(output="ok", is_error=False)

    with patch("agentd.tools.env.setup_env", fake_setup_env):
        await maybe_run_pending_install(
            execution_state=state,
            shadow_root=tmp_path,
            real_workspace=tmp_path,
        )

    assert called == []


@pytest.mark.asyncio
async def test_loop_clears_flag_even_when_setup_env_fails(tmp_path: Path):
    """Loop must not retry on failure — flag is one-shot. Failure surfaces
    via setup_env's tool result on the next opportunity."""
    from agentd.tools.loop import maybe_run_pending_install
    _python_profile(tmp_path)
    state = TaskExecutionState(
        pending_install_for_scope="python:services/agentd-py"
    )

    async def failing_setup_env(*a, **k):
        from agentd.tools.registry import ToolOutput
        return ToolOutput(output="boom", is_error=True)

    with patch("agentd.tools.env.setup_env", failing_setup_env):
        await maybe_run_pending_install(
            execution_state=state,
            shadow_root=tmp_path,
            real_workspace=tmp_path,
        )

    assert state.pending_install_for_scope is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_manifest_autosync.py -v
```

Expected: ImportError — `maybe_run_pending_install` not yet in `agentd.tools.loop`.

- [ ] **Step 3: Add the helper to `tools/loop.py`**

In `services/agentd-py/agentd/tools/loop.py`, add (near the top after existing imports):

```python
from pathlib import Path

from agentd.domain.models import TaskExecutionState
from agentd.env.profile_store import EnvProfileStore
from agentd.tools.env import setup_env
```

Add the helper function (at module scope, not inside `ToolLoop`):

```python
async def maybe_run_pending_install(
    *,
    execution_state: TaskExecutionState,
    shadow_root: Path,
    real_workspace: Path,
) -> None:
    """If execution_state.pending_install_for_scope is set, run the scope's
    install_command via setup_env, then clear the flag (regardless of exit).
    Failure surfaces in subsequent tool results — no retry here.
    """
    scope_key = execution_state.pending_install_for_scope
    if scope_key is None:
        return

    profile = EnvProfileStore().read(real_workspace)
    if profile is None:
        execution_state.pending_install_for_scope = None
        return

    entry = next((e for e in profile.ecosystems if e.scope_key == scope_key), None)
    if entry is None:
        execution_state.pending_install_for_scope = None
        return

    try:
        await setup_env(
            command=entry.install_command,
            shadow_root=shadow_root,
            real_workspace=real_workspace,
            cwd=entry.subdir or None,
        )
    finally:
        execution_state.pending_install_for_scope = None
```

Now wire it into the loop's `run_command` path. Find the place in `ToolLoop` where `run_command` is dispatched. Just BEFORE that dispatch, broadcast and call:

```python
scope_key = task.execution_state.pending_install_for_scope
if scope_key is not None:
    await self._broadcaster.broadcast(
        channel_id=task.task_id,
        event="env_install_running",
        payload={"scope_key": scope_key},
    )

result_summary: dict | None = None
async def _capture(*, command, shadow_root, real_workspace, cwd=None, **k):
    # passthrough wrapper that captures the setup_env result for SSE
    from agentd.tools.env import setup_env as _real
    out = await _real(command=command, shadow_root=shadow_root,
                       real_workspace=real_workspace, cwd=cwd, **k)
    nonlocal result_summary
    result_summary = {"exit_ok": not out.is_error, "tail": out.output[-400:]}
    return out

# (We don't actually intercept setup_env in production — that capture pattern
# is just here for SSE shaping. Simpler: call maybe_run_pending_install,
# then if the flag was cleared this turn, broadcast env_install_done.)

await maybe_run_pending_install(
    execution_state=task.execution_state,
    shadow_root=self._shadow_root,
    real_workspace=self._real_workspace,
)

if scope_key is not None:
    await self._broadcaster.broadcast(
        channel_id=task.task_id,
        event="env_install_done",
        payload={"scope_key": scope_key},
    )
```

Simpler alternative if the SSE shaping pattern above feels overwrought: extend `maybe_run_pending_install` to take an optional `on_done` callback and broadcast from the call site. The contract is: two events around the auto-sync, with `scope_key` in both payloads. Skip the install-result tail unless it adds debugging value.

(The exact insertion point depends on whether `run_command` is dispatched through `ToolRegistry.execute()` or invoked directly — look for the `if name == "run_command":` branch or the equivalent and insert immediately before it. If the dispatch is inside `ToolRegistry`, gate it on the registry's `execute` method instead — but the cleaner path is to call it from the loop right before each iteration's tool dispatch when the next tool name is `run_command`.)

Also: when `PatchEngine.apply_op` returns `manifest_changed_scope_key != None`, set it on the execution state. Find where the loop calls `apply_op` (or where `emit_patch` outcomes are handled) and add:

```python
if patch_result.manifest_changed_scope_key:
    task.execution_state.pending_install_for_scope = patch_result.manifest_changed_scope_key
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_manifest_autosync.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Run full suite**

```
pytest -q
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add services/agentd-py/agentd/tools/loop.py services/agentd-py/tests/test_manifest_autosync.py
git commit -m "feat(env-profile): ToolLoop manifest-write auto-sync via pending_install_for_scope"
```

---

## Task 10: API routes + end-to-end integration test

**Files:**
- Modify: `services/agentd-py/agentd/api/routes.py`
- Test: `services/agentd-py/tests/test_env_profile_routes.py`
- Test: `services/agentd-py/tests/test_env_profile_e2e.py`

- [ ] **Step 1: Write the failing route tests**

Create `services/agentd-py/tests/test_env_profile_routes.py`:

```python
"""Tests for /v1/workspaces/env-profile GET + POST routes."""
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_get_returns_404_when_no_profile(tmp_path: Path):
    from agentd.chat.app_factory import build_app
    app = build_app(workspace_root=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/v1/workspaces/env-profile", params={"workspace": str(tmp_path)})
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_post_builds_profile_then_get_returns_it(tmp_path: Path):
    from agentd.chat.app_factory import build_app
    (tmp_path / "pyproject.toml").write_text("[project]\nname=\"x\"\nversion=\"0\"\n")
    canned = {
        "ecosystems": [{
            "ecosystem": "python", "subdir": "", "manifest_path": "pyproject.toml",
            "package_manager": "uv", "install_command": "uv sync",
            "interpreter_or_runner": ".venv/bin/python", "test_command": "pytest",
            "declared_dependencies_top": [], "notes": None,
        }],
        "conventions_notes": None,
    }
    app = build_app(workspace_root=tmp_path, draft_conventions_responses=[canned])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/v1/workspaces/env-profile", params={"workspace": str(tmp_path)})
        assert r.status_code == 200
        body = r.json()
        assert body["ecosystems"][0]["package_manager"] == "uv"

        r2 = await c.get("/v1/workspaces/env-profile", params={"workspace": str(tmp_path)})
        assert r2.status_code == 200
        assert r2.json()["ecosystems"][0]["install_command"] == "uv sync"


@pytest.mark.asyncio
async def test_post_rejects_workspace_outside_filesystem(tmp_path: Path):
    from agentd.chat.app_factory import build_app
    app = build_app(workspace_root=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/v1/workspaces/env-profile", params={"workspace": "/nonexistent/path/xyz"})
        assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_env_profile_routes.py -v
```

Expected: 404 from FastAPI (route not defined yet) — note: this is correct **route-missing** 404, not the in-test "no profile" 404; tests fail because the path doesn't exist.

- [ ] **Step 3: Add the routes**

In `services/agentd-py/agentd/api/routes.py`, inside `build_router()`, add:

```python
from agentd.env.profile_builder import EnvProfileBuilder
from agentd.env.profile_store import EnvProfileStore

@router.get("/workspaces/env-profile")
async def get_env_profile(workspace: str):
    ws = Path(workspace)
    if not ws.is_dir():
        raise HTTPException(status_code=400, detail=f"workspace not a directory: {workspace}")
    profile = EnvProfileStore().read(ws)
    if profile is None:
        raise HTTPException(status_code=404, detail="env profile not built")
    return profile

@router.post("/workspaces/env-profile")
async def build_env_profile(workspace: str):
    ws = Path(workspace)
    if not ws.is_dir():
        raise HTTPException(status_code=400, detail=f"workspace not a directory: {workspace}")
    builder = EnvProfileBuilder(reasoner=reasoner)  # reasoner is closed over from build_router scope
    profile = await builder.build(ws)
    EnvProfileStore().write(ws, profile)
    return profile
```

(Match the existing route style — use the local variable name for the reasoner that other routes use; check the function signature of `build_router`.)

- [ ] **Step 4: Write the end-to-end integration test**

Create `services/agentd-py/tests/test_env_profile_e2e.py`:

```python
"""End-to-end: workspace registration → task submission → profile present
on disk before s1 ran; patch touches pyproject.toml → setup_env runs before
next run_command."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest

from agentd.env.profile_store import EnvProfileStore


@pytest.mark.asyncio
async def test_lazy_build_on_first_task(tmp_path: Path):
    from agentd.orchestrator.engine import AgentOrchestrator
    from agentd.orchestrator.scripted_engine import ScriptedReasoningEngine
    from agentd.storage.in_memory import InMemoryTaskStore
    from agentd.workspace.shadow import ShadowWorkspaceManager
    from agentd.domain.models import TaskRecord, TaskStatus

    (tmp_path / "pyproject.toml").write_text("[project]\nname=\"x\"\nversion=\"0\"\n")
    canned_conventions = {
        "ecosystems": [{
            "ecosystem": "python", "subdir": "", "manifest_path": "pyproject.toml",
            "package_manager": "uv", "install_command": "uv sync",
            "interpreter_or_runner": ".venv/bin/python", "test_command": "pytest",
            "declared_dependencies_top": [], "notes": None,
        }],
        "conventions_notes": None,
    }

    assert EnvProfileStore().read(tmp_path) is None  # not yet built

    reasoner = ScriptedReasoningEngine(
        draft_conventions_responses=[canned_conventions],
    )
    orch = AgentOrchestrator(
        store=InMemoryTaskStore(),
        reasoner=reasoner,
        workspace_manager=ShadowWorkspaceManager(tmp_path / "shadows"),
    )

    # Just exercise the ensure hook end-to-end.
    await orch._ensure_env_profile(tmp_path)
    profile = EnvProfileStore().read(tmp_path)
    assert profile is not None
    assert profile.ecosystems[0].package_manager == "uv"


@pytest.mark.asyncio
async def test_manifest_write_triggers_auto_sync_before_next_run_command(tmp_path: Path):
    """Drive the loop helper directly: state flagged → install runs → flag cleared."""
    from agentd.domain.models import (
        EnvEcosystemEntry, EnvProfile, TaskExecutionState,
    )
    from agentd.tools.loop import maybe_run_pending_install

    EnvProfileStore().write(tmp_path, EnvProfile(
        workspace_root=str(tmp_path),
        built_at=datetime.now(timezone.utc),
        bootstrap_needed=False,
        ecosystems=[EnvEcosystemEntry(
            ecosystem="python", subdir="services/agentd-py",
            manifest_path="services/agentd-py/pyproject.toml",
            package_manager="uv", install_command="uv sync",
            interpreter_or_runner=".venv/bin/python", test_command="pytest",
            declared_dependencies_top=[], notes=None,
        )],
        conventions_notes=None, diagnostics=[],
    ))

    state = TaskExecutionState(pending_install_for_scope="python:services/agentd-py")

    captured = {}
    async def fake_setup_env(*, command, shadow_root, real_workspace, cwd=None, **_):
        captured["command"] = command
        captured["cwd"] = cwd
        from agentd.tools.registry import ToolOutput
        return ToolOutput(output="installed", is_error=False)

    with mock_patch("agentd.tools.env.setup_env", fake_setup_env):
        await maybe_run_pending_install(
            execution_state=state,
            shadow_root=tmp_path,
            real_workspace=tmp_path,
        )

    assert captured == {"command": "uv sync", "cwd": "services/agentd-py"}
    assert state.pending_install_for_scope is None
```

- [ ] **Step 5: Run all new tests**

```
pytest tests/test_env_profile_routes.py tests/test_env_profile_e2e.py -v
```

Expected: all PASS.

- [ ] **Step 6: Run full suite — final regression check**

```
pytest -q
```

Expected: all green. Targeted suites that matter most:
- `tests/test_verify_phase_sm.py` (verify SM unchanged)
- `tests/test_tool_loop_state_gates.py` (loop gating unchanged)
- `tests/test_orchestrator_verify_flow.py` (orchestrator still works)
- `tests/test_orchestrator_candidate_scoring.py`, `test_orchestrator_plan_target_validation.py` (plan/orchestration regression)

- [ ] **Step 7: Final commit**

```bash
git add services/agentd-py/agentd/api/routes.py services/agentd-py/tests/test_env_profile_routes.py services/agentd-py/tests/test_env_profile_e2e.py
git commit -m "feat(env-profile): API routes + end-to-end integration tests"
```

---

## Validation checklist (after Task 10)

- [ ] `pytest -q` is fully green
- [ ] New file count: 6 source + 6 test
- [ ] Manual smoke: start backend pointed at `workspaces/shadow-forge-stress`; first task on a fresh workspace builds the profile in <30s; subsequent tasks reuse it; `cat workspaces/shadow-forge-stress/.crucible/state/env_profile.json` shows the expected ecosystems
- [ ] No new task status (`PREPARING_ENV` not introduced)
- [ ] No env state machine module exists
- [ ] `read_env_profile` appears in both `explore` and `verify` phase tool listings
- [ ] `ENV PROFILE` teaching block present in `TOOL_LOOP_SYSTEM_PROMPT`

## Notes for the implementer

- **Backend reload hazard**: editing `agentd/` files while a task is running will trigger uvicorn `--reload` and kill the in-flight coroutine, which may mark a child task `FAILED` after a DB revert. Stop the backend before editing; restart after.
- **TQP qwen3.6 timing**: `draft_conventions` is a single structured call. With the rich payload (manifest text + tree + diagnostics), expect 30-60s on local TQP for a polyglot workspace. Cache mitigates this — only first task pays the cost.
- **Test isolation**: each test gets a fresh `tmp_path`. `EnvProfileStore` writes to `<tmp_path>/.crucible/state/env_profile.json` — no cross-test bleed.
- **Direct interpreter path is the convention**: the teaching block and the schema field name (`interpreter_or_runner`) lock this in. Do NOT introduce a `source venv/bin/activate` path anywhere — it doesn't persist across tool calls.
