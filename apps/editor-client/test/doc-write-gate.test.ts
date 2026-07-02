import { describe, expect, it } from "vitest";
import { PendingGateSchema } from "../src/contracts/task-contracts";

describe("doc_write gate contract", () => {
  it("parses a kind=doc_write pending gate (a kind missing from the Zod enum makes the /live parse throw and the gate silently never renders)", () => {
    const gate = PendingGateSchema.parse({
      kind: "doc_write",
      payload: { path: "docs/a.md", exists: false, preview: "# hi" },
    });
    expect(gate.kind).toBe("doc_write");
  });
});
