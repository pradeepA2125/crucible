// vscode-free message handler for the first-run setup wizard (setup-panel.ts wires it
// to RuntimeManager + HttpBackendClient). Mirrors memory-data.ts's split.

export interface SetupDeps {
  install(
    onProgress: (p: { id: string; status: string; detail?: string }) => void,
  ): Promise<{ ok: boolean }>;
  validate(req: {
    backend: string;
    model?: string;
    credentials?: Record<string, string>;
  }): Promise<{ ok: boolean; model?: string; error?: string; jsonMode?: string; warning?: string }>;
  saveAndStart(
    backend: string,
    model: string,
    apiKey?: string,
    extraCredentials?: Record<string, string>,
  ): Promise<{ port: number; jsonMode?: string; warning?: string }>;
  openChat(): void;
  keyEnvVar(backend: string): string | undefined; // PROVIDER_KEY_ENV mirror
  /**
   * Env vars whose values are already in SecretStorage for this backend, from an
   * earlier run. Needed because a blank field means "keep the stored value" — a
   * guard that could not tell those apart would block a returning user who has
   * nothing left to type.
   */
  storedExtraEnvVars(backend: string): string[];
}

// webview → host
export type SetupInMsg =
  | { type: "setup/install" }
  | { type: "setup/validate"; backend: string; model: string; apiKey?: string; extraCredentials?: Record<string, string> }
  | { type: "setup/save"; backend: string; model: string; apiKey?: string; extraCredentials?: Record<string, string> }
  | { type: "setup/openChat" };

// host → webview
export type SetupOutMsg =
  | { type: "setup/progress"; component: string; status: string; detail?: string | undefined }
  | { type: "setup/installDone"; ok: boolean }
  | { type: "setup/validateResult"; ok: boolean; model?: string; error?: string; jsonMode?: string; warning?: string }
  | { type: "setup/ready"; port: number; jsonMode?: string; warning?: string }
  | { type: "setup/error"; message: string };

export interface ExtraField {
  envVar: string;
  label: string;
  placeholder?: string;
  optional?: boolean;
}

export interface ProviderInfo {
  id: string;
  label: string;
  local: boolean;
  keyEnvVar?: string;
  /** Cloud provider whose API key is genuinely optional (self-hosted endpoints). */
  keyOptional?: boolean;
  defaultModel: string;
  /** Additional required credential fields beyond the primary API key. */
  extraFields?: ExtraField[];
}

