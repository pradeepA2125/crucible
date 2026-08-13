"""Ask a live OpenAI-compatible endpoint which reasoning-effort form it honors.

Run:
  cd services/agentd-py && source .venv/bin/activate && cd -
  export $(cat .env | grep -v "^#" | grep "=" | sed 's/"//g' | xargs)
  python scripts/verify/probe_reasoning_effort.py

Settles one question for the openai_compatible wire mapping: does NIM honor
vLLM's generic top-level `reasoning_effort` for nemotron-3, or does it need
NIM's own `chat_template_kwargs.{enable_thinking, low_effort}`?

Judges by REASONING TOKEN COUNT, not by HTTP status. An endpoint that ignores an
unknown body field returns 200 and simply reasons just as much — exactly the
silent no-op this probe exists to catch. A mapping that shipped without this
check could be a placebo and nothing in the test suite would notice.

STREAMS, and prints a live 🧠/↓ counter per case, for the same reason the
composer's WorkBar does: a non-streaming call gives no signal at all until the
whole response lands, so a slow model is indistinguishable from a hang. The
reasoning-delta field names mirror providers/openai_compatible_transport.py's
_REASONING_DELTA_FIELDS — the ecosystem split on `reasoning` vs
`reasoning_content` and NIM uses the latter.
"""
import asyncio
import os
import sys
import time

from openai import AsyncOpenAI

PROMPT = "What is 17 * 23? Answer with the number only."

BASE_URL = os.environ.get(
    "CRUCIBLE_OPENAI_COMPAT_BASE_URL", "https://integrate.api.nvidia.com/v1"
)
# Default to super-120b, not ultra-550b: it is the model whose NIM reference page
# documents enable_thinking/low_effort/reasoning_budget, and ultra is queue-bound
# on the free tier (a 16-token call to it did not return inside 55s).
MODEL = os.environ.get(
    "CRUCIBLE_OPENAI_COMPAT_MODEL", "nvidia/nemotron-3-super-120b-a12b"
)
API_KEY = (
    os.environ.get("CRUCIBLE_OPENAI_COMPAT_API_KEY")
    or os.environ.get("NVIDIA_API_KEY")
    or "x"
)
# Chars per token, matching the transport's own live-counter constant. This is an
# activity indicator, not a billing figure — the exact usage totals come from the
# stream's final usage chunk when the endpoint sends one.
CHARS_PER_TOKEN = 4
_REASONING_DELTA_FIELDS = ("reasoning", "reasoning_content")


def _say(line: str) -> None:
    print(line, flush=True)  # flush: block buffering is why an earlier run looked dead


def _reasoning_chunk(delta: object) -> str:
    for name in _REASONING_DELTA_FIELDS:
        value = getattr(delta, name, None)
        if isinstance(value, str) and value:
            return value
    return ""


async def _measure(client: AsyncOpenAI, label: str, extra: dict) -> None:
    reasoning_chars = content_chars = 0
    answer: list[str] = []
    started = time.monotonic()
    last_tick = 0.0
    usage = None
    try:
        stream = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": PROMPT}],
            max_completion_tokens=2048,
            temperature=1.0,
            stream=True,
            stream_options={"include_usage": True},
            extra_body=extra,
        )
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            for choice in chunk.choices or []:
                delta = choice.delta
                if delta is None:
                    continue
                reasoning_chars += len(_reasoning_chunk(delta))
                text = getattr(delta, "content", None)
                if isinstance(text, str) and text:
                    content_chars += len(text)
                    answer.append(text)
            elapsed = time.monotonic() - started
            if elapsed - last_tick >= 2.0:  # a heartbeat, not a per-delta firehose
                last_tick = elapsed
                _say(
                    f"    [{elapsed:5.1f}s] 🧠 ~{reasoning_chars // CHARS_PER_TOKEN:5}"
                    f"  ↓ ~{content_chars // CHARS_PER_TOKEN:4}"
                )
    except Exception as exc:  # noqa: BLE001 — a probe reports, it does not raise
        _say(f"    ERROR after {time.monotonic() - started:.1f}s: "
             f"{type(exc).__name__}: {str(exc)[:140]}")
        return

    elapsed = time.monotonic() - started
    if usage is not None:
        details = getattr(usage, "completion_tokens_details", None)
        exact_reasoning = getattr(details, "reasoning_tokens", None)
        _say(
            f"  => {elapsed:5.1f}s  completion={usage.completion_tokens}"
            f"  reasoning={exact_reasoning if exact_reasoning is not None else 'n/r'}"
            f"  (est 🧠 {reasoning_chars // CHARS_PER_TOKEN} / ↓ {content_chars // CHARS_PER_TOKEN})"
            f"  answer={''.join(answer).strip()[:30]!r}"
        )
    else:
        _say(
            f"  => {elapsed:5.1f}s  no usage chunk"
            f"  (est 🧠 {reasoning_chars // CHARS_PER_TOKEN} / ↓ {content_chars // CHARS_PER_TOKEN})"
            f"  answer={''.join(answer).strip()[:30]!r}"
        )


async def main() -> None:
    _say(f"endpoint : {BASE_URL}")
    _say(f"model    : {MODEL}\n")
    # max_retries=0: a retry triples the wall clock and tells us nothing new — a
    # probe wants the first honest answer, including a failure.
    client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=120.0, max_retries=0)
    cases = {
        "baseline (no effort field)": {},
        "generic reasoning_effort=low": {"reasoning_effort": "low"},
        "generic reasoning_effort=none": {"reasoning_effort": "none"},
        "nim chat_template_kwargs low_effort": {
            "chat_template_kwargs": {"enable_thinking": True, "low_effort": True}
        },
        "nim chat_template_kwargs off": {
            "chat_template_kwargs": {"enable_thinking": False}
        },
    }
    for label, extra in cases.items():
        _say(label)
        await _measure(client, label, extra)
    _say("\ndone")
    sys.stdout.flush()


asyncio.run(main())
