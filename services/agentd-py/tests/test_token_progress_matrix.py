"""Which transports advertise live token progress.

The WorkBar's counter is gated on `supports_token_progress`; a transport that does
not set it silently receives no on_progress/on_usage/on_salvage and the counter stays
blank for the entire turn — which is how the gap went unnoticed until it was spotted
live on Ollama-hosted nemotron.

This pins the matrix so the next transport added has to make a deliberate choice
rather than inheriting a silent no.
"""
import inspect

import pytest

SUPPORTED = {
    "openai_compatible_transport",  # instance attr, set in __init__
    "openrouter_transport",         # subclasses OpenAICompatibleTransport
    "ollama_transport",
    "gemini_transport",
    "turboquant_transport",
    "groq_transport",
}

# generate_json here never streams: on_thinking is either an ignored parameter or is
# called once post-hoc on the assembled reply, so there are no deltas to count.
# Adding the counter means adding streaming first — a real change, not an increment.
NOT_STREAMING = {
    "anthropic_transport",
    "openai_transport",
    "huggingface_transport",
    "watsonx_transport",
}


@pytest.mark.parametrize("mod_name", sorted(SUPPORTED | NOT_STREAMING))
def test_transport_progress_support_is_declared_deliberately(mod_name: str) -> None:
    mod = __import__(f"agentd.providers.{mod_name}", fromlist=["*"])
    transport_cls = next(
        (obj for _n, obj in inspect.getmembers(mod, inspect.isclass)
         if obj.__module__ == mod.__name__ and _n.endswith("Transport")),
        None,
    )
    assert transport_cls is not None, f"no Transport class found in {mod_name}"

    declared = getattr(transport_cls, "supports_token_progress", False)
    if mod_name in NOT_STREAMING:
        assert declared is False, (
            f"{mod_name} advertises token progress but generate_json does not stream — "
            "the counter would stay blank and the stall detector would have nothing "
            "to key off")


def test_every_transport_module_is_accounted_for() -> None:
    """A new transport must be classified here, not silently omitted."""
    import pathlib
    d = pathlib.Path(__file__).resolve().parents[1] / "agentd" / "providers"
    on_disk = {p.stem for p in d.glob("*_transport.py")}
    assert on_disk == SUPPORTED | NOT_STREAMING, (
        f"unclassified: {on_disk - (SUPPORTED | NOT_STREAMING)}, "
        f"stale: {(SUPPORTED | NOT_STREAMING) - on_disk}")
