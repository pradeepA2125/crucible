import { createHash } from "node:crypto";
import { chmodSync, mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { ProjectTrustSaves, trustKindForPath } from "../src/project-trust-saves.js";

function workspace(): string {
  return mkdtempSync(join(tmpdir(), "crucible-trust-"));
}

describe("project trust saves", () => {
  it("maps covered paths to their kind", () => {
    const ws = workspace();
    expect(trustKindForPath(ws, join(ws, "AGENTS.md"))).toBe("agents_md");
    expect(trustKindForPath(ws, join(ws, ".crucible", "skills", "a", "SKILL.md"))).toBe("skills");
    expect(trustKindForPath(ws, join(ws, ".crucible", "skills", "a", "run.sh"))).toBeNull();
    expect(trustKindForPath(ws, join(ws, ".crucible", "mcp.json"))).toBe("mcp");
    expect(trustKindForPath(ws, join(ws, ".crucible", "approved-web-search.json"))).toBe("approved_web_search");
    expect(trustKindForPath(ws, join(ws, "src", "AGENTS.md"))).toBeNull();
    expect(trustKindForPath(ws, "/elsewhere/AGENTS.md")).toBeNull();
  });

  it("save of a covered file reports before hash", async () => {
    const ws = workspace();
    const file = join(ws, "AGENTS.md");
    writeFileSync(file, "use npm");
    const report = vi.fn(async () => true);
    const refresh = vi.fn(async () => {});
    const saves = new ProjectTrustSaves(ws, report, refresh);
    await saves.willSave(file);
    writeFileSync(file, "use pnpm");
    await saves.didSave(file);
    expect(report).toHaveBeenCalledWith("agents_md", createHash("sha256").update("use npm").digest("hex"));
    expect(refresh).toHaveBeenCalled();
  });

  it("a new file reports a null before hash, and other files report nothing", async () => {
    const ws = workspace();
    const report = vi.fn(async () => true);
    const saves = new ProjectTrustSaves(ws, report, async () => {});
    mkdirSync(join(ws, ".crucible"), { recursive: true });
    const mcp = join(ws, ".crucible", "mcp.json");
    await saves.willSave(mcp);
    writeFileSync(mcp, "{}");
    await saves.didSave(mcp);
    expect(report).toHaveBeenCalledWith("mcp", null);
    report.mockClear();
    const other = join(ws, "README.md");
    await saves.willSave(other);
    await saves.didSave(other);
    expect(report).not.toHaveBeenCalled();
  });

  it("an unreadable file before the save is never reported as new", async () => {
    const ws = workspace();
    const file = join(ws, "AGENTS.md");
    writeFileSync(file, "planted");
    chmodSync(file, 0);
    const report = vi.fn(async () => true);
    const saves = new ProjectTrustSaves(ws, report, async () => {});
    try {
      await saves.willSave(file);
      chmodSync(file, 0o644);
      writeFileSync(file, "planted + mine");
      await saves.didSave(file);
      expect(report).not.toHaveBeenCalled();
    } finally {
      chmodSync(file, 0o644);
    }
  });

  it("a skill's before hash is the whole set's fingerprint", async () => {
    const ws = workspace();
    const dir = join(ws, ".crucible", "skills", "a");
    mkdirSync(dir, { recursive: true });
    const skill = join(dir, "SKILL.md");
    writeFileSync(skill, "body");
    const report = vi.fn(async () => true);
    const saves = new ProjectTrustSaves(ws, report, async () => {});
    await saves.willSave(skill);
    await saves.didSave(skill);
    const pair = `.crucible/skills/a/SKILL.md\0${createHash("sha256").update("body").digest("hex")}`;
    expect(report).toHaveBeenCalledWith("skills", createHash("sha256").update(pair).digest("hex"));
  });
});
