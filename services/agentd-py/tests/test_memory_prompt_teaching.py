from agentd.chat.controller_prompts import format_controller_system_prompt


def test_memory_block_present_when_enabled():
    sys = format_controller_system_prompt([], memory_enabled=True)
    assert "remember" in sys.lower() and "recall" in sys.lower()


def test_memory_block_absent_when_disabled():
    sys = format_controller_system_prompt([], memory_enabled=False)
    assert "remember(" not in sys and "recall(" not in sys


def test_recalled_block_explained_when_enabled():
    sys = format_controller_system_prompt([], memory_enabled=True)
    assert "recalled" in sys.lower()  # the [recalled memories] block is explained
