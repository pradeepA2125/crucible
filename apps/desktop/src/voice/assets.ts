import { createHash, randomUUID } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { lstat, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import pocket from "../../voice-native/pocket-assets.json" with { type: "json" };
import pocketOnnx from "../../voice-native/pocket-onnx-assets.json" with { type: "json" };

const revision = "5359861c739e955e79d9a303bcbc70fb988958b1";
const whisperSource = `https://huggingface.co/ggerganov/whisper.cpp/resolve/${revision}`;
const models = {
  "whisper-base": {
    file: "ggml-base.bin", bytes: 147951465,
    sha256: "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
  },
  "whisper-small": {
    file: "ggml-small.bin", bytes: 487601967,
    sha256: "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b",
  },
} as const;

/** Silero VAD (MIT) for whisper.cpp: tells speech from noise, so noise never holds a turn open. */
export const VAD_MODEL = {
  file: "ggml-silero-v6.2.0.bin", bytes: 885098,
  sha256: "2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987",
  url: "https://huggingface.co/ggml-org/whisper-vad/resolve/9ffd54a1e1ee413ddf265af9913beaf518d1639b/ggml-silero-v6.2.0.bin",
} as const;

export type VoiceModel = keyof typeof models;
export type VoiceAsset = VoiceModel | "pocket";
type VerifiedAsset = { bytes: number; sha256: string };

function checkedPath(path: string): string {
  if (!path.split("/").every(part => /^[a-zA-Z0-9_.-]+$/.test(part) && part !== "." && part !== "..")) {
    throw new Error("Invalid local voice asset path.");
  }
  return path;
}

/** A Pocket TTS model pack: the CoreML pack on Apple Silicon, the sherpa-onnx pack elsewhere. */
export type PocketPack = {
  id: string;
  engine: "coreml" | "onnx";
  /** Folder inside the pack that holds `files` ("" for the pack's own root). */
  root: string;
  files: { path: string; url: string; bytes: number; sha256: string }[];
  /** CC-BY credit written into the installed pack as ATTRIBUTION. */
  attribution?: string;
};

/** The Pocket pack this machine can run, if any. */
export function pocketPackFor(platform: string, arch: string): PocketPack | undefined {
  if (platform === "darwin" && arch === "arm64") {
    return {
      id: pocket.revision, engine: "coreml", root: pocket.relativeRoot,
      files: pocket.files.map(file => ({ ...file, url: `${pocket.source}/resolve/${pocket.revision}/${file.path}` })),
    };
  }
  if ((platform === "win32" || platform === "linux") && arch === "x64") {
    return { id: pocketOnnx.id, engine: "onnx", root: "", files: pocketOnnx.files, attribution: pocketOnnx.attribution };
  }
  return undefined;
}

async function matchSize(path: string, info: VerifiedAsset): Promise<boolean> {
  const stat = await lstat(path).catch(() => undefined);
  return stat?.isFile() === true && stat.size === info.bytes;
}

async function verifyFile(path: string, info: VerifiedAsset, signal: AbortSignal): Promise<void> {
  if (!(await matchSize(path, info))) throw new Error("Install the selected voice model first.");
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(path, { signal })) hash.update(chunk);
  if (hash.digest("hex") !== info.sha256) throw new Error("A voice model is damaged. Remove it and download it again.");
}

export class VoiceAssets {
  private pending: { controller: AbortController; settled: Promise<void> } | undefined;
  private readonly pocketPath: string;

  constructor(private readonly directory: string, private readonly fetchModel = fetch,
    private readonly pack: PocketPack | null = pocketPackFor(process.platform, process.arch) ?? null) {
    this.pocketPath = join(directory, `pocket-${checkedPath(pack?.id ?? "none")}`);
  }

  async installed(): Promise<VoiceModel[]> {
    // A speech model installed before the VAD existed shows Install once more, which adds it.
    if (!(await matchSize(join(this.directory, VAD_MODEL.file), VAD_MODEL))) return [];
    const keys = Object.keys(models) as VoiceModel[];
    const found = await Promise.all(keys.map(key => matchSize(join(this.directory, models[key].file), models[key])));
    return keys.filter((_key, index) => found[index]);
  }

  async pocketInstalled(): Promise<boolean> {
    const marker = await readFile(join(this.pocketPath, ".complete"), "utf8").catch(() => "");
    return this.pack !== null && marker === this.pack.id;
  }

  async verifiedPath(model: VoiceModel, signal: AbortSignal): Promise<string> {
    const info = models[model];
    const path = join(this.directory, info.file);
    await verifyFile(path, info, signal);
    return path;
  }

  async verifiedVadPath(signal: AbortSignal): Promise<string> {
    const path = join(this.directory, VAD_MODEL.file);
    await verifyFile(path, VAD_MODEL, signal);
    return path;
  }

