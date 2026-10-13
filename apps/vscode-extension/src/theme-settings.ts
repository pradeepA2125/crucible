// vscode-free theme settings (spec 2026-10-13 §1). The webview mirrors these lists
// in webview-ui/src/theme.ts; test/theme-settings.test.ts guards the drift.

export const APPEARANCES = ["vscode", "dark", "light"] as const;
export const ACCENTS = ["blue", "violet", "ember"] as const;
export type Appearance = (typeof APPEARANCES)[number];
export type Accent = (typeof ACCENTS)[number];

export interface ThemeSettings {
  appearance: Appearance;
  accent: Accent;
}

export const DEFAULT_THEME: ThemeSettings = { appearance: "vscode", accent: "blue" };

export type ThemeMessage = { type: "themeChanged" } & ThemeSettings;

export function normalizeThemeSettings(raw: { appearance?: unknown; accent?: unknown }): ThemeSettings {
  const appearance = (APPEARANCES as readonly unknown[]).includes(raw.appearance)
    ? (raw.appearance as Appearance) : DEFAULT_THEME.appearance;
  const accent = (ACCENTS as readonly unknown[]).includes(raw.accent)
    ? (raw.accent as Accent) : DEFAULT_THEME.accent;
  return { appearance, accent };
}

/** Stamp the configured theme on <html> so the first paint is right; the webview's
 * theme.ts refines data-theme and data-tone from VS Code's body classes once it runs
 * (vscode mode's real tone and high contrast are only known there). Values come from
 * normalizeThemeSettings, so they are always one of the known literals. */
export function injectThemeAttributes(html: string, s: ThemeSettings): string {
  const tone = s.appearance === "light" ? "light" : "dark";
  const attrs = `data-appearance="${s.appearance}" data-accent="${s.accent}" data-theme="${s.appearance}" data-tone="${tone}"`;
  return html.replace(/<html(\s[^>]*)?>/, (_m, existing: string | undefined) => `<html${existing ?? ""} ${attrs}>`);
}

export class ThemeHub {
  private current: ThemeSettings = DEFAULT_THEME;
  private readonly listeners = new Set<(m: ThemeMessage) => void>();

  get settings(): ThemeSettings {
    return this.current;
  }

  update(next: ThemeSettings): void {
    if (next.appearance === this.current.appearance && next.accent === this.current.accent) return;
    this.current = next;
    const message: ThemeMessage = { type: "themeChanged", ...next };
    for (const post of this.listeners) post(message);
  }

  subscribe(post: (m: ThemeMessage) => void): () => void {
    this.listeners.add(post);
    return () => {
      this.listeners.delete(post);
    };
  }
}

/** One hub per extension host; every Crucible panel subscribes on creation. */
export const themeHub = new ThemeHub();
