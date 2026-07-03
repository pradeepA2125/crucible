// Local mirror of src/setup-data.ts message protocol — the webview bundle never
// imports the extension's src/ (separate Vite bundle).

export type SetupInMsg =
  | { type: "setup/install" }
  | { type: "setup/validate"; backend: string; model: string; apiKey?: string }
  | { type: "setup/save"; backend: string; model: string; apiKey?: string }
  | { type: "setup/openChat" };

export type SetupOutMsg =
  | { type: "setup/progress"; component: string; status: string; detail?: string }
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

export const COMPONENT_LABELS: Record<string, string> = {
  uv: "uv (Python manager)",
  agentd: "AI Editor backend",
  indexer: "Code indexer",
  ripgrep: "ripgrep",
  lsps: "Language servers",
};
