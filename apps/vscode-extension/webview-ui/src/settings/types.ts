// Local mirror of src/settings-data.ts message protocol — the webview bundle never
// imports the extension's src/ (separate Vite bundle). Mirrors setup/types.ts's split.

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
  provider: { backend: string; model: string } | null;
  runtime: { releaseTag: string; components: Record<string, string> } | null;
  mcp: { enabled: boolean; servers: McpServerRow[] };
  skills: { name: string; description: string; enabled: boolean }[];
  envFlags: Record<string, string>;
  restartRequired: boolean;
}

// webview → host
export type SettingsInMsg =
  | { type: "settings/load" }
  | { type: "settings/setProvider"; backend: string; model: string; apiKey?: string }
  | { type: "settings/mcpUpsert"; name: string; entry: Record<string, unknown> }
  | { type: "settings/mcpDelete"; name: string }
  | { type: "settings/mcpToggle"; name: string; enabled: boolean }
  | { type: "settings/mcpReconnect"; name: string }
  | { type: "settings/skillToggle"; name: string; enabled: boolean }
  | { type: "settings/setEnvFlag"; key: string; value: string }
  | { type: "settings/restartBackend" };

// host → webview
export type SettingsOutMsg =
  | { type: "settings/state"; state: SettingsState }
  | { type: "settings/error"; message: string };

export interface ProviderInfo {
  id: string;
  label: string;
  local: boolean;
  keyEnvVar?: string;
  defaultModel: string;
}

// Mirror of src/setup-data.ts PROVIDERS (defaults from agentd/providers/factory.py).
export const PROVIDERS: ProviderInfo[] = [
  { id: "openai", label: "OpenAI", local: false, keyEnvVar: "OPENAI_API_KEY", defaultModel: "gpt-5" },
  { id: "anthropic", label: "Anthropic", local: false, keyEnvVar: "ANTHROPIC_API_KEY", defaultModel: "claude-3-5-sonnet-latest" },
  { id: "gemini", label: "Google Gemini", local: false, keyEnvVar: "GEMINI_API_KEY", defaultModel: "gemini-3-flash-preview" },
  { id: "groq", label: "Groq", local: false, keyEnvVar: "GROQ_API_KEY", defaultModel: "openai/gpt-oss-120b" },
  { id: "ollama", label: "Ollama (local)", local: true, defaultModel: "glm-4.7-flash:latest" },
  { id: "watsonx", label: "IBM watsonx", local: false, keyEnvVar: "WATSONX_API_KEY", defaultModel: "ibm/granite-3-8b-instruct" },
  { id: "openrouter", label: "OpenRouter", local: false, keyEnvVar: "OPENROUTER_API_KEY", defaultModel: "stepfun/step-3.5-flash:free" },
  { id: "huggingface", label: "Hugging Face", local: false, keyEnvVar: "HF_TOKEN", defaultModel: "deepseek-ai/DeepSeek-R1:fastest" },
  { id: "turboquant", label: "TurboQuant (local)", local: true, defaultModel: "devstral-small-2:24b-q4_k_xl" },
];

// Env-flag settings the panel round-trips (spec §6.2 Policies + Memory sections).
export const ENV_FLAG_OPTIONS: { key: string; label: string; options: string[] }[] = [
  { key: "aiEditor.policy.shell", label: "Shell command policy", options: ["ask", "allow_all"] },
  { key: "aiEditor.policy.scope", label: "Scope-extension policy", options: ["ask", "strict", "auto"] },
  { key: "aiEditor.memory.enabled", label: "Memory harness", options: ["false", "true"] },
  { key: "aiEditor.memory.reranker", label: "Memory reranker", options: ["false", "true"] },
];
