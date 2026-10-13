import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import {
  ACCENTS, APPEARANCES, DEFAULT_THEME, ThemeHub, injectThemeAttributes, normalizeThemeSettings,
} from "../src/theme-settings.js";

describe("normalizeThemeSettings", () => {
  it("keeps valid values", () => {
    expect(normalizeThemeSettings({ appearance: "light", accent: "ember" })).toEqual({ appearance: "light", accent: "ember" });
  });
  it("falls back to defaults for unknown or missing values", () => {
    expect(normalizeThemeSettings({ appearance: "neon", accent: "pink" })).toEqual(DEFAULT_THEME);
    expect(normalizeThemeSettings({})).toEqual({ appearance: "vscode", accent: "blue" });
  });
});

describe("injectThemeAttributes", () => {
  it("stamps appearance, accent, theme and tone on the html tag", () => {
    const out = injectThemeAttributes('<!DOCTYPE html>\n<html lang="en">\n<head>', { appearance: "light", accent: "violet" });
    expect(out).toContain('<html lang="en" data-appearance="light" data-accent="violet" data-theme="light" data-tone="light">');
  });
  it("uses a dark tone as the first-paint guess in vscode mode", () => {
    const out = injectThemeAttributes("<html>", { appearance: "vscode", accent: "blue" });
    expect(out).toBe('<html data-appearance="vscode" data-accent="blue" data-theme="vscode" data-tone="dark">');
  });
});

describe("ThemeHub", () => {
  it("broadcasts changes to subscribers and stops after unsubscribe", () => {
    const hub = new ThemeHub();
    const post = vi.fn();
    const unsubscribe = hub.subscribe(post);
    hub.update({ appearance: "dark", accent: "ember" });
    expect(post).toHaveBeenCalledWith({ type: "themeChanged", appearance: "dark", accent: "ember" });
    unsubscribe();
    hub.update({ appearance: "light", accent: "ember" });
    expect(post).toHaveBeenCalledTimes(1);
  });
  it("does not broadcast an unchanged value", () => {
    const hub = new ThemeHub();
    const post = vi.fn();
    hub.subscribe(post);
    hub.update(DEFAULT_THEME);
    expect(post).not.toHaveBeenCalled();
    expect(hub.settings).toEqual(DEFAULT_THEME);
  });
});

// Drift guard: the webview keeps its own copy of these lists (separate bundle).
describe("host and webview theme lists match", () => {
  const webview = readFileSync(join(__dirname, "..", "webview-ui", "src", "theme.ts"), "utf8");
  const list = (name: string) =>
    JSON.parse(webview.match(new RegExp(`${name} = (\\[[^\\]]*\\]) as const`))![1]!.replace(/'/g, '"'));
  it("appearances", () => expect(list("APPEARANCES")).toEqual([...APPEARANCES]));
  it("accents", () => expect(list("ACCENTS")).toEqual([...ACCENTS]));
});

describe("themeReadyReply (panel remount replay)", () => {
  it("answers a webview's themeReady with the current theme", async () => {
    const { themeReadyReply } = await import("../src/theme-settings.js");
    const hub = new ThemeHub();
    hub.update({ appearance: "light", accent: "ember" });
    expect(themeReadyReply({ type: "themeReady" }, hub)).toEqual({ type: "themeChanged", appearance: "light", accent: "ember" });
  });
  it("ignores every other message", async () => {
    const { themeReadyReply } = await import("../src/theme-settings.js");
    expect(themeReadyReply({ type: "settings/load" }, new ThemeHub())).toBeNull();
    expect(themeReadyReply(null, new ThemeHub())).toBeNull();
  });
});
