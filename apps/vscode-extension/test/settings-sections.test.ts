import { describe, expect, it } from "vitest";
import { SETTINGS_SECTIONS, asSettingsSectionId } from "../src/settings-sections.js";

// The registry is the activity-bar tree's row model. Its ids MUST mirror the
// webview SectionId union (webview-ui/src/settings/sections/meta.ts) — this test
// is the drift guard for that cross-file enum (the repo's recurring `.min(1)` class).
describe("SETTINGS_SECTIONS registry", () => {
  it("lists the seven settings sections in nav order, each with a label", () => {
    expect(SETTINGS_SECTIONS.map((s) => s.id)).toEqual([
      "overview",
      "provider",
      "mcp",
      "skills",
      "instructions",
      "policies",
      "runtime",
    ]);
    for (const section of SETTINGS_SECTIONS) {
      expect(section.label.length).toBeGreaterThan(0);
    }
  });

  it("has unique ids", () => {
    const ids = SETTINGS_SECTIONS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

// Guards the command handler against a bad/absent section argument (the command is
// invoked both from the tree with a valid id and from the palette with none).
describe("asSettingsSectionId", () => {
  it("passes through a known section id", () => {
    expect(asSettingsSectionId("runtime")).toBe("runtime");
  });

  it("returns undefined for an unknown or non-string argument", () => {
    expect(asSettingsSectionId("bogus")).toBeUndefined();
    expect(asSettingsSectionId(undefined)).toBeUndefined();
    expect(asSettingsSectionId(42)).toBeUndefined();
  });
});
