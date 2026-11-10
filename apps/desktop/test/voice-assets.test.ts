import { mkdtemp, open, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VAD_MODEL, VoiceAssets } from "../src/voice/assets.js";

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
