from copy import deepcopy

import pytest

from agentd.image_context import project_image_context


def ref(identity, *, width=1, size=10):
    return {"asset_id": identity, "name": identity + ".png", "width": width,
            "height": 1, "bytes": size, "media_type": "image/png"}


def resolve(value):
    return {**value, "data_url": "data:image/png;base64,AA==", "base64_data": "AA=="}


def test_newest_origin_owns_unique_visual_and_durable_history_unchanged():
    history = [{"role": "user", "content": "old", "image_refs": [ref("a")]},
               {"role": "user", "content": "new", "image_refs": [ref("a")]}]
    original = deepcopy(history)
    result = project_image_context(history, resolve)
    assert history == original
    assert "image_refs" not in result.history[0]
    assert "alias of newer" in result.history[0]["content"]
    assert result.history[1]["image_refs"][0]["asset_id"] == "a"
    assert len(result.image_assets) == 1
    assert result.estimated_image_tokens == 1536
    assert "data:image" not in str(result.history)


def test_budget_drop_is_truthful_and_revision_changes():
    history = [{"role": "user", "content": "", "image_refs": [ref("a"), ref("b")]}]
    active = project_image_context(history, resolve)
    limited = project_image_context(history, resolve, max_images=1)
    assert list(limited.image_assets) == ["b"]
    assert "Do not claim to see it" in limited.history[0]["content"]
    assert "read_image with asset_id" in limited.history[0]["content"]
    assert active.revision != limited.revision
    empty = project_image_context(history, resolve, max_visual_tokens=0)
    assert not empty.image_assets
    assert "image_refs" not in empty.history[0]


def test_oversized_newest_drops_and_smaller_older_fits_headroom():
    history = [{"role": "user", "content": "", "image_refs": [ref("small")]},
               {"role": "user", "content": "", "image_refs": [ref("large", width=8192)]}]
    result = project_image_context(history, resolve, context_window=4000,
                                   reserved_output_tokens=1000, text_and_fixed_tokens=1400)
    assert list(result.image_assets) == ["small"]
    assert "unavailable" in result.history[1]["content"]
    assert result.estimated_image_tokens <= 1600


def test_missing_asset_never_claims_visual_access():
    def missing(value):
        raise ValueError("missing")
    history = [{"role": "tool_result", "content": "image", "image_refs": [ref("a")]}]
    result = project_image_context(history, missing)
    assert not result.image_assets
    assert result.estimated_image_tokens == 0
    assert "missing asset" in result.history[0]["content"]
    assert "image_refs" not in result.history[0]


def test_byte_budget_and_origin_change_revision():
    history = [{"role": "user", "content": "", "image_refs": [ref("a", size=20)]}]
    dropped = project_image_context(history, resolve, max_bytes=19)
    assert not dropped.image_assets
    active = project_image_context(history, resolve)
    moved = project_image_context([{"role": "user", "content": "text"}] + history, resolve)
    assert moved.revision != active.revision


def test_observed_total_does_not_double_count_existing_visuals():
    from agentd.memory.compactor import input_tokens
    from agentd.memory.models import ObservedPrompt
    history = [{"role": "user", "content": "x", "image_refs": [ref("a")]}]
    observed = ObservedPrompt(tokens=2000, message_count=1)
    assert input_tokens(history, observed) == 2000
    history.append({"role": "user", "content": "y", "image_refs": [ref("b")]})
    assert input_tokens(history, observed) == 3537


@pytest.mark.asyncio
async def test_compaction_tombstone_survives_summary_without_binary(tmp_path):
    from agentd.memory.compactor import Compactor
    from agentd.memory.store import MemoryStore
    async def summarize(old, evicted):
        return "summary omitted image details"
    history = [{"role": "user", "content": "old", "image_refs": [ref("a")]},
               {"role": "user", "content": "latest"}]
    view = project_image_context(history, resolve)
    compactor = Compactor(MemoryStore(tmp_path / "m.db"), summarize,
                          window_tokens=1000, hot_turns=1)
    result = await compactor.maybe_compact(history, "r", force=True,
                                          projected_history=view.history)
    assert result.compacted
    assert "history compaction" in result.history[0]["content"]
    assert "Do not claim to see it" in result.history[0]["content"]
    assert "data:image" not in str(result.history)


