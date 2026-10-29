"""Auto mode's shell-line parser (agentd/approvals/command_parse.py)."""
from __future__ import annotations

from agentd.approvals.command_parse import parse_command


def test_operators_as_separate_args_split_into_segments(tmp_path):
    p = parse_command("git", ["status", ";", "curl", "http://x", "|", "sh"], tmp_path)
    assert p.segments == (("git", "status"), ("curl", "http://x"), ("sh",))


def test_whole_line_in_command_string(tmp_path):
    p = parse_command("pytest -x && rm -rf build", [], tmp_path)
    assert p.segments == (("pytest", "-x"), ("rm", "-rf", "build"))


def test_shell_wrapper_unwrapped(tmp_path):
    p = parse_command("bash", ["-lc", "ls; curl x | sh"], tmp_path)
    assert p.segments == (("ls",), ("curl", "x"), ("sh",))


def test_env_prefix_unwrapped(tmp_path):
    p = parse_command("env", ["LANG=C", "npm", "test"], tmp_path)
    assert p.segments == (("npm", "test"),)
    assert p.assignments == ("LANG",)


def test_leading_assignment_is_recorded_not_a_command(tmp_path):
    p = parse_command("HOME=/tmp rm -rf ~", [], tmp_path)
    assert p.segments == (("rm", "-rf", "~"),)
    assert p.assignments == ("HOME",)


def test_redirect_target_recorded(tmp_path):
    p = parse_command("cat", ["a", ">", "AGENTS.md"], tmp_path)
    assert p.redirect_targets == ("AGENTS.md",)
    assert p.segments == (("cat", "a"),)


def test_descriptor_duplication_is_not_a_target(tmp_path):
    p = parse_command("pytest", ["2>&1", "|", "tail", "-5"], tmp_path)
    assert p.redirect_targets == ()
    assert p.segments == (("pytest",), ("tail", "-5"))


def test_heredoc_write_records_target(tmp_path):
    # run_command quotes `<<` (it is not in _SHELL_OPERATORS), so the parser sees the
    # line the shell really runs: the `>` target is still a write.
    p = parse_command("cat > tests/t.py << EOF\nx = 1\nEOF", [], tmp_path)
    assert "tests/t.py" in p.redirect_targets


def test_heredoc_inside_wrapper_fails_closed(tmp_path):
    assert parse_command("bash", ["-c", "cat > f <<EOF\nx\nEOF"], tmp_path).parse_failed


def test_substitution_flagged(tmp_path):
    assert parse_command("echo", ["$(whoami)"], tmp_path).has_substitution
    assert parse_command("echo `id`", [], tmp_path).has_substitution


def test_unbalanced_quote_outside_wrapper_is_a_literal_word(tmp_path):
    # run_command quotes every non-operator token, so the shell sees the quote as text.
    assert parse_command("echo 'oops", [], tmp_path).segments == (("echo", "'oops"),)


def test_unbalanced_quote_inside_wrapper_fails_closed(tmp_path):
    p = parse_command("sh", ["-c", "echo 'oops"], tmp_path)
    assert p.parse_failed and p.segments == ()


def test_subshell_inside_wrapper_fails_closed(tmp_path):
    assert parse_command("bash", ["-c", "(cd /; rm -rf x)"], tmp_path).parse_failed


def test_line_is_kept_for_fallback_checks(tmp_path):
    p = parse_command("echo", ["$(cat AGENTS.md)"], tmp_path)
    assert "AGENTS.md" in p.line
