import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const pkg = JSON.parse(readFileSync(join(__dirname, "..", "package.json"), "utf8"));

describe("security-sensitive settings", () => {
  it("cannot be set from a workspace settings file (spec §3.9)", () => {
    const props = pkg.contributes.configuration.properties;
    for (const key of ["crucible.policy.shell", "crucible.policy.scope", "crucible.backendBaseUrl",
      "crucible.devSourcePath", "crucible.managedRuntime.enabled"]) {
      expect(props[key].scope).toBe("machine");
    }
  });
});
