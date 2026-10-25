// Shared provider/model coordination. Platform settings and secret storage stay
// behind the runtime port; backend calls stay in the trusted host.
import type { BackendConfig, BackendTaskClient, ReasoningEffort } from "@crucible/editor-client";
import { buildModelOptions } from "./composer-models.js";
import { PROVIDERS } from "./setup-data.js";
import { PROVIDER_KEY_ENV } from "./provider-credentials.js";

export interface ComposerRuntimePort {
  chatgptRegistration(): string | undefined;
  claudeRegistration(): string | undefined;
  getProviderKey(backend: string): Promise<string | undefined>;
  saveProvider(backend: string, model: string): Promise<void>;
  saveContextWindow(tokens: number): Promise<void>;
  saveReasoningEffort(level: string | undefined): Promise<void>;
}

type ComposerClient = Pick<BackendTaskClient, "setProvider" | "listChatGPTModels" | "listClaudeModels"> & {
  getConfig(): Promise<Pick<BackendConfig, "provider">>;
};

export interface ComposerModelPorts {
  client(): ComposerClient;
  runtime: ComposerRuntimePort;
  refreshContextUsage(): Promise<void>;
  refreshModels(): Promise<void>;
  now?: () => number;
}

export function createComposerModelHost(ports: ComposerModelPorts) {
  const now = ports.now ?? Date.now;
  // What a hot-swap must send so the backend can build the transport even when its
  // process env predates this provider (factory.py: request credentials override
  // process env): the stored API key, or for the ChatGPT plan the registration id
  // (an id, never a token).
  const providerCredentials = async (backend: string): Promise<Record<string, string> | undefined> => {
    if (backend === "claude_subscription") {
      const registration = ports.runtime.claudeRegistration();
      return registration ? { CRUCIBLE_CLAUDE_REGISTRATION: registration } : undefined;
    }
    if (backend === "chatgpt") {
      const registration = ports.runtime.chatgptRegistration();
      return registration ? { CRUCIBLE_CHATGPT_REGISTRATION: registration } : undefined;
    }
    const key = await ports.runtime.getProviderKey(backend);
    const envVar = PROVIDER_KEY_ENV[backend];
    return envVar && key ? { [envVar]: key } : undefined;
  };

  // The signed-in account's model catalog for the composer menu. Cached briefly: every
  // modelList refresh would otherwise be a call to OpenAI. A failure (signed out, plan
  // usage off) just means the menu offers no plan models.
  let chatgptCatalogCache: { registration: string; at: number; models: { slug: string; displayName: string; contextWindow: number | null }[] } | null = null;
  let chatgptCatalogEpoch = 0;
  const invalidateChatgptCatalog = (): void => {
    chatgptCatalogEpoch += 1;
    chatgptCatalogCache = null;
  };
  const chatgptCatalog = async () => {
    const registration = ports.runtime.chatgptRegistration();
    if (!registration) return undefined;
    if (chatgptCatalogCache?.registration === registration
        && now() - chatgptCatalogCache.at < 5 * 60_000) {
      return chatgptCatalogCache.models;
    }
    const epoch = chatgptCatalogEpoch;
    try {
      const models = await ports.client().listChatGPTModels(registration);
      if (epoch !== chatgptCatalogEpoch || ports.runtime.chatgptRegistration() !== registration) {
        return undefined;
      }
      chatgptCatalogCache = { registration, at: now(), models };
      return models;
    } catch {
      return undefined;
    }
  };

  let claudeCatalogCache: { registration: string; at: number; models: { slug: string; displayName: string; contextWindow: number | null }[] } | null = null;
  let claudeCatalogEpoch = 0;
  const claudeCatalog = async () => {
    const registration = ports.runtime.claudeRegistration();
    if (!registration) return undefined;
    if (claudeCatalogCache?.registration === registration && now() - claudeCatalogCache.at < 5 * 60_000) {
      return claudeCatalogCache.models;
    }
    const epoch = claudeCatalogEpoch;
    try {
      const models = (await ports.client().listClaudeModels(registration)).filter(m => m.contextWindow !== null);
      if (epoch !== claudeCatalogEpoch || ports.runtime.claudeRegistration() !== registration) return undefined;
      claudeCatalogCache = { registration, at: now(), models };
      return models;
    } catch { return undefined; }
  };

  // Composer model quick-swap: the current provider + the set of providers with a
  // stored key (offered for hot-swap). Rebuilt after every swap.
  const composerModelState = async () => {
    const config = await ports.client().getConfig();
    const keyed: string[] = [];
    for (const p of PROVIDERS) {
      if (p.keyEnvVar && (await ports.runtime.getProviderKey(p.id)) !== undefined) keyed.push(p.id);
    }
    const current = config.provider ?? null;
    const [chatgptModels, claudeModels] = await Promise.all([chatgptCatalog(), claudeCatalog()]);
    return {
      current,
      // Drives "Using ChatGPT plan · Manage usage" beside the model chip (UI/UX guidelines).
      usesChatgptPlan: config.provider?.usesChatgptPlan ?? false,
      usesClaudePlan: current?.backend === "claude_subscription",
      options: buildModelOptions(current, keyed, PROVIDERS, chatgptModels, claudeModels),
      effort: {
        level: config.provider?.reasoningEffort ?? null,
        support: config.provider?.reasoningEffortSupport ?? null,
        // Sourced from GET /v1/config so it rides EVERY modelList refresh (not
        // just the swap that produced it) — a plain listModels poll must not
        // clear it. See CLAUDE.md's reasoning-effort-note durability fix.
        note: config.provider?.reasoningEffortNote ?? null,
      },
    };
  };

  // Reasoning-effort dial quick-swap: mirrors the composer model swap above —
  // reads the persisted rung back into the SAME modelList shape so the webview
  // has one refresh path for both.
  const setReasoningEffort = async (level: ReasoningEffort | null) => {
    const config = await ports.client().getConfig();
    const backend = config.provider?.backend;
    if (!backend) throw new Error("no provider configured");
    // model and credentials are NOT optional here, for the same reasons the model
    // swap below passes them: ProviderRuntime.swap resolves `model or
    // resolve_model(backend)`, and resolve_model reads the PROCESS ENV — so
    // omitting the live model silently reverts a hot-swapped one, and on
    // openai_compatible (a backend spawned as something else has no
    // CRUCIBLE_OPENAI_COMPAT_MODEL in its env) it raises and the chip 400s.
    // credentials likewise: the running backend's env may predate this key.
    const credentials = await providerCredentials(backend);
    const model = config.provider?.model;
    const res = await ports.client().setProvider({
      backend,
      ...(model ? { model } : {}),
      reasoningEffort: level,
      ...(credentials ? { credentials } : {}),
    });
    // Persist what the backend ACTUALLY applied, not what was asked — a clamped
    // rung must not come back on the next managed spawn as the unclamped one.
    await ports.runtime.saveReasoningEffort(
      res.reasoningEffort !== undefined ? res.reasoningEffort ?? undefined : level ?? undefined,
    );
    // The note itself now comes from composerModelState() (sourced off GET
    // /v1/config), which is already current since setProvider just applied it —
    // no need to thread it through this response separately.
    return composerModelState();
  };

  const setModel = async (backend: string, model: string) => {
    const credentials = await providerCredentials(backend);
    // Resolve on the host from the signed-in account's cached catalog, not
    // from webview input. Unknown windows leave the user's setting intact.
    const catalogWindow = backend === "chatgpt"
      ? (await chatgptCatalog())?.find((m) => m.slug === model)?.contextWindow
      : undefined;
    // Match ProviderSwapRequest's sanity bounds; unusable catalog metadata
    // must not make an otherwise valid model swap fail validation.
    const contextWindow = typeof catalogWindow === "number"
      && Number.isSafeInteger(catalogWindow)
      && catalogWindow >= 1024 && catalogWindow <= 10_000_000
      ? catalogWindow : undefined;
    const result = await ports.client().setProvider({
      backend, model,
      ...(credentials ? { credentials } : {}),
      ...(contextWindow !== undefined ? { contextWindow } : {}),
    });
    await ports.runtime.saveProvider(backend, model);
    // A rejected swap must not change the next managed spawn's window either.
    const effectiveWindow = backend === "claude_subscription" ? result.contextWindow : contextWindow;
    if (effectiveWindow !== undefined) await ports.runtime.saveContextWindow(effectiveWindow);
    await ports.refreshContextUsage();
    return composerModelState();
  };

  const afterSettingsMutation = async (kind: "auth" | "provider"): Promise<void> => {
    if (kind === "auth") invalidateChatgptCatalog();
    claudeCatalogEpoch += 1;
    claudeCatalogCache = null;
    // Both reads are backend-authoritative; catalog windows never drive the meter.
    await Promise.all([ports.refreshContextUsage(), ports.refreshModels()]);
  };

  return { state: composerModelState, setModel, setReasoningEffort, afterSettingsMutation };
}
