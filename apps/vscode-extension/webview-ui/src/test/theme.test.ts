import { afterEach, describe, expect, it } from "vitest";
import { applyTheme, installThemeSync, resolveTheme } from "../theme";

const classes = (...names: string[]) => ({ contains: (c: string) => names.includes(c) });

describe("resolveTheme", () => {
  it("uses the configured appearance outside high contrast", () => {
    expect(resolveTheme("dark", "violet", classes("vscode-light"))).toEqual({ theme: "dark", tone: "dark", accent: "violet" });
    expect(resolveTheme("light", "blue", classes("vscode-dark"))).toEqual({ theme: "light", tone: "light", accent: "blue" });
  });

  it("takes the tone from the editor in vscode mode", () => {
    expect(resolveTheme("vscode", "blue", classes("vscode-light"))).toEqual({ theme: "vscode", tone: "light", accent: "blue" });
    expect(resolveTheme("vscode", "ember", classes("vscode-dark"))).toEqual({ theme: "vscode", tone: "dark", accent: "ember" });
  });

  it("resolves high contrast over any appearance and accent", () => {
    expect(resolveTheme("light", "ember", classes("vscode-high-contrast")).theme).toBe("hc");
    const hcLight = resolveTheme("dark", "violet", classes("vscode-high-contrast-light"));
    expect(hcLight).toEqual({ theme: "hc", tone: "light", accent: "violet" });
  });
});

describe("applyTheme and installThemeSync", () => {
  let dispose: (() => void) | null = null;
  afterEach(() => {
    dispose?.();
    dispose = null;
    document.body.className = "";
    for (const a of ["appearance", "accent", "theme", "tone"]) delete document.documentElement.dataset[a];
  });

  it("writes the three attributes on the root", () => {
    applyTheme(document.documentElement, { theme: "light", tone: "light", accent: "ember" });
    const d = document.documentElement.dataset;
    expect([d.theme, d.tone, d.accent]).toEqual(["light", "light", "ember"]);
  });

  it("reads host-stamped settings, defaulting to vscode/blue", () => {
    document.body.className = "vscode-dark";
    dispose = installThemeSync();
    expect(document.documentElement.dataset.theme).toBe("vscode");
    expect(document.documentElement.dataset.accent).toBe("blue");
  });

  it("re-resolves when the editor switches to high contrast", async () => {
    document.documentElement.dataset.appearance = "dark";
    document.body.className = "vscode-dark";
    dispose = installThemeSync();
    expect(document.documentElement.dataset.theme).toBe("dark");
    document.body.className = "vscode-high-contrast-light";
    await Promise.resolve(); // MutationObserver callbacks run as microtasks
    expect(document.documentElement.dataset.theme).toBe("hc");
    expect(document.documentElement.dataset.tone).toBe("light");
  });

  it("applies a themeChanged message and ignores malformed ones", () => {
    document.body.className = "vscode-dark";
    dispose = installThemeSync();
    window.dispatchEvent(new MessageEvent("message", { data: { type: "themeChanged", appearance: "light", accent: "violet" } }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.dataset.accent).toBe("violet");
    window.dispatchEvent(new MessageEvent("message", { data: { type: "themeChanged", appearance: "neon", accent: "pink" } }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