// Defaults mirror agentd/providers/factory.py::_DEFAULT_MODEL; key vars mirror
// PROVIDER_KEY_ENV (local providers have none). `scripted` is dev-only, hidden.
export const PROVIDERS: ProviderInfo[] = [
  { id: "openai", label: "OpenAI", local: false, keyEnvVar: "OPENAI_API_KEY", defaultModel: "gpt-5" },
  { id: "anthropic", label: "Anthropic", local: false, keyEnvVar: "ANTHROPIC_API_KEY", defaultModel: "claude-3-5-sonnet-latest" },
  { id: "gemini", label: "Google Gemini", local: false, keyEnvVar: "GEMINI_API_KEY", defaultModel: "gemini-3-flash-preview" },
  { id: "groq", label: "Groq", local: false, keyEnvVar: "GROQ_API_KEY", defaultModel: "openai/gpt-oss-120b" },
  { id: "ollama", label: "Ollama (local)", local: true, defaultModel: "glm-4.7-flash:latest" },
  {
    id: "watsonx",
    label: "IBM watsonx",
    local: false,
    keyEnvVar: "WATSONX_API_KEY",
    defaultModel: "openai/gpt-oss-120b",
    extraFields: [
      { envVar: "WATSONX_SPACE_ID", label: "Space ID", placeholder: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" },
      { envVar: "WATSONX_URL", label: "URL", placeholder: "https://us-south.ml.cloud.ibm.com" },
    ],
  },
  { id: "openrouter", label: "OpenRouter", local: false, keyEnvVar: "OPENROUTER_API_KEY", defaultModel: "stepfun/step-3.5-flash:free" },
  { id: "huggingface", label: "Hugging Face", local: false, keyEnvVar: "HF_TOKEN", defaultModel: "deepseek-ai/DeepSeek-R1:fastest" },
  { id: "turboquant", label: "TurboQuant (local)", local: true, defaultModel: "qwen3.6:35b-a3b-q4_K_M" },
  {
    id: "openai_compatible",
    label: "OpenAI-compatible",
    local: false,
    keyOptional: true,
    keyEnvVar: "CRUCIBLE_OPENAI_COMPAT_API_KEY",
    defaultModel: "",
    extraFields: [
      {
        envVar: "CRUCIBLE_OPENAI_COMPAT_BASE_URL",
        label: "Base URL",
        placeholder: "https://integrate.api.nvidia.com/v1",
      },
    ],
  },
];

/**
 * Config the wizard must have before it may spawn a backend, expressed as the
 * human labels of whatever is still missing (empty = safe to start).
 *
 * This exists because the wizard's save path is save -> SPAWN -> validate: an
 * incomplete provider is not caught by a failed validate, it kills the backend
 * at import. Live example — picking OpenAI-compatible without a Base URL made
 * agentd raise "CRUCIBLE_OPENAI_COMPAT_BASE_URL is required" from
 * build_transport, and the user saw a raw Python traceback where a field-level
 * message belonged. The Settings panel cannot do this (it validates BEFORE it
 * swaps, so a bad value never reaches a process); the wizard can, so the wizard
 * needs the guard.
 *
 * Deliberately does NOT check the API key: `keyOptional` and local providers
 * legitimately have none, and telling a stored key from an absent one needs a
 * SecretStorage read this pure function has no business doing. A missing key
 * still surfaces through the backend's own error.
 */
export function missingRequiredFields(
  backend: string,
  model: string,
  extraCredentials: Record<string, string> | undefined,
  storedEnvVars: string[],
): string[] {
  const provider = PROVIDERS.find((p) => p.id === backend);
  if (!provider) return []; // unknown to us — say nothing rather than guess
  const missing: string[] = [];
  if (!model.trim()) missing.push("Model");
  for (const field of provider.extraFields ?? []) {
    if (field.optional) continue;
    const supplied = extraCredentials?.[field.envVar]?.trim();
    if (!supplied && !storedEnvVars.includes(field.envVar)) missing.push(field.label);
  }
  return missing;
}

export function createSetupHandler(
  deps: SetupDeps,
  post: (msg: SetupOutMsg) => void,
): (msg: SetupInMsg) => Promise<void> {
  return async (msg: SetupInMsg): Promise<void> => {
    try {
      switch (msg.type) {
        case "setup/install": {
          const result = await deps.install((p) =>
            post({
              type: "setup/progress",
              component: p.id,
              status: p.status,
              detail: p.detail,
            }),
          );
          post({ type: "setup/installDone", ok: result.ok });
          return;
        }
        case "setup/validate": {
          const envVar = deps.keyEnvVar(msg.backend);
          const primaryCred = envVar && msg.apiKey ? { [envVar]: msg.apiKey } : undefined;
          const credentials = (primaryCred || msg.extraCredentials)
            ? { ...primaryCred, ...msg.extraCredentials }
            : undefined;
          const result = await deps.validate({
            backend: msg.backend,
            model: msg.model,
            ...(credentials ? { credentials } : {}),
          });
          post({
            type: "setup/validateResult",
            ok: result.ok,
            ...(result.model !== undefined ? { model: result.model } : {}),
            ...(result.error !== undefined ? { error: result.error } : {}),
            ...(result.jsonMode !== undefined ? { jsonMode: result.jsonMode } : {}),
            ...(result.warning !== undefined ? { warning: result.warning } : {}),
          });
          return;
        }
        case "setup/save": {
          // Refuse before saveAndStart, not after: it persists the provider and
          // spawns the backend before anything validates it, so an incomplete
          // config becomes a dead backend plus a stored setting that reproduces
          // the crash on every subsequent start.
          const missing = missingRequiredFields(
            msg.backend, msg.model, msg.extraCredentials,
            deps.storedExtraEnvVars(msg.backend),
          );
          if (missing.length > 0) {
            post({
              type: "setup/error",
              message:
                `${missing.join(" and ")} ${missing.length > 1 ? "are" : "is"} required for `
                + `${PROVIDERS.find((p) => p.id === msg.backend)?.label ?? msg.backend}. `
                + `Fill ${missing.length > 1 ? "them" : "it"} in and try again — `
                + "the backend cannot start without it.",
            });
            return;
          }
          const { port, jsonMode, warning } = await deps.saveAndStart(
            msg.backend, msg.model, msg.apiKey, msg.extraCredentials,
          );
          post({
            type: "setup/ready",
            port,
            ...(jsonMode !== undefined ? { jsonMode } : {}),
            ...(warning !== undefined ? { warning } : {}),
          });
          return;
        }
        case "setup/openChat":
          deps.openChat();
          return;
      }
    } catch (err) {
      post({
        type: "setup/error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };
}
