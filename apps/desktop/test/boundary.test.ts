import { describe, it, expect } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync, readFileSync, realpathSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MAX_MESSAGE_BYTES, parseEnvelope, parsePayload, type BridgeContext } from "../src/contract.js";
import { assetPath, assertSender, containedPath, externalURL, isAppDocument } from "../src/security.js";
import { JsonState, EncryptedSecrets } from "../src/store.js";
const context: BridgeContext = { v: 1, clientId: "client", viewId: "view", projectId: "project", threadId: "thread", processId: "process" };
const command = () => ({ ...context, sequence: 1, requestId: "request", payload: { type: "stopTurn" } });
describe("desktop IPC admission", () => {
  it.each(["clientId", "viewId", "projectId", "threadId", "processId"])("rejects stale %s before dispatch", key => {
    expect(() => parseEnvelope({ ...command(), [key]: "other" }, context, 0)).toThrow("Stale");
  });
  it("accepts one command and rejects duplicates, unknown types and malformed decisions", () => {
    expect(parseEnvelope(command(), context, 0).payload.type).toBe("stopTurn");
    expect(() => parseEnvelope(command(), context, 1)).toThrow("Duplicate");
    expect(() => parsePayload({ type: "exec", command: "echo" })).toThrow();
    expect(() => parsePayload({ type: "editDecision", threadId: "thread", decision: "accept" })).toThrow();
    expect(() => parsePayload({ type: "sendMessage", text: "hello", backendUrl: "http://localhost" })).toThrow();
  });
  it.each(["image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/tiff"])("accepts the composer's %s attachment format", mediaType => {
    expect(parsePayload({ type: "sendMessage", text: "", imageAttachments: [{ name: "image", mediaType, dataUrl: `data:${mediaType};base64,AA==`, bytes: 1 }] }).type).toBe("sendMessage");
  });
  it.each(["setup/save", "setup/validate", "settings/setProvider", "settings/testContextWindow"])("confines %s extra credentials to the selected provider", type => {
    const request = { type, backend: "watsonx", model: "", contextWindow: 4096 };
    if (type !== "settings/setProvider" && type !== "settings/testContextWindow") delete (request as Partial<typeof request>).contextWindow;
    for (const key of ["PYTHONPATH", "CRUCIBLE_TASK_SUBSYSTEM", "CRUCIBLE_OPENAI_COMPAT_BASE_URL"]) {
      expect(() => parsePayload({ ...request, extraCredentials: { [key]: "" } })).toThrow("Unsupported provider credential field");
    }
    expect(parsePayload({ ...request, extraCredentials: { WATSONX_SPACE_ID: "", WATSONX_URL: "https://example.com" } }).type).toBe(type);
    expect(parsePayload({ ...request, backend: "openai_compatible", extraCredentials: { CRUCIBLE_OPENAI_COMPAT_BASE_URL: "https://example.com/v1" } }).type).toBe(type);
    expect(() => parsePayload({ ...request, backend: "openai", extraCredentials: { WATSONX_URL: "" } })).toThrow("Unsupported provider credential field");
    expect(parsePayload({ ...request, backend: "openai", extraCredentials: {} }).type).toBe(type);
  });
  it("admits only per-key desktop flag values and refuses approval bypass", () => {
    for (const [key, value] of [["crucible.policy.shell", "allow_all"], ["crucible.policy.scope", "allow_all"], ["crucible.memory.enabled", "anything"], ["crucible.webFetch.enabled", "1"]]) {
      expect(() => parsePayload({ type: "settings/setEnvFlag", key, value })).toThrow("untrusted desktop");
    }
    for (const [key, value] of [["crucible.policy.shell", "ask"], ["crucible.policy.scope", "strict"], ["crucible.policy.scope", "auto"], ["crucible.memory.enabled", "false"], ["crucible.localModels", "true"]]) {
      expect(parsePayload({ type: "settings/setEnvFlag", key, value }).type).toBe("settings/setEnvFlag");
    }
  });
  it("admits the shared eight-image limit and refuses excess count or total bytes for sends and drafts", () => {
    const attachment = { name: "image", mediaType: "image/png", dataUrl: "data:image/png;base64,AA==", bytes: 1 };
    for (const type of ["sendMessage", "draft/save"]) {
      const payload = (images: unknown[]) => type === "sendMessage" ? { type, text: "", imageAttachments: images }
        : { type, requestId: "request", threadId: "thread", revision: 1, version: 0, draft: { text: "", imageAttachments: images, mentionedPaths: [] } };
      expect(parsePayload(payload(Array.from({ length: 8 }, () => attachment))).type).toBe(type);
      expect(() => parsePayload(payload(Array.from({ length: 9 }, () => attachment)))).toThrow();
      expect(() => parsePayload(payload(Array.from({ length: 4 }, () => ({ ...attachment, bytes: 8 * 1024 * 1024 }))))).toThrow("total size");
    }
    expect(() => parsePayload({ type: "draft/save", requestId: "request", threadId: "thread", revision: -1, draft: {} })).toThrow();
  });
  it("rejects an oversized envelope before schema dispatch", () => {
    expect(() => parseEnvelope({ ...command(), payload: { type: "sendMessage", text: "x".repeat(MAX_MESSAGE_BYTES) } }, context, 0)).toThrow("size limit");
  });
  it("keeps reactive diff opening available but admits no legacy task commands", () => {
    expect(parsePayload({ type: "viewDiffFile", path: "hello.py" }).type).toBe("viewDiffFile");
    expect(() => parsePayload({ type: "acceptTask", taskId: "task" })).toThrow();
  });
  it("rejects wrong windows, subframes, origin spoofs, and non-document URLs", () => {
    expect(() => assertSender({ id: 2, frameIsMain: true, url: "app://app/index.html" }, 1)).toThrow();
    expect(() => assertSender({ id: 1, frameIsMain: false, url: "app://app/index.html" }, 1)).toThrow();
    expect(isAppDocument("app://app.evil/index.html")).toBe(false);
    expect(isAppDocument("app://app/assets/index.js")).toBe(false);
    expect(externalURL("javascript:alert(1)")).toBeNull();
    expect(externalURL("https://user:password@example.com")).toBeNull();
    expect(externalURL("https://example.com/page")).toBe("https://example.com/page");
  });
});
describe("private storage and project assets", () => {
  it("confines files through canonical symlinks and rejects encoded separator traversal", async () => {
    const directory = mkdtempSync(join(tmpdir(), "crucible-boundary-"));
    try {
      const root = join(directory, "assets"); mkdirSync(root); writeFileSync(join(root, "index.html"), "test");
      writeFileSync(join(directory, "outside"), "outside"); symlinkSync(join(directory, "outside"), join(root, "escape"));
      expect(await assetPath(root, "app://app/index.html")).toBe(realpathSync(join(root, "index.html")));
      await expect(containedPath(root, "../outside")).rejects.toThrow();
      await expect(assetPath(root, "app://app/escape")).rejects.toThrow();
      await expect(assetPath(root, "app://app/a%2f..%2foutside")).rejects.toThrow();
      await expect(assetPath(root, "file:///index.html")).rejects.toThrow();
    } finally { rmSync(directory, { recursive: true }); }
  });
  it("distinguishes unset settings and refuses secret persistence when encryption is unavailable", async () => {
    const directory = mkdtempSync(join(tmpdir(), "crucible-store-"));
    try {
      const state = new JsonState(join(directory, "preferences.json"));
      expect(state.inspect("crucible.localModels")).toBeUndefined();
      await state.update("crucible.localModels", false);
      expect(state.inspect("crucible.localModels")).toEqual({ globalValue: false });
      const secrets = new EncryptedSecrets(state, { isEncryptionAvailable: () => false, encryptString: () => { throw new Error("must not run"); }, decryptString: () => { throw new Error("must not run"); } });
      await expect(secrets.store("provider", "")).rejects.toThrow("not saved");
      const plaintextFallback = new EncryptedSecrets(state, { isEncryptionAvailable: () => true, getSelectedStorageBackend: () => "basic_text", encryptString: () => { throw new Error("must not run"); }, decryptString: () => { throw new Error("must not run"); } });
      await expect(plaintextFallback.store("provider", "")).rejects.toThrow("not saved");
      expect(readFileSync(join(directory, "preferences.json"), "utf8")).not.toContain("provider");
    } finally { rmSync(directory, { recursive: true }); }
  });
});
