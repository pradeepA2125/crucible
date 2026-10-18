import { describe, expect, it } from "vitest";
import { nextToolFilters } from "./mcpToolFilters";

describe("nextToolFilters", () => {
  it("edits the blocklist when there is no allowlist", () => {
    const off = nextToolFilters({ enabledTools: null, disabledTools: [] }, "b", false);
    expect(off).toEqual({ enabledTools: null, disabledTools: ["b"] });
    expect(nextToolFilters(off, "b", true)).toEqual({ enabledTools: null, disabledTools: [] });
  });

  it("keeps a hand-written allowlist an allowlist", () => {
    expect(nextToolFilters({ enabledTools: ["a", "b"], disabledTools: [] }, "b", false))
      .toEqual({ enabledTools: ["a"], disabledTools: [] });
    expect(nextToolFilters({ enabledTools: ["a"], disabledTools: [] }, "c", true))
      .toEqual({ enabledTools: ["a", "c"], disabledTools: [] });
  });

  it("checking a tool hidden by both lists shows it", () => {
    expect(nextToolFilters({ enabledTools: ["a"], disabledTools: ["c"] }, "c", true))
      .toEqual({ enabledTools: ["a", "c"], disabledTools: [] });
  });

  it("returns sorted, deduplicated lists", () => {
    expect(nextToolFilters({ enabledTools: null, disabledTools: ["z", "b"] }, "b", false))
      .toEqual({ enabledTools: null, disabledTools: ["b", "z"] });
  });
});
