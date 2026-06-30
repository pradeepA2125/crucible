from agentd.chat.controller_prompts import build_controller_step_payload


def test_active_skills_ride_the_tail_after_goal() -> None:
    ctx = {
        "workspace_path": "/ws",
        "goal": "do it",
        "active_skills": [{"name": "git-commit", "body": "STEP 1..."}],
    }
    payload = build_controller_step_payload(ctx, [], [], phase="DECIDE")
    assert payload["active_skills"] == [{"name": "git-commit", "body": "STEP 1..."}]
    keys = list(payload.keys())
    assert keys.index("active_skills") > keys.index("goal")  # tail, after goal


def test_active_skills_omitted_when_empty() -> None:
    payload = build_controller_step_payload(
        {"workspace_path": "/ws", "goal": "x", "active_skills": []}, [], [], phase="DECIDE"
    )
    assert "active_skills" not in payload
