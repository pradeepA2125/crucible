import { createHash, randomUUID } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { lstat, mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import pocket from "../../voice-native/pocket-assets.json" with { type: "json" };

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

export type VoiceModel = keyof typeof models;
export type VoiceAsset = VoiceModel | "pocket";
type VerifiedAsset = { bytes: number; sha256: string };

function checkedPath(path: string): string {
  if (!path.split("/").every(part => /^[a-zA-Z0-9_.-]+$/.test(part) && part !== "." && part !== "..")) {
    throw new Error("Invalid local voice asset path.");
  }
  return path;
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

  constructor(private readonly directory: string, private readonly fetchModel = fetch) {
    this.pocketPath = join(directory, `pocket-${checkedPath(pocket.revision)}`);
  }

  async installed(): Promise<VoiceModel[]> {
    const keys = Object.keys(models) as VoiceModel[];
    const found = await Promise.all(keys.map(key => matchSize(join(this.directory, models[key].file), models[key])));
    return keys.filter((_key, index) => found[index]);
  }

  async pocketInstalled(): Promise<boolean> {
    const marker = await readFile(join(this.pocketPath, ".complete"), "utf8").catch(() => "");
    return marker === pocket.revision;
  }

  async verifiedPath(model: VoiceModel, signal: AbortSignal): Promise<string> {
    const info = models[model];
    const path = join(this.directory, info.file);
    await verifyFile(path, info, signal);
    return path;
  }

  async verifiedPocketPath(signal: AbortSignal): Promise<string> {
    if (!(await this.pocketInstalled())) throw new Error("Download the Pocket TTS voice first.");
    for (const file of pocket.files) {
      await verifyFile(join(this.pocketPath, checkedPath(pocket.relativeRoot), checkedPath(file.path)), file, signal);
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
    const files = pocket.files.values();
    const downloadNext = async () => {
      for (let item = files.next(); !item.done; item = files.next()) {
        signal.throwIfAborted();
        const file = item.value;
        const relative = checkedPath(file.path);
        const target = join(partial, checkedPath(pocket.relativeRoot), relative);
        await mkdir(dirname(target), { recursive: true, mode: 0o700 });
        await this.download(`${pocket.source}/resolve/${pocket.revision}/${relative}`, target, file, signal);
      }
    };
    const results = await Promise.allSettled(Array.from({ length: 3 }, () => downloadNext().catch(error => {
      controller.abort(error);
      throw error;
    })));
    const failure = results.find(result => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
    await writeFile(join(partial, ".complete"), pocket.revision, { mode: 0o600, flag: "wx" });
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
