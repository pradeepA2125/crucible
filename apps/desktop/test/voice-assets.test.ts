import { createHash } from "node:crypto";
import { mkdtemp, open, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { pocketPackFor, VAD_MODEL, VoiceAssets, type PocketPack } from "../src/voice/assets.js";
import onnxPack from "../voice-native/pocket-onnx-assets.json" with { type: "json" };

const directories: string[] = [];
afterEach(async () => { await Promise.all(directories.splice(0).map(path => rm(path, { recursive: true, force: true }))); });

async function directory() {
  const path = await mkdtemp(join(tmpdir(), "crucible-voice-assets-"));
  directories.push(path);
  return path;
}

/** A sparse file of the pinned size: enough for the size check, free to create. */
async function sized(path: string, bytes: number) {
  const file = await open(path, "w");
  await file.truncate(bytes);
  await file.close();
}

describe("voice assets: the Silero VAD model", () => {
  it("pins the v6.2.0 model by revision, size and checksum", () => {
    expect(VAD_MODEL).toEqual({
      file: "ggml-silero-v6.2.0.bin", bytes: 885098,
      sha256: "2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987",
      url: "https://huggingface.co/ggml-org/whisper-vad/resolve/9ffd54a1e1ee413ddf265af9913beaf518d1639b/ggml-silero-v6.2.0.bin",
    });
  });

  it("a speech model counts as installed only once the VAD model is there too", async () => {
    const root = await directory();
    const assets = new VoiceAssets(root);
    await sized(join(root, "ggml-base.bin"), 147951465);
    expect(await assets.installed()).toEqual([]);
    await sized(join(root, VAD_MODEL.file), VAD_MODEL.bytes);
    expect(await assets.installed()).toEqual(["whisper-base"]);
  });

  it("installing a speech model fetches the VAD model first", async () => {
    const root = await directory();
    const fetchModel = vi.fn(async (_url: string) => new Response("not a model", { status: 200 }));
    Object.defineProperty(Response.prototype, "url", { configurable: true, get: () => "https://huggingface.co/x" });
    try {
      await expect(new VoiceAssets(root, fetchModel as typeof fetch).install("whisper-base")).rejects.toThrow();
    } finally {
      delete (Response.prototype as { url?: string }).url;
    }
    expect(fetchModel.mock.calls[0]?.[0]).toBe(VAD_MODEL.url);
  });

  it("refuses a missing VAD model", async () => {
    const assets = new VoiceAssets(await directory());
    await expect(assets.verifiedVadPath(AbortSignal.timeout(5_000))).rejects.toThrow();
  });
});

describe("voice assets: the Pocket pack per platform", () => {
  const sha = (text: string) => createHash("sha256").update(text).digest("hex");
  const testPack: PocketPack = {
    id: "onnx-test", engine: "onnx", root: "", attribution: "Pocket TTS by Kyutai, CC BY 4.0.",
    files: [
      { path: "model.onnx", url: "https://example.test/model.onnx", bytes: 5, sha256: sha("model") },
      { path: "voice.wav", url: "https://example.test/voice.wav", bytes: 5, sha256: sha("voice") },
    ],
  };
  async function withFetch<T>(run: (fetchModel: typeof fetch) => Promise<T>): Promise<T> {
    const bodies: Record<string, string> = { "https://example.test/model.onnx": "model", "https://example.test/voice.wav": "voice" };
    const fetchModel = vi.fn(async (url: string) => new Response(bodies[url] ?? "", { status: bodies[url] ? 200 : 404 }));
    Object.defineProperty(Response.prototype, "url", { configurable: true, get: () => "https://example.test/x" });
    try { return await run(fetchModel as unknown as typeof fetch); } finally { delete (Response.prototype as { url?: string }).url; }
  }

  it("pocketPackFor picks CoreML on Apple Silicon, ONNX on Windows and Linux, nothing elsewhere", () => {
    expect(pocketPackFor("darwin", "arm64")?.engine).toBe("coreml");
    expect(pocketPackFor("win32", "x64")?.engine).toBe("onnx");
    expect(pocketPackFor("linux", "x64")?.engine).toBe("onnx");
    expect(pocketPackFor("darwin", "x64")).toBeUndefined();
    expect(pocketPackFor("linux", "arm64")).toBeUndefined();
    const coreml = pocketPackFor("darwin", "arm64")!;
    expect(coreml.files[0]?.url).toMatch(/^https:\/\/huggingface\.co\/FluidInference\/pocket-tts-coreml\/resolve\/c2e998e2/);
  });

  it("the shipped onnx pack matches the pinned table", async () => {
    expect(onnxPack.files).toHaveLength(9);
    expect(onnxPack.files.every(file => file.url.startsWith("https://huggingface.co/"))).toBe(true);
    expect(onnxPack.files.find(file => file.path === "voice.wav")?.sha256)
      .toBe("46264e83cb99115c3d210260e029117566d9c64f20266d10daa78107759ede3e");
    const assets = new VoiceAssets(await directory(), fetch, pocketPackFor("linux", "x64") ?? null);
    expect(assets.pocketDownloadBytes()).toBe(199_269_026);
    expect(new VoiceAssets(await directory(), fetch, null).pocketDownloadBytes()).toBe(0);
  });

  it("the onnx pack installs, verifies and writes its attribution", async () => {
    const root = await directory();
    await withFetch(async fetchModel => {
      const assets = new VoiceAssets(root, fetchModel, testPack);
      expect(await assets.pocketInstalled()).toBe(false);
      await assets.install("pocket");
      expect(await assets.pocketInstalled()).toBe(true);
      const path = await assets.verifiedPocketPath(AbortSignal.timeout(5_000));
      expect(await readFile(join(path, "voice.wav"), "utf8")).toBe("voice");
      expect(await readFile(join(path, "ATTRIBUTION"), "utf8")).toContain("CC BY 4.0");
    });
  });

  it("a pack without its completion marker is not installed", async () => {
    const root = await directory();
    await withFetch(async fetchModel => {
      const assets = new VoiceAssets(root, fetchModel, testPack);
      await assets.install("pocket");
      await rm(join(root, "pocket-onnx-test", ".complete"));
      expect(await assets.pocketInstalled()).toBe(false);
      await expect(assets.verifiedPocketPath(AbortSignal.timeout(5_000))).rejects.toThrow();
    });
  });

  it("a damaged pack file fails verification", async () => {
    const root = await directory();
    await withFetch(async fetchModel => {
      const assets = new VoiceAssets(root, fetchModel, testPack);
      await assets.install("pocket");
      await writeFile(join(root, "pocket-onnx-test", "model.onnx"), "modex");
      await expect(assets.verifiedPocketPath(AbortSignal.timeout(5_000))).rejects.toThrow("A voice model is damaged");
    });
  });
});
