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
  }): Promise<{ ok: boolean; model?: string; error?: string }>;
  saveAndStart(backend: string, model: string, apiKey?: string): Promise<{ port: number }>;
  openChat(): void;
  keyEnvVar(backend: string): string | undefined; // PROVIDER_KEY_ENV mirror
}

// webview → host
export type SetupInMsg =
  | { type: "setup/install" }
  | { type: "setup/validate"; backend: string; model: string; apiKey?: string }
  | { type: "setup/save"; backend: string; model: string; apiKey?: string }
  | { type: "setup/openChat" };

// host → webview
export type SetupOutMsg =
  | { type: "setup/progress"; component: string; status: string; detail?: string | undefined }
  | { type: "setup/installDone"; ok: boolean }
  | { type: "setup/validateResult"; ok: boolean; model?: string; error?: string }
  | { type: "setup/ready"; port: number }
  | { type: "setup/error"; message: string };

export interface ProviderInfo {
  id: string;
  label: string;
  local: boolean;
  keyEnvVar?: string;
  defaultModel: string;
}

// Defaults mirror agentd/providers/factory.py::_DEFAULT_MODEL; key vars mirror
// PROVIDER_KEY_ENV (local providers have none). `scripted` is dev-only, hidden.
export const PROVIDERS: ProviderInfo[] = [
  { id: "openai", label: "OpenAI", local: false, keyEnvVar: "OPENAI_API_KEY", defaultModel: "gpt-5" },
  { id: "anthropic", label: "Anthropic", local: false, keyEnvVar: "ANTHROPIC_API_KEY", defaultModel: "claude-3-5-sonnet-latest" },
  { id: "gemini", label: "Google Gemini", local: false, keyEnvVar: "GEMINI_API_KEY", defaultModel: "gemini-3-flash-preview" },
  { id: "groq", label: "Groq", local: false, keyEnvVar: "GROQ_API_KEY", defaultModel: "openai/gpt-oss-120b" },
  { id: "ollama", label: "Ollama (local)", local: true, defaultModel: "glm-4.7-flash:latest" },
  { id: "watsonx", label: "IBM watsonx", local: false, keyEnvVar: "WATSONX_API_KEY", defaultModel: "ibm/granite-3-8b-instruct" },
  { id: "openrouter", label: "OpenRouter", local: false, keyEnvVar: "OPENROUTER_API_KEY", defaultModel: "stepfun/step-3.5-flash:free" },
  { id: "huggingface", label: "Hugging Face", local: false, keyEnvVar: "HF_TOKEN", defaultModel: "deepseek-ai/DeepSeek-R1:fastest" },
  { id: "turboquant", label: "TurboQuant (local)", local: true, defaultModel: "qwen3.6:35b-a3b-q4_K_M" },
];

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
          const credentials =
            envVar && msg.apiKey ? { [envVar]: msg.apiKey } : undefined;
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
          });
          return;
        }
        case "setup/save": {
          const { port } = await deps.saveAndStart(msg.backend, msg.model, msg.apiKey);
          post({ type: "setup/ready", port });
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
