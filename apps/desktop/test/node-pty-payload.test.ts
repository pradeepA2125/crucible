import { describe, expect, it } from "vitest";
// @ts-expect-error -- plain ESM module shared with build.mjs
import { nodePtyPayload } from "../node-pty-payload.mjs";

const has = (...paths: string[]) => (path: string) => paths.includes(path);

describe("node-pty files in the desktop build", () => {
  it("macOS uses the published prebuild and makes its spawn helper executable", () => {
    expect(nodePtyPayload("darwin", "arm64", has("prebuilds/darwin-arm64", "prebuilds/darwin-arm64/spawn-helper"))).toEqual({
      entries: ["lib", "package.json", "LICENSE", "prebuilds"], helper: "prebuilds/darwin-arm64/spawn-helper",
    });
  });

  it("Windows uses the published prebuild and has no spawn helper", () => {
    expect(nodePtyPayload("win32", "x64", has("prebuilds/win32-x64"))).toEqual({
      entries: ["lib", "package.json", "LICENSE", "prebuilds"], helper: null,
    });
  });

  it("Linux has no prebuild: it ships the module npm compiled into build/Release", () => {
    expect(nodePtyPayload("linux", "x64", has("build/Release", "build/Release/pty.node"))).toEqual({
      entries: ["lib", "package.json", "LICENSE", "build/Release"], helper: null,
    });
  });

  it("a compiled spawn helper is made executable too", () => {
    expect(nodePtyPayload("linux", "x64", has("build/Release", "build/Release/spawn-helper")).helper)
      .toBe("build/Release/spawn-helper");
  });

  it("no native module for this platform stops the build and says how to fix it", () => {
    expect(() => nodePtyPayload("linux", "arm64", has())).toThrow(
      "node-pty has no native module for linux-arm64 (neither prebuilds/linux-arm64 nor build/Release). Run: npm rebuild node-pty");
  });
});