@pytest.mark.asyncio
async def test_actual_loop_known_small_window_without_usage_drops_image(tmp_path, monkeypatch):
    from agentd.chat.controller_loop import ControllerLoop
    from agentd.chat.controller_phase import ControllerPhaseSM
    from agentd.images import ImageAssetStore
    from agentd.memory.harness import TurnPreparation
    from agentd.orchestrator.broadcaster import EventBroadcaster
    from agentd.tools.sources import AggregatingToolRegistry

    captured = []
    class Engine:
        async def create_controller_step(self, *, history, image_assets=None, **kwargs):
            captured.append((history, image_assets))
            return {"type": "answer", "thought": "done", "answer": "ok"}
    class Harness:
        image_context_window = 32000
        async def prepare_turn(self, history, *args, **kwargs):
            return TurnPreparation(history=history, recalled_memories=[], compacted=False)
    monkeypatch.setattr(ImageAssetStore, "resolve", lambda self, value: resolve(value))
    loop = ControllerLoop(Engine(), AggregatingToolRegistry([]), EventBroadcaster(),
                          channel_id="c", phase_sm=ControllerPhaseSM(start="ACTIVE"),
                          memory_harness=Harness())
    durable = [{"role": "user", "content": "look", "image_refs": [ref("a")]}]
    await loop.run({"goal": "look", "workspace_path": str(tmp_path)}, seed_history=durable)
    assert captured
    assert not captured[0][1]
    assert "unavailable" in captured[0][0][0]["content"]
    assert durable[0]["image_refs"]


def test_missing_duplicates_share_truthful_reason():
    def missing(value):
        raise ValueError("missing")
    history = [{"content": "", "image_refs": [ref("a"), ref("a")]}]
    projection = project_image_context(history, missing)
    assert projection.history[0]["content"].count("missing asset") == 2


def test_overhead_is_pinned_to_measured_projection():
    from agentd.memory.compactor import fixed_overhead
    from agentd.memory.models import ObservedPrompt
    observed = ObservedPrompt(2000, 1, history_tokens=1537)
    assert fixed_overhead([{"content": "missing tombstone"}], observed) == 463


@pytest.mark.parametrize("batching", [None, "false", "true"], ids=["unset", "false", "true"])
@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.asyncio
async def test_real_read_image_loop_origins_refs_and_separate_assets(
    tmp_path, monkeypatch, count, batching,
):
    from PIL import Image

    from agentd.chat.controller_loop import ControllerLoop
    from agentd.chat.controller_phase import ControllerPhaseSM
    from agentd.orchestrator.broadcaster import EventBroadcaster
    from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource

    if batching is None:
        monkeypatch.delenv("CRUCIBLE_TOOL_BATCHING_ENABLED", raising=False)
    else:
        monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", batching)
    for index in range(count):
        Image.new("RGB", (2, 2), (index * 100, 0, 0)).save(tmp_path / f"{index}.png")
    captured = []
    calls = [{"id": f"c{index}", "tool": "read_image", "args": {"path": f"{index}.png"}}
             for index in range(count)]
    # The disabled schema permits one member per action, not a two-member batch.
    groups = [calls] if batching == "true" else [[call] for call in calls]

    class Engine:
        async def create_controller_step(self, *, history, image_assets=None, **kwargs):
            captured.append((deepcopy(history), deepcopy(image_assets)))
            step = len(captured) - 1
            if step:
                expected = count if batching == "true" else step
                assert len(image_assets) == expected
                completed = [m for m in history if m.get("tool") == "read_image"]
                assert all(m["status"] == "success" for m in completed)
            if step < len(groups):
                return {"type": "tool_calls", "thought": "inspect images", "calls": groups[step]}
            return {"type": "answer", "thought": "done", "answer": "ok"}

    registry = AggregatingToolRegistry([BuiltinToolSource(
        shadow_root=tmp_path, real_workspace_path=tmp_path)])
    loop = ControllerLoop(Engine(), registry, EventBroadcaster(), channel_id="c",
                          phase_sm=ControllerPhaseSM(start="ACTIVE"))
    outcome = await loop.run({"goal": "inspect images", "workspace_path": str(tmp_path)})
    view, assets = captured[-1]
    results = [m for m in view if m.get("tool") == "read_image"]
    assert len(results) == count
    assert len(assets) == count
    assert len({asset["data_url"] for asset in assets.values()}) == count
    assert len(captured) == len(groups) + 1
    assert all(message["status"] == "success" and not message["is_error"] for message in results)
    for index, message in enumerate(results):
        assert message["call_id"].endswith(f"_c{index}")
        identity = message["image_refs"][0]["asset_id"]
        assert identity in assets
        assert assets[identity]["data_url"].startswith("data:image/png;base64,")
    assert "data:image" not in str(view)
    assert "base64_data" not in str(outcome.history)
    assert all(m["image_refs"] for m in outcome.history if m.get("tool") == "read_image")


