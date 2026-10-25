import type { BackendClientFactory } from "./controller.js";
import type { RuntimeCore } from "./runtime/runtime-core.js";
import { PROVIDER_KEY_ENV } from "./provider-credentials.js";
import { createClaudeDeps } from "./claude-host-deps.js";
import { createChatGPTDeps } from "./chatgpt-host-deps.js";
import type { SetupDeps } from "./setup-data.js";
export interface HostSetupOptions {
  runtime: RuntimeCore; workspace: string; clientFactory: BackendClientFactory;
  openChat(): void; onBackendUrl(url: string): void; openExternal(url: string): Promise<void>;
  openClaudeExternal?(url: string): Promise<void>;
  install?: SetupDeps["install"];
  start?: () => Promise<{ port: number; reused: boolean }>;
}
export function createHostSetupDeps(opts: HostSetupOptions): SetupDeps {
  const { runtime, workspace, clientFactory } = opts;
    return {
      localModels: () => runtime.localModels(),
      install: opts.install ?? (async (onProgress, localModels = true) => {
        await runtime.setLocalModels(localModels);
        return runtime.install(onProgress);
      }),
      validate: async (req) => {
        const url = runtime.backendUrl(workspace);
        if (!url) {
          return { ok: false, error: "Backend not started yet — click Save & Start." };
        }
        const result = await clientFactory(url).validateProvider(req);
        return {
          ok: result.ok,
          ...(result.model !== undefined ? { model: result.model } : {}),
          ...(result.error !== undefined ? { error: result.error } : {}),
          ...(result.jsonMode !== undefined ? { jsonMode: result.jsonMode } : {}),
          ...(result.warning !== undefined ? { warning: result.warning } : {}),
        };
      },
      saveAndStart: async (backend, model, apiKey, extraCredentials) => {
        await runtime.saveProvider(backend, model, apiKey, extraCredentials);
        const { port } = await (opts.start ?? (() => runtime.startForWorkspace(workspace)))();
        const url = runtime.backendUrl(workspace) ?? `http://127.0.0.1:${port}`;
        const result = await clientFactory(url).validateProvider({ backend, model });
        if (!result.ok) {
          throw new Error(result.error ?? "Provider validation failed after starting the backend.");
        }
        // The chat controller/settings provider must learn about this URL too — otherwise
        // a manually-run setup (e.g. the wizard didn't auto-open, or a retry after a failed
        // first attempt) leaves the controller pointed at the stale default backendBaseUrl
        // and every chat call fails with "fetch failed" even though the backend is healthy.
        opts.onBackendUrl(url);
        return {
          port,
          ...(result.jsonMode !== undefined ? { jsonMode: result.jsonMode } : {}),
          ...(result.warning !== undefined ? { warning: result.warning } : {}),
        };
      },
      startForChatGPT: async () => {
        // No model yet: the account's catalog is only reachable once signed in.
        await runtime.saveProvider("chatgpt", "");
        const { port } = await (opts.start ?? (() => runtime.startForWorkspace(workspace)))();
        const url = runtime.backendUrl(workspace) ?? `http://127.0.0.1:${port}`;
        opts.onBackendUrl(url);
        return { port };
      },
      startForClaude: async () => {
        await runtime.saveProvider("claude_subscription", "claude-sonnet-5-5");
        const { port } = await (opts.start ?? (() => runtime.startForWorkspace(workspace)))();
        const url = runtime.backendUrl(workspace) ?? `http://127.0.0.1:${port}`;
        opts.onBackendUrl(url);
        return { port };
      },
      claude: createClaudeDeps(runtime, () => {
        const url = runtime.backendUrl(workspace);
        if (!url) throw new Error("Backend not started yet.");
        return clientFactory(url);
      }, opts.openClaudeExternal ?? opts.openExternal),
      chatgpt: createChatGPTDeps(runtime, () => {
        const url = runtime.backendUrl(workspace);
        if (!url) throw new Error("Backend not started yet.");
        return clientFactory(url);
      }, opts.openExternal),
      openChat: () => opts.openChat(),
      keyEnvVar: (backend) => PROVIDER_KEY_ENV[backend],
      storedExtraEnvVars: (backend) => runtime.storedExtraEnvVars(backend),
    };
}