  async verifiedPocketPath(signal: AbortSignal): Promise<string> {
    if (!(await this.pocketInstalled())) throw new Error("Download the Pocket TTS voice first.");
    for (const file of this.pack!.files) {
      await verifyFile(this.packFile(this.pocketPath, file.path), file, signal);
    }
    return this.pocketPath;
  }

  async install(asset: VoiceAsset): Promise<void> {
    if (this.pending) throw new Error("A voice download is already in progress.");
    const controller = new AbortController();
    let settled!: () => void;
    const completion = new Promise<void>(resolve => { settled = resolve; });
    this.pending = { controller, settled: completion };
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(30 * 60 * 1000)]);
    const partial = join(this.directory, `.${asset}.${randomUUID()}.partial`);
    try {
      await mkdir(this.directory, { recursive: true, mode: 0o700 });
      if (asset === "pocket") {
        if (await this.pocketInstalled()) return;
        await this.installPocket(partial, signal, controller);
        signal.throwIfAborted();
        await rename(partial, this.pocketPath);
      } else {
        await this.installVad(signal);
        const info = models[asset];
        try { await this.verifiedPath(asset, signal); return; }
        catch { signal.throwIfAborted(); }
        await this.download(`${whisperSource}/${info.file}`, partial, info, signal);
        signal.throwIfAborted();
        await rename(partial, join(this.directory, info.file));
      }
    } finally {
      try { await rm(partial, { recursive: true, force: true }); }
      finally { this.pending = undefined; settled(); }
    }
  }

  private async installVad(signal: AbortSignal): Promise<void> {
    try { await this.verifiedVadPath(signal); return; }
    catch { signal.throwIfAborted(); }
    const partial = join(this.directory, `.vad.${randomUUID()}.partial`);
    try {
      await this.download(VAD_MODEL.url, partial, VAD_MODEL, signal);
      signal.throwIfAborted();
      await rename(partial, join(this.directory, VAD_MODEL.file));
    } finally {
      await rm(partial, { force: true });
    }
  }

  cancel(): void { this.pending?.controller.abort(); }

  async cancelAndWait(): Promise<void> {
    const pending = this.pending;
    pending?.controller.abort();
    await pending?.settled;
  }

  async remove(asset: VoiceAsset): Promise<void> {
    if (this.pending) throw new Error("Wait for the current voice download to finish or cancel it.");
    const path = asset === "pocket" ? this.pocketPath : join(this.directory, models[asset].file);
    await rm(path, { recursive: asset === "pocket", force: true });
  }

  private async installPocket(partial: string, signal: AbortSignal, controller: AbortController): Promise<void> {
    const pack = this.pack;
    if (!pack) throw new Error("Pocket TTS is not available on this computer.");
    const files = pack.files.values();
    const downloadNext = async () => {
      for (let item = files.next(); !item.done; item = files.next()) {
        signal.throwIfAborted();
        const file = item.value;
        const target = this.packFile(partial, file.path);
        await mkdir(dirname(target), { recursive: true, mode: 0o700 });
        await this.download(file.url, target, file, signal);
      }
    };
    const results = await Promise.allSettled(Array.from({ length: 3 }, () => downloadNext().catch(error => {
      controller.abort(error);
      throw error;
    })));
    const failure = results.find(result => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
    if (pack.attribution) await writeFile(join(partial, "ATTRIBUTION"), `${pack.attribution}\n`, { mode: 0o600 });
    await writeFile(join(partial, ".complete"), pack.id, { mode: 0o600, flag: "wx" });
  }

  /** Bytes a Pocket install downloads (0 when this computer has no Pocket pack). */
  pocketDownloadBytes(): number {
    return this.pack?.files.reduce((total, file) => total + file.bytes, 0) ?? 0;
  }

  private packFile(base: string, path: string): string {
    const root = this.pack?.root ?? "";
    return root ? join(base, checkedPath(root), checkedPath(path)) : join(base, checkedPath(path));
  }

  private async download(url: string, path: string, info: VerifiedAsset, signal: AbortSignal): Promise<void> {
    const response = await this.fetchModel(url, { signal, credentials: "omit" });
    if (!response.ok || !response.body || new URL(response.url).protocol !== "https:") {
      await response.body?.cancel().catch(() => {});
      throw new Error("The voice model could not be downloaded.");
    }
    const hash = createHash("sha256");
    let received = 0;
    const stream = response.body;
    async function* verifiedChunks() {
      const reader = stream.getReader();
      try {
        while (true) {
          const next = await reader.read();
          if (next.done) break;
          received += next.value.byteLength;
          if (received > info.bytes) throw new Error("The voice download is larger than expected.");
          hash.update(next.value);
          yield next.value;
        }
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
    }
    await pipeline(Readable.from(verifiedChunks()), createWriteStream(path, { flags: "wx", mode: 0o600 }), { signal });
    if (received !== info.bytes || hash.digest("hex") !== info.sha256) throw new Error("The downloaded voice model failed verification.");
  }
}
