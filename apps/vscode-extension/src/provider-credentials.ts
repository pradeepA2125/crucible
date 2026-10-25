// Same table as agentd/providers/factory.py::PROVIDER_KEY_ENV (local providers absent).
export const PROVIDER_KEY_ENV: Record<string, string> = {
  openai: "OPENAI_API_KEY",
  anthropic: "ANTHROPIC_API_KEY",
  gemini: "GEMINI_API_KEY",
  groq: "GROQ_API_KEY",
  openrouter: "OPENROUTER_API_KEY",
  watsonx: "WATSONX_API_KEY",
  huggingface: "HF_TOKEN",
  openai_compatible: "CRUCIBLE_OPENAI_COMPAT_API_KEY",
};