@pytest.mark.parametrize("batching", [None, "false"], ids=["unset", "false"])
@pytest.mark.asyncio
async def test_real_image_batch_disabled_rejects_without_visuals(tmp_path, monkeypatch, batching):
    from PIL import Image

    from agentd.chat.controller_loop import ControllerLoop
    from agentd.chat.controller_phase import ControllerPhaseSM
    from agentd.orchestrator.broadcaster import EventBroadcaster
    from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource

    if batching is None:
        monkeypatch.delenv("CRUCIBLE_TOOL_BATCHING_ENABLED", raising=False)
    else:
        monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", batching)
    for index in range(2):
        Image.new("RGB", (2, 2), (index * 100, 0, 0)).save(tmp_path / f"{index}.png")
    captured = []

    class Engine:
        async def create_controller_step(self, *, history, image_assets=None, **kwargs):
            captured.append((deepcopy(history), deepcopy(image_assets)))
            if len(captured) == 1:
                return {"type": "tool_calls", "thought": "inspect", "calls": [
                    {"id": f"c{i}", "tool": "read_image", "args": {"path": f"{i}.png"}}
                    for i in range(2)]}
            return {"type": "answer", "thought": "done", "answer": "ok"}

    registry = AggregatingToolRegistry([BuiltinToolSource(
        shadow_root=tmp_path, real_workspace_path=tmp_path)])
    loop = ControllerLoop(Engine(), registry, EventBroadcaster(), channel_id="c",
                          phase_sm=ControllerPhaseSM(start="ACTIVE"))
    outcome = await loop.run({"goal": "inspect", "workspace_path": str(tmp_path)})
    view, assets = captured[1]
    results = [m for m in view if m.get("tool") == "read_image"]
    assert len(results) == 2
    assert all(m["status"] == "blocked" and m["is_error"] for m in results)
    assert all("calls must contain 1–1 members" in m["content"] for m in results)
    assert not assets
    assert not any(m.get("image_refs") for m in outcome.history)
    assert not outcome.tool_events


@pytest.mark.parametrize("scenario", ["sibling_error", "small_text_budget", "no_result_headroom",
                                      "no_visual_headroom"])
@pytest.mark.asyncio
async def test_real_image_batch_sibling_and_budgets(tmp_path, monkeypatch, scenario):
    from PIL import Image

    from agentd.chat.controller_loop import ControllerLoop
    from agentd.chat.controller_phase import ControllerPhaseSM
    from agentd.memory.harness import TurnPreparation
    from agentd.orchestrator.broadcaster import EventBroadcaster
    from agentd.tools.sources import AggregatingToolRegistry, BuiltinToolSource

    monkeypatch.setenv("CRUCIBLE_TOOL_BATCHING_ENABLED", "true")
    for index in range(2):
        Image.new("RGB", (2, 2), (index * 100, 0, 0)).save(tmp_path / f"{index}.png")
    captured = []

    class Harness:
        image_context_window = 1 if scenario == "no_visual_headroom" else 272000

        async def prepare_turn(self, history, *args, **kwargs):
            return TurnPreparation(history=history)

    class Engine:
        async def create_controller_step(self, *, history, image_assets=None, **kwargs):
            captured.append((deepcopy(history), deepcopy(image_assets)))
            if len(captured) == 1:
                return {"type": "tool_calls", "thought": "inspect", "calls": [
                    {"id": f"c{i}", "tool": "read_image", "args": {"path":
                     "missing.png" if scenario == "sibling_error" and i == 0 else f"{i}.png"}}
                    for i in range(2)]}
            return {"type": "answer", "thought": "done", "answer": "ok"}

    registry = AggregatingToolRegistry([BuiltinToolSource(
        shadow_root=tmp_path, real_workspace_path=tmp_path)])
    loop = ControllerLoop(Engine(), registry, EventBroadcaster(), channel_id="c",
                          phase_sm=ControllerPhaseSM(start="ACTIVE"), memory_harness=Harness())
    budget = 1023 if scenario == "no_result_headroom" else 1024
    outcome = await loop.run({"goal": "inspect", "workspace_path": str(tmp_path),
                              "tool_result_budget": budget})
    assert len(captured) == 2
    view, assets = captured[1]
    results = [m for m in view if m.get("tool") == "read_image"]
    durable = [m for m in outcome.history if m.get("tool") == "read_image"]
    assert len(results) == len(durable) == 2
    assert all(m["call_id"].endswith(f"_c{i}") for i, m in enumerate(results))
    assert "data:image" not in str(view)
    assert "base64_data" not in str(outcome.history)
    if scenario == "no_result_headroom":
        assert all(m["status"] == "blocked" and m["is_error"] for m in results)
        assert all("insufficient result headroom" in m["content"] for m in results)
        assert not assets
        assert not any(m.get("image_refs") for m in durable)
    elif scenario == "no_visual_headroom":
        assert all(m["status"] == "success" for m in results)
        assert all(m["image_refs"] for m in durable)
        assert not assets
        assert all(not m.get("image_refs") and "Do not claim to see it" in m["content"]
                   for m in results)
    else:
        expected = 1 if scenario == "sibling_error" else 2
        assert len(assets) == expected
        assert len({a["data_url"] for a in assets.values()}) == expected
        for i, message in enumerate(results):
            if scenario == "sibling_error" and i == 0:
                assert message["status"] == "error" and message["is_error"]
                assert not message.get("image_refs")
            else:
                assert message["status"] == "success" and not message["is_error"]
                assert message["image_refs"][0]["asset_id"] in assets
                assert durable[i]["image_refs"]


