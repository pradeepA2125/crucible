import { describe, expect, it } from "vitest";

import { CommandDecisionSchema } from "../src/contracts/task-contracts";

describe("CommandDecisionSchema", () => {
  it("parses a remember+prefix decision with ruleValue", () => {
    const d = CommandDecisionSchema.parse({
      approve: true,
      remember: true,
      scope: "prefix",
      ruleValue: "python -c",
    });
    expect(d.approve).toBe(true);
    expect(d.remember).toBe(true);
    expect(d.scope).toBe("prefix");
    expect(d.ruleValue).toBe("python -c");
  });

  it("defaults remember=false and scope=exact when only approve is given", () => {
    const d = CommandDecisionSchema.parse({ approve: false });
    expect(d.approve).toBe(false);
    expect(d.remember).toBe(false);
    expect(d.scope).toBe("exact");
    expect(d.ruleValue).toBeUndefined();
  });

  it("rejects an unknown scope", () => {
    expect(() =>
      CommandDecisionSchema.parse({ approve: true, scope: "wildcard" }),
    ).toThrow();
  });
});
