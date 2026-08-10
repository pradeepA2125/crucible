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
"""
from __future__ import annotations

import json
import random
import re

# Mid-range of the 3.71-4.40 chars/token that Part 1 measured against real
# responses on the configured provider. Sizing the filler is unavoidably an
# estimate — under-filling produces a false pass, over-filling a false fail —
# which is why run_context_test reports the provider's OWN prompt_tokens next to
# the verdict wherever the transport supplies it. This number sizes the request;
# that number is what the user is shown.
PROBE_CHARS_PER_TOKEN = 4.0

# Ordinary words, not hex: a recall target like "9f3a71c2" invites tokenizer
# mangling and would produce a wrong verdict for a reason that has nothing to do
# with the context window. 48^3 = 110,592 combinations is far more than enough to
# stop a cached or echoed response from faking a pass.
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

# Room for the system prompt, the JSON envelope, the chat template's own tokens
# and the answer. Without it a probe sized exactly to the window would overrun it
# by construction and fail every time.
_HEADROOM_TOKENS = 2048


def new_passphrase(rng: random.Random | None = None) -> str:
    """Three hyphenated words, fresh per test run."""
    source = rng or random.Random()
    return "-".join(source.sample(_WORDS, 3))


def build_probe(
    window_tokens: int, passphrase: str
) -> tuple[str, dict[str, object]]:
    """(system_instructions, user_payload) for a prompt of the declared size."""
    budget_tokens = max(256, window_tokens - _HEADROOM_TOKENS)
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