@pytest.mark.asyncio
async def test_loop_invalidates_observed_and_cache_when_asset_disappears(tmp_path, monkeypatch):
    from agentd.chat.controller_loop import ControllerLoop
    from agentd.chat.controller_phase import ControllerPhaseSM
    from agentd.images import ImageAssetStore
    from agentd.memory.harness import TurnPreparation
    from agentd.orchestrator.broadcaster import EventBroadcaster
    from agentd.tools.sources import AggregatingToolRegistry

    captured = []
    observations = []
    invalidations = []
    available = True

    def asset(self, value):
        if not available:
            raise ValueError("missing")
        return resolve(value)

    class Harness:
        image_context_window = 272000
        async def prepare_turn(self, history, *args, observed=None, **kwargs):
            observations.append(observed)
            return TurnPreparation(history=history)

    class Engine:
        async def create_controller_step(self, *, history, on_usage, image_assets=None, **kwargs):
            nonlocal available
            captured.append((history, image_assets))
            on_usage(20000, 10)
            if len(captured) == 1:
                available = False
                return {"type": "progress", "thought": "continue", "note": "Inspecting"}
            return {"type": "answer", "thought": "done", "answer": "ok"}

    monkeypatch.setattr(ImageAssetStore, "resolve", asset)
    loop = ControllerLoop(Engine(), AggregatingToolRegistry([]), EventBroadcaster(),
                          channel_id="c", phase_sm=ControllerPhaseSM(start="ACTIVE"),
                          memory_harness=Harness(),
                          context_invalidate=lambda: invalidations.append(True))
    outcome = await loop.run({"goal": "look", "workspace_path": str(tmp_path)},
                             seed_history=[{"content": "look", "role": "user",
                                            "image_refs": [ref("a")]}])
    assert captured[0][1]
    assert not captured[1][1]
    assert "missing asset" in captured[1][0][0]["content"]
    assert observations == [None, None]
    assert invalidations
    assert outcome.history[0]["image_refs"]


@pytest.mark.parametrize("kind", ["input_image", "image", "gemini"])
def test_physical_metrics_do_not_count_base64_as_text(kind):
    from agentd.context_metrics import physical_context
    def body(raw):
        if kind == "input_image":
            part = {"type": kind, "image_url": "data:image/png;base64," + raw}
        elif kind == "image":
            part = {"type": kind, "source": {"type": "base64", "data": raw}}
        else:
            part = {"inline_data": {"mime_type": "image/png", "data": raw}}
        return {"contents" if kind == "gemini" else "input": [part]}
    small = physical_context(body("AA=="))
    large = physical_context(body("AAAA" * 1000))
    assert small["total_chars"] == large["total_chars"]
    assert large["image_count"] == 1
    assert large["image_bytes"] == 3000

