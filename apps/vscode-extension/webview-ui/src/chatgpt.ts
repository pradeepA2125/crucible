// Shared constants for "Use your ChatGPT plan" in the webview bundles (settings,
// setup, chat). Mirror of src/chatgpt-settings.ts — the webview never imports src/.

/** "Manage usage" opens ChatGPT settings, where usage and per-app limits live (UI/UX
 * guidelines). TODO(chatgpt-plan): the docs name "ChatGPT Settings → Usage" without a
 * URL — confirm the deep link in the Phase 0 spike before release. */
export const CHATGPT_USAGE_URL = "https://chatgpt.com/#settings";
