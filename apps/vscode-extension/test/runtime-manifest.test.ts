import { describe, expect, it } from "vitest";
import { platformKey, sha256Hex, verifyChecksum } from "../src/runtime/manifest.js";

describe("platformKey", () => {
  it("maps the four supported targets", () => {
    expect(platformKey("darwin", "arm64")).toBe("darwin-arm64");
    expect(platformKey("darwin", "x64")).toBe("darwin-x64");
    expect(platformKey("linux", "x64")).toBe("linux-x64");
    expect(platformKey("win32", "x64")).toBe("win32-x64");
  });
  it("throws on unsupported combos", () => {
    expect(() => platformKey("linux", "arm64")).toThrow(/unsupported/i);
  });
});

describe("checksums", () => {
  it("sha256Hex matches a known vector", () => {
    expect(sha256Hex(Buffer.from("abc"))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
  it("verifyChecksum throws with both digests in the message", () => {
    expect(() => verifyChecksum(Buffer.from("abc"), "00".repeat(32)))
      .toThrow(/ba7816bf/);
  });
});
