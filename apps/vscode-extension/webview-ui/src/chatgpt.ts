// Shared constants for "Use your ChatGPT plan" in the webview bundles (settings,
// setup, chat). Mirror of src/chatgpt-settings.ts — the webview never imports src/.

/** "Manage usage" opens ChatGPT's Usage page, where the user reviews usage and per-app
 * limits (UI/UX guidelines; URL confirmed from the ChatGPT UI, 2026-10-07). */
export const CHATGPT_USAGE_URL = "https://chatgpt.com/settings/usage?tab=overview";
