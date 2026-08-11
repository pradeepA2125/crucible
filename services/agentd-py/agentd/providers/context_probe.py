"""The context-window Test button.

Verifies a DECLARED context window by capability rather than by HTTP status.
That distinction is the whole point: measured against NVIDIA NIM, a 600,058-token
prompt returned HTTP 200, billed every token, and answered with
`completion_tokens: 1` and empty content. A test that looked for an error would
have reported success at roughly five times the real window.

So the probe puts a passphrase at the very FRONT of a prompt sized to the declared
window and asks for it back. The front is what falls off when the window is
overrun, so recall is evidence the model can genuinely use a context that size,
and silence is evidence it cannot.

The completion budget is squeezed from BOTH directions at once, and both are real
bugs this module has shipped and fixed:

- Too large, and `prompt_tokens + max_tokens` overruns the declared window on any
  endpoint that validates the sum (vLLM, OpenAI, Anthropic all do) — a CORRECTLY
  declared window fails the test for a reason that has nothing to do with the
  window. This was `_PROBE_COMPLETION_TOKENS`'s original bug (fixed by shrinking
  it off the transport's 4096-token default, see the constant's own comment).
- Too small, and a reasoning model (one that emits hidden "thinking" tokens before
  its visible answer, e.g. anything `_is_reasoning_model` in
  openai_compatible_transport.py matches — "nemotron" among them) spends the
  ENTIRE completion budget on that thinking and never emits the answer, so the
  probe reports `recalled=False` — "your window is too large" — for a window that
  was never actually the problem. Measured live against NVIDIA NIM's
  nvidia/nemotron-3-ultra-550b-a55b at a fixed, trivially in-window 5,000-token
  prompt: a 16-token budget produced the truncated reasoning fragment
  `'The user is asking for the passphrase from the'` (never reaching the answer)
  for BOTH filler styles tested; a 512-token budget produced the correct
  three-word passphrase for both. The failure at 16 tokens was a starved
  completion budget, not a real window limit — see `_PROBE_COMPLETION_TOKENS`.

Satisfying both at once means the headroom formula must reserve room for the
completion budget explicitly, not just take a fraction of the window — see
`_HEADROOM_FRAC` / `_PROBE_COMPLETION_TOKENS`.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
from dataclasses import dataclass

from agentd.providers.factory import build_transport, resolve_model

# Mid-range of the 3.71-4.40 chars/token that Part 1 measured against real
# responses on the configured provider. Sizing the filler is unavoidably an
# estimate — under-filling produces a false pass, over-filling a false fail —
# which is why run_context_test reports the provider's OWN prompt_tokens next to
# the verdict wherever the transport supplies it. This number sizes the request;
# that number is what the user is shown.
PROBE_CHARS_PER_TOKEN = 4.0

# Ordinary words, not hex: a recall target like "9f3a71c2" invites tokenizer
# mangling and would produce a wrong verdict for a reason that has nothing to do
# with the context window. new_passphrase samples without replacement, so the
# space is 48x47x46 = 103,776 combinations — far more than enough to stop a
# cached or echoed response from faking a pass.
_WORDS = (
    "velvet", "harbor", "quasar", "lantern", "cobalt", "meadow", "cinder", "harrow",
    "pewter", "willow", "basalt", "kestrel", "marlin", "nimbus", "orchard", "plover",
    "quarry", "ribbon", "saffron", "tundra", "umber", "verdant", "walnut", "yonder",
    "zephyr", "amber", "bramble", "citrine", "dapple", "ember", "fathom", "granite",
    "hollow", "indigo", "juniper", "kindle", "lichen", "mortar", "nectar", "opaline",
    "pumice", "quiver", "russet", "sable", "thicket", "upland", "vellum", "wicker",
)

_SYSTEM = (
    "You are checking whether a long document fits in your context window. "
    "The document begins with a line reading PASSPHRASE: followed by three "
    "hyphenated words. Reply with those three words and nothing else. If you "
    "cannot see that line, reply exactly: NOT VISIBLE."
)

# Filler that is cheap to build and hard to skim: a model cannot infer the
# passphrase from the body, so recall really does require having read the front.
# ASCII-only by design: the payload is sent as JSON, and json.dumps's default
# ensure_ascii=True escapes any non-ASCII character (e.g. an em-dash) to a
# 6-char \uXXXX sequence — that inflation is real (it's what the wire actually
# carries), but build_probe's sizing loop counts raw characters, so a non-ASCII
# filler would make the built document silently overshoot the declared window.
_FILLER_LINE = (
    "{n:07d} reference record - inventory checksum, no semantic content, "
    "retained for context-length measurement only.\n"
)

# The probe's visible answer is three words ("velvet-harbor-quasar"), but a
# REASONING model (one that emits hidden "thinking" tokens sharing the same
# completion budget as its visible answer — see openai_compatible_transport.py's
# _is_reasoning_model, which matches "nemotron" among others) spends that budget
# on thinking FIRST. Measured live against NVIDIA NIM's
# nvidia/nemotron-3-ultra-550b-a55b at a fixed, trivially in-window 5,000-token
# prompt: 16 tokens produced only a truncated reasoning fragment
# ('The user is asking for the passphrase from the') and never reached the
# answer, for both filler styles tested; 512 tokens produced the correct
# three-word passphrase for both. So 512 is not a stylistic margin the way "real
# answers run a little long" would suggest — it is the measured floor below
# which a reasoning model cannot finish thinking and answer inside the budget,
# which reads back as a false "window too large" failure. Do not shrink this
# below 512 without a live re-measurement on a reasoning model; there is a
# regression test pinning the floor (test_context_probe.py).
_PROBE_COMPLETION_TOKENS = 512

# Room for the system prompt, the JSON envelope, the chat template's own tokens,
# and the completion budget above. Without it a probe sized exactly to the
# window would overrun it by construction and fail every time.
#
# PROPORTIONAL, not flat, AND floored at the completion budget plus slack —
# three lower bounds, the largest wins:
#   1. _MIN_HEADROOM_TOKENS: a flat absolute floor.
#   2. window * _HEADROOM_FRAC: a flat reserve dominates small windows (at a
#      flat 2048 a declared 4,096 window was filled to only 52%), so this scales
#      with the window instead. Under-filling is the FALSE-PASS direction — the
#      probe would confirm a window it never actually tested.
#   3. _PROBE_COMPLETION_TOKENS + _NON_DOCUMENT_SLACK_TOKENS: at small declared
#      windows (near the route's 1,024 floor), 5% of the window is far smaller
#      than the 512-token completion budget alone — reserving only the
#      proportional amount would let prompt_tokens + max_tokens exceed the
#      window again, the exact bug _PROBE_COMPLETION_TOKENS was shrunk to fix.
#      This bound guarantees the completion budget is ALWAYS covered regardless
#      of how small the declared window is.
# The fill ratio is necessarily lower at the smallest windows now than it was
# under the old formula — the completion budget is a real fixed cost that has to
# come from somewhere, and reserving too little for it is exactly the bug this
# module exists to avoid repeating in the other direction.
_HEADROOM_FRAC = 0.05
_MIN_HEADROOM_TOKENS = 256

# Covers the system prompt (~66 estimated tokens, see _SYSTEM) and the JSON
# envelope's quoting/key overhead (~21 estimated tokens for the "document"/
# "question" keys and escaping) that build_probe adds ON TOP OF the document it
# sizes to budget_tokens — both measured live via build_probe/estimated_prompt_
# tokens against the real _SYSTEM string and payload shape. 256 rounds that
# ~87-token overhead up generously (headroom is cheap; a false "too large"
# result from under-reserving is not) and doubles as the chat-template/
# JSON-escaping absorption the old flat comment described.
_NON_DOCUMENT_SLACK_TOKENS = 256


def new_passphrase(rng: random.Random | None = None) -> str:
    """Three hyphenated words, fresh per test run."""
    source = rng or random.Random()
    return "-".join(source.sample(_WORDS, 3))


def build_probe(
    window_tokens: int, passphrase: str
) -> tuple[str, dict[str, object]]:
    """(system_instructions, user_payload) for a prompt of the declared size."""
    headroom = max(
        _MIN_HEADROOM_TOKENS,
        int(window_tokens * _HEADROOM_FRAC),
        _PROBE_COMPLETION_TOKENS + _NON_DOCUMENT_SLACK_TOKENS,
    )
    budget_tokens = max(256, window_tokens - headroom)
    target_chars = int(budget_tokens * PROBE_CHARS_PER_TOKEN)
    head = f"PASSPHRASE: {passphrase}\n\n"
    parts = [head]
    filled = len(head)
    n = 0
    while filled < target_chars:
        line = _FILLER_LINE.format(n=n)
        parts.append(line)
        filled += len(line)
        n += 1
    document = "".join(parts)[:target_chars]
    return _SYSTEM, {"document": document, "question": "What is the passphrase?"}


def estimated_prompt_tokens(
    system_instructions: str, user_payload: dict[str, object]
) -> int:
    """Our own estimate of what this probe will cost, in the same units the filler
    was built in. Reported only when the transport gives us nothing better."""
    total_chars = len(system_instructions) + len(json.dumps(user_payload))
    return int(total_chars / PROBE_CHARS_PER_TOKEN)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def recalled(answer: str, passphrase: str) -> bool:
    """Did the model read the front of the prompt?

    Hyphen- and case-insensitive, because a model that reproduces the words has
    demonstrated recall whether or not it copies the punctuation. An empty answer
    is a definite FAIL, not an inconclusive result — that is precisely the shape
    of NIM's over-long-prompt response.
    """
    if not answer.strip():
        return False
    return _normalize(passphrase) in _normalize(answer)


@dataclass(frozen=True)
class ContextTestResult:
    """What one context test learned.

    `ok` and `recalled` answer different questions, and conflating them is the
    failure this whole module exists to avoid: `ok` is "the call completed",
    `recalled` is "the model could actually use a context that size". The NIM
    measurement in this module's docstring is ok=True, recalled=False.

    `exact` says whether `prompt_tokens` came from the provider or from our own
    chars-per-token estimate, so the UI can label it honestly rather than
    presenting a guess as a measurement.
    """

    ok: bool
    recalled: bool
    prompt_tokens: int | None = None
    exact: bool = False
    error: str | None = None


def context_test_timeout_sec() -> float:
    # 240, not 300: the settings webview's request to the backend route travels
    # through Node's fetch (undici), whose default headersTimeout is exactly
    # 300000ms. A backend timeout that also lands at 300s races that client-side
    # abort — whichever fires first decides what the user sees, and losing the
    # race surfaces an opaque UND_ERR_HEADERS_TIMEOUT instead of this module's
    # own "Provider did not respond within 240s". 240 keeps a 60s margin so the
    # backend's own clean timeout always wins.
    try:
        return float(os.getenv("CRUCIBLE_CONTEXT_TEST_TIMEOUT_SEC", "240"))
    except ValueError:
        return 240.0


async def run_context_test(
    *,
    backend: str,
    model: str | None,
    credentials: dict[str, str] | None,
    window_tokens: int,
    timeout_sec: float | None = None,
) -> ContextTestResult:
    """Send one prompt of the declared size and judge by passphrase recall.

    Deliberately expensive: it consumes approximately one full window of input
    tokens in a single request. It is opt-in from the UI and is never run as part
    of a save.
    """
    limit = timeout_sec if timeout_sec is not None else context_test_timeout_sec()
    try:
        transport = build_transport(backend, credentials=credentials)
        resolved = model or resolve_model(backend)
    except Exception as exc:
        # Same reasoning as ping_provider's: a transport that refuses to construct
        # carries the actionable message ("…_BASE_URL is required"), and that is
        # what the user should read — not a stack trace.
        return ContextTestResult(ok=False, recalled=False, error=str(exc))

    passphrase = new_passphrase()
    system, payload = build_probe(window_tokens, passphrase)

    observed: list[int] = []
    kwargs: dict[str, object] = {}
    # Gated exactly as reasoning/engine.py gates its progress callbacks: the eight
    # transports that do not declare this capability must never see either kwarg
    # — their generate_text signatures don't accept on_usage OR max_tokens, and
    # supports_token_progress is the one capability flag that already tells us
    # which transport (OpenAICompatibleTransport and its OpenRouter subclass)
    # accepts both.
    if getattr(transport, "supports_token_progress", False):
        kwargs["on_usage"] = lambda prompt_tokens, _completion: observed.append(
            prompt_tokens
        )
        kwargs["max_tokens"] = _PROBE_COMPLETION_TOKENS

    try:
        answer = await asyncio.wait_for(
            transport.generate_text(
                model=resolved,
                system_instructions=system,
                user_payload=payload,
                **kwargs,  # type: ignore[arg-type]
            ),
            timeout=limit,
        )
    except TimeoutError:
        return ContextTestResult(
            ok=False, recalled=False,
            error=f"Provider did not respond within {limit:.0f}s",
        )
    except Exception as exc:  # surface the provider's own message — it names the fix
        return ContextTestResult(ok=False, recalled=False, error=str(exc))

    return ContextTestResult(
        ok=True,
        recalled=recalled(answer, passphrase),
        prompt_tokens=observed[-1] if observed else estimated_prompt_tokens(system, payload),
        exact=bool(observed),
    )
