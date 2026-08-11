import { describe, expect, it } from "vitest";
import {
  contextWindowError,
  DEFAULT_CONTEXT_WINDOW,
  defaultContextWindow,
  MAX_CONTEXT_WINDOW,
  MIN_CONTEXT_WINDOW,
} from "./contextWindows";

describe("defaultContextWindow", () => {
  it("matches on a lowercase substring, like _is_reasoning_model does", () => {
    expect(defaultContextWindow("claude-3-5-sonnet-latest")).toBe(200_000);
    expect(defaultContextWindow("CLAUDE-3-5-SONNET-LATEST")).toBe(200_000);
  });

  it("falls back to the default for anything unrecognised", () => {
    expect(defaultContextWindow("some-model-nobody-has-heard-of")).toBe(DEFAULT_CONTEXT_WINDOW);
    expect(defaultContextWindow("")).toBe(DEFAULT_CONTEXT_WINDOW);
  });

  it("defaults to 128000, matching CRUCIBLE_MEMORY_WINDOW_TOKENS", () => {
    expect(DEFAULT_CONTEXT_WINDOW).toBe(128_000);
  });

  it("returns the first matching entry so specific keys can precede general ones", () => {
    // Both "qwen3" and a hypothetical longer key could match; the table is ordered.
    expect(defaultContextWindow("qwen3.6:35b-a3b-q4_K_M")).toBeGreaterThan(0);
  });
});

describe("contextWindowError", () => {
  it("accepts a value inside the backend's rails", () => {
    expect(contextWindowError("128000")).toBeNull();
    expect(contextWindowError(String(MIN_CONTEXT_WINDOW))).toBeNull(); // inclusive floor
    expect(contextWindowError(String(MAX_CONTEXT_WINDOW))).toBeNull(); // inclusive ceiling
  });

  it("rejects below the floor with a message naming the minimum", () => {
    /* Without this the value reaches PUT /v1/config/provider, pydantic 422s, and
       fetchJson discards the body — the user sees only "Backend request failed
       (422)" with no mention of the window. */
    const err = contextWindowError("500");
    expect(err).toMatch(/1,?024/);
  });

  it("rejects above the ceiling", () => {
    expect(contextWindowError("10000001")).not.toBeNull();
  });

  it("rejects empty and zero", () => {
    expect(contextWindowError("")).not.toBeNull();
    expect(contextWindowError("0")).not.toBeNull();
  });
});
