// `acquireVsCodeApi()` may only be called ONCE per webview lifetime — a second call
// throws. The chat webview now embeds the settings UI (floating overlay), so two
// entry modules (src/vscodeApi.ts and src/settings/vscodeApi.ts) each want a handle.
// Cache the instance on `window` so every caller shares the single acquired handle.
declare function acquireVsCodeApi<T = unknown>(): T;

interface PostMessageApi {
  postMessage(msg: unknown): void;
}

export function acquireVsCodeApiSingleton<T extends PostMessageApi>(): T {
  if (typeof acquireVsCodeApi !== "function") {
    // Outside the webview host (unit tests, SSR) — a harmless no-op stub.
    return { postMessage: () => {} } as unknown as T;
  }
  const w = window as unknown as { __vscodeApi?: T };
  return (w.__vscodeApi ??= acquireVsCodeApi<T>());
}
