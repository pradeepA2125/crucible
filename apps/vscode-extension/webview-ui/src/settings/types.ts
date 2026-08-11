// Local mirror of src/settings-data.ts message protocol — the webview bundle never
// imports the extension's src/ (separate Vite bundle). Mirrors setup/types.ts's split.
import type { SectionId } from "./sections/meta";

export interface McpServerRow {
  name: string;
  transport: string;
  enabledInFile: boolean;
  state: string;
  detail: string | null;
  toolCount: number;
  userEnabled: boolean;
}

export interface SettingsState {
  // contextWindow is the window compaction is using right now, read back from
  // GET /v1/config so the field shows what the process actually has, not what the
  // panel last sent.
  provider: { backend: string; model: string; contextWindow?: number | null | undefined } | null;
  // Non-fatal note from the last successful provider validate (e.g. an
  // openai_compatible endpoint that only supports json_object, not strict JSON
  // schema). null once no validate has produced one yet.
  providerWarning?: string | null;
  runtime: { releaseTag: string; components: Record<string, string> } | null;
  mcp: { enabled: boolean; servers: McpServerRow[] };
  skills: { name: string; description: string; enabled: boolean }[];
  envFlags: Record<string, string>;
  restartRequired: boolean;
}

// webview → host
export type SettingsInMsg =
  | { type: "settings/load" }
  | { type: "settings/setProvider"; backend: string; model: string; apiKey?: string; extraCredentials?: Record<string, string>; contextWindow?: number }
  // Explicit delete of a backend's stored API key — a blank API-key field means
  // "keep the stored key", so this is the only way to remove one.
  | { type: "settings/clearProviderKey"; backend: string }
  // Opt-in, expensive (~one full window of input tokens per call), never part of
  // a save. Carries the same credentials as a save so the user can test an
  // endpoint before committing to it.
  | { type: "settings/testContextWindow"; backend: string; model: string; contextWindow: number; apiKey?: string; extraCredentials?: Record<string, string> }
  | { type: "settings/mcpUpsert"; name: string; entry: Record<string, unknown> }
  | { type: "settings/mcpDelete"; name: string }
  | { type: "settings/mcpToggle"; name: string; enabled: boolean }
  | { type: "settings/mcpReconnect"; name: string }
  | { type: "settings/skillToggle"; name: string; enabled: boolean }
  | { type: "settings/setEnvFlag"; key: string; value: string }
  | { type: "settings/loadInstructions" }
  | { type: "settings/saveInstructions"; content: string }
  | { type: "settings/restartBackend" };

// host → webview
export type SettingsOutMsg =
  | { type: "settings/state"; state: SettingsState }
  | { type: "settings/instructions"; content: string; exists: boolean }
  | { type: "settings/error"; message: string }
  | { type: "settings/navigate"; section: SectionId }
  // Deliberately NOT folded into settings/state: a verdict about a value is not a
  // change to one, and it must not survive the next snapshot rebuild.
  | { type: "settings/contextTestResult"; result: { ok: boolean; recalled: boolean; promptTokens?: number | undefined; exact?: boolean | undefined; error?: string | undefined } };

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
  extraFields?: ExtraField[];
}

// Mirror of src/setup-data.ts PROVIDERS (defaults from agentd/providers/factory.py).
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

// Env-flag settings the panel round-trips (spec §6.2 Policies + Memory sections).
export const ENV_FLAG_OPTIONS: { key: string; label: string; options: string[] }[] = [
  { key: "crucible.policy.shell", label: "Shell command policy", options: ["ask", "allow_all"] },
  { key: "crucible.policy.scope", label: "Scope-extension policy", options: ["ask", "strict", "auto"] },
  { key: "crucible.memory.enabled", label: "Memory harness", options: ["false", "true"] },
  { key: "crucible.memory.reranker", label: "Memory reranker", options: ["false", "true"] },
];
