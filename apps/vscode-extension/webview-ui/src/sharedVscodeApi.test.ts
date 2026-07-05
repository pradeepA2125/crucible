import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { acquireVsCodeApiSingleton } from "./sharedVscodeApi";

describe("acquireVsCodeApiSingleton", () => {
  beforeEach(() => {
    delete (window as unknown as { __vscodeApi?: unknown }).__vscodeApi;
    delete (globalThis as unknown as { acquireVsCodeApi?: unknown }).acquireVsCodeApi;
  });
  afterEach(() => {
    delete (window as unknown as { __vscodeApi?: unknown }).__vscodeApi;
    delete (globalThis as unknown as { acquireVsCodeApi?: unknown }).acquireVsCodeApi;
  });

  it("invokes the host acquireVsCodeApi only once across many calls", () => {
    // acquireVsCodeApi() throws if called twice per webview. The chat bundle now
    // embeds the settings UI, so two entry modules each want a handle — they must
    // share one. Simulate the host by installing a spy global.
    const api = { postMessage: vi.fn() };
    const spy = vi.fn(() => api);
    (globalThis as unknown as { acquireVsCodeApi: unknown }).acquireVsCodeApi = spy;

    const a = acquireVsCodeApiSingleton();
    const b = acquireVsCodeApiSingleton();

    expect(a).toBe(api);
    expect(b).toBe(api);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("returns a no-op stub when the host API is unavailable (tests/SSR)", () => {
    const api = acquireVsCodeApiSingleton();
    expect(() => api.postMessage({})).not.toThrow();
  });
});
