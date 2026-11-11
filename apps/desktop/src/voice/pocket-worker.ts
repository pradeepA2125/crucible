// Spoken replies off Apple Silicon: Pocket TTS through sherpa-onnx, served over the worker
// protocol. Runs as an Electron utility process (`--models <pack folder>`); the CI smoke loads
// this same bundle in plain Node and calls createSherpaPocket directly.
import { join } from "node:path";
import { servePocket, type PocketEngine } from "./pocket-protocol.js";

type Wave = { samples: Float32Array; sampleRate: number };
type SherpaTts = {
  sampleRate: number;
  generateAsync(request: {
    text: string; sid: number; speed: number; generationConfig: unknown;
    onProgress(info: { samples: Float32Array; progress: number }): boolean;
  }): Promise<unknown>;
};
type Sherpa = {
  OfflineTts: { createAsync(config: unknown): Promise<SherpaTts> };
  GenerationConfig: new (options: Record<string, unknown>) => unknown;
  readWave(path: string): Wave;
};

/** Loads the pinned Pocket ONNX pack and its reference voice (`voice.wav`). */
export async function createSherpaPocket(models: string): Promise<PocketEngine> {
  const sherpa = require("sherpa-onnx-node") as Sherpa;
  const tts = await sherpa.OfflineTts.createAsync({
    model: {
      pocket: {
        lmFlow: join(models, "lm_flow.int8.onnx"),
        lmMain: join(models, "lm_main.int8.onnx"),
        encoder: join(models, "encoder.onnx"),
        decoder: join(models, "decoder.int8.onnx"),
        textConditioner: join(models, "text_conditioner.onnx"),
        vocabJson: join(models, "vocab.json"),
        tokenScoresJson: join(models, "token_scores.json"),
        voiceEmbeddingCacheCapacity: 4,
      },
      numThreads: 2,
      provider: "cpu",
      debug: false,
    },
    maxNumSentences: 1,
  });
  const voice = sherpa.readWave(join(models, "voice.wav"));
  const generationConfig = new sherpa.GenerationConfig({
    referenceAudio: voice.samples, referenceSampleRate: voice.sampleRate, extra: { max_reference_audio_len: 10 },
  });
  return {
    sampleRate: tts.sampleRate,
    async generate(text, onChunk) {
      await tts.generateAsync({
        text, sid: 0, speed: 1, generationConfig,
        onProgress: ({ samples }) => onChunk(samples),
      });
    },
  };
}

type ParentPort = { on(type: "message", listener: (event: { data: unknown }) => void): void; postMessage(value: unknown): void };
const port = (process as unknown as { parentPort?: ParentPort }).parentPort;

if (port) {
  const index = process.argv.indexOf("--models");
  const models = index >= 0 ? process.argv[index + 1] : undefined;
  if (!models) process.exit(2);
  createSherpaPocket(models).then(engine => {
    const receive = servePocket(engine, line => port.postMessage(line));
    port.on("message", event => { if (typeof event.data === "string") receive(event.data); });
  }, () => process.exit(1));
}
