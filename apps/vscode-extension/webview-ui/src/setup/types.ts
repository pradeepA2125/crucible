import type { SettingsInMsg } from "../settings/types";
// Local mirror of src/setup-data.ts message protocol — the webview bundle never
// imports the extension's src/ (separate Vite bundle).

export type SetupInMsg =
  | { type: "setup/install" }
  | { type: "setup/validate"; backend: string; model: string; apiKey?: string; extraCredentials?: Record<string, string> }
  | { type: "setup/save"; backend: string; model: string; apiKey?: string; extraCredentials?: Record<string, string> }
  | { type: "setup/openChat" }
  | { type: "setup/startForChatGPT" }
  // The embedded ChatGPT plan panel posts the settings/chatgpt* messages; the setup host
  // runs the same handler (src/chatgpt-settings.ts).
  | SettingsInMsg;

export type SetupOutMsg =
  | { type: "setup/progress"; component: string; status: string; detail?: string }
  | { type: "setup/installDone"; ok: boolean }
  | { type: "setup/validateResult"; ok: boolean; model?: string; error?: string; jsonMode?: string; warning?: string }
  | { type: "setup/ready"; port: number; jsonMode?: string; warning?: string }
  | { type: "setup/error"; message: string }
  | { type: "setup/chatgptReady"; hasAccounts: boolean };

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
  /** Signs in with an account instead of an API key ("Continue with ChatGPT"). */
  signIn?: "chatgpt";
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
  // Signs in instead of taking a key: the backend owns the OAuth tokens and the model
  // list comes from the account (spec 2026-10-06). No default model.
  { id: "chatgpt", label: "ChatGPT plan", local: false, signIn: "chatgpt", defaultModel: "" },
];

export const COMPONENT_LABELS: Record<string, string> = {
  uv: "uv (Python manager)",
  agentd: "Crucible backend",
  indexer: "Code indexer",
  ripgrep: "ripgrep",
  "rust-analyzer": "Rust language server",
  gopls: "Go language server",
  jre: "Java runtime (JRE 21)",
  jdtls: "Java language server",
  lsps: "Python/TS language servers",
};
