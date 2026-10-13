/** Theme resolution (spec 2026-10-13 §1). The host stamps data-appearance and
 * data-accent on <html> and sends themeChanged when the settings change; VS Code
 * sets vscode-light|vscode-dark|vscode-high-contrast|vscode-high-contrast-light on
 * <body> and swaps it live when the user changes editor theme. styles/themes.css
 * keys every token on the data-theme, data-tone and data-accent written here. */

export const APPEARANCES = ["vscode", "dark", "light"] as const;
export const ACCENTS = ["blue", "violet", "ember"] as const;
export type Appearance = (typeof APPEARANCES)[number];
export type Accent = (typeof ACCENTS)[number];
export type ThemeName = Appearance | "hc";
export type Tone = "dark" | "light";

export interface ResolvedTheme {
  theme: ThemeName;
  tone: Tone;
  accent: Accent;
}

interface ClassList {
  contains(cls: string): boolean;
}

export function isAppearance(v: unknown): v is Appearance {
  return typeof v === "string" && (APPEARANCES as readonly string[]).includes(v);
}

export function isAccent(v: unknown): v is Accent {
  return typeof v === "string" && (ACCENTS as readonly string[]).includes(v);
}

export function resolveTheme(appearance: Appearance, accent: Accent, body: ClassList): ResolvedTheme {
  // High contrast is an accessibility choice; it overrides the user's appearance and accent.
  if (body.contains("vscode-high-contrast-light")) return { theme: "hc", tone: "light", accent };
  if (body.contains("vscode-high-contrast")) return { theme: "hc", tone: "dark", accent };
  if (appearance === "vscode") {
    return { theme: "vscode", tone: body.contains("vscode-light") ? "light" : "dark", accent };
  }
  return { theme: appearance, tone: appearance, accent };
}

export function applyTheme(root: HTMLElement, t: ResolvedTheme): void {
  root.dataset.theme = t.theme;
  root.dataset.tone = t.tone;
  root.dataset.accent = t.accent;
}

function configured(root: HTMLElement): { appearance: Appearance; accent: Accent } {
  const appearance = root.dataset.appearance;
  const accent = root.dataset.accent;
  return {
    appearance: isAppearance(appearance) ? appearance : "vscode",
    accent: isAccent(accent) ? accent : "blue",
  };
}

/** Resolve now, then keep the root in sync with body-class changes and
 * themeChanged messages. Returns a disposer (used by tests). */
export function installThemeSync(doc: Document = document, win: Window = window): () => void {
  const root = doc.documentElement;
  let current = configured(root);
  const apply = () => applyTheme(root, resolveTheme(current.appearance, current.accent, doc.body.classList));
  apply();

  const observer = new MutationObserver(apply);
  observer.observe(doc.body, { attributes: true, attributeFilter: ["class"] });

  const onMessage = (event: MessageEvent) => {
    const m = event.data as { type?: unknown; appearance?: unknown; accent?: unknown } | null;
    if (!m || m.type !== "themeChanged" || !isAppearance(m.appearance) || !isAccent(m.accent)) return;
    current = { appearance: m.appearance, accent: m.accent };
    root.dataset.appearance = m.appearance;
    apply();
  };
  win.addEventListener("message", onMessage);

  return () => {
    observer.disconnect();
    win.removeEventListener("message", onMessage);
  };
}
