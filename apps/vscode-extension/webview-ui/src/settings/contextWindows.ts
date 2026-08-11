/**
 * Starter context windows, keyed by lowercase model-name substring — the same
 * matching shape as `_is_reasoning_model` in openai_compatible_transport.py.
 *
 * This table PRE-FILLS a field the user is expected to correct. It is not a
 * capability lookup and must never grow into one: NVIDIA NIM's /v1/models returns
 * only id/object/created/owned_by, and NIM accepts a 600k-token prompt with HTTP
 * 200, so there is nothing to detect the real window from.
 *
 * Two rules for editing it:
 *   1. Verify every entry against the vendor's model card before committing it.
 *      A plausible wrong number is worse than an obvious default, because it
 *      stops the user looking.
 *   2. When unsure, declare the SMALLER number. Too small costs one early
 *      summarization call; too large can make the agent silently answer nothing
 *      (measured on NIM: 600,058 tokens in, completion_tokens: 1, no error).
 *
 * First match wins, so specific keys precede general ones.
 */
export const DEFAULT_CONTEXT_WINDOW = 128_000;

export const CONTEXT_WINDOWS: readonly (readonly [string, number])[] = [
  // Anthropic's own docs (platform.claude.com/docs/en/build-with-claude/context-windows,
  // verified 2026-08-11): most current models are 200k, but Opus 5/4.6-4.8, Sonnet 5,
  // Sonnet 4.6, Fable 5 and Mythos are 1M. A bare "claude" substring can't tell those
  // apart, so this deliberately declares the smaller 200k per rule 2 above — it undercounts
  // the 1M models rather than risk overcounting the 200k ones.
  ["claude", 200_000],
  // ai.google.dev/gemini-api/docs/gemini-3 (verified 2026-08-11): Gemini 3 Flash, 3 Flash
  // Preview and 3.1 Pro all report 1M input tokens — matches this repo's default Gemini
  // model (gemini-3-flash-preview) exactly.
  ["gemini", 1_000_000],
  // developers.openai.com/api/docs/models/gpt-4o (verified 2026-08-11): "128,000 context window".
  ["gpt-4o", 128_000],
  // huggingface.co/Qwen/Qwen3-32B model card (verified 2026-08-11): "32,768 natively" (131,072
  // with YaRN). Qwen3's native window; deployments that enable YaRN extension should raise
  // this by hand. Erring low per rule 2.
  ["qwen3", 32_768],
];

export function defaultContextWindow(model: string): number {
  const name = model.toLowerCase();
  for (const [key, tokens] of CONTEXT_WINDOWS) {
    if (name.includes(key)) return tokens;
  }
  return DEFAULT_CONTEXT_WINDOW;
}

// Mirrors the pydantic rails on ProviderSwapRequest.context_window (routes.py).
// Duplicated deliberately: the backend keeps its own check as defense in depth,
// but a violation there returns a 422 whose body HttpBackendClient.fetchJson
// discards, so the user would see only "Backend request failed (422)". Catching
// it here puts the reason next to the field they are typing in.
export const MIN_CONTEXT_WINDOW = 1024;
export const MAX_CONTEXT_WINDOW = 10_000_000;

/** null when the raw field value is an acceptable window, else why it is not. */
export function contextWindowError(raw: string): string | null {
  const tokens = Number(raw);
  if (!raw.trim() || !Number.isInteger(tokens)) return "Enter a whole number of tokens.";
  if (tokens < MIN_CONTEXT_WINDOW) {
    return `Too small — the minimum is ${MIN_CONTEXT_WINDOW.toLocaleString()} tokens.`;
  }
  if (tokens > MAX_CONTEXT_WINDOW) {
    return `Too large — the maximum is ${MAX_CONTEXT_WINDOW.toLocaleString()} tokens.`;
  }
  return null;
}
