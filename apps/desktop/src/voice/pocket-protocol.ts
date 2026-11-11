// The spoken-reply worker protocol (voice-native/README.md, "Worker protocols"), served for an
// engine that turns text into float samples. Kept free of Electron and sherpa-onnx so it can be
// tested with a fake engine and reused by the CI smoke.

/** Text to speech: `onChunk` receives successive mono samples in [-1, 1] and returns false to stop. */
export interface PocketEngine {
  sampleRate: number;
  generate(text: string, onChunk: (samples: Float32Array) => boolean): Promise<void>;
}

const MAX_TEXT_BYTES = 4_096;
// Before base64; the host's parser accepts lines up to 350,000 characters of PCM.
const MAX_AUDIO_BYTES = 262_144;
const VALID_ID = /^[A-Za-z0-9._:-]{1,80}$/;

type Job = { id: string; cancelled: boolean };

function pcm16Chunks(samples: Float32Array): Buffer[] {
  const pcm = Buffer.alloc(samples.length * 2);
  for (let index = 0; index < samples.length; index += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[index] ?? 0));
    pcm.writeInt16LE(Math.round(clamped * 32767), index * 2);
  }
  const chunks: Buffer[] = [];
  for (let start = 0; start < pcm.length; start += MAX_AUDIO_BYTES) {
    chunks.push(pcm.subarray(start, start + MAX_AUDIO_BYTES));
  }
  return chunks;
}

/** Announces `ready`, then returns the handler for each incoming request line. One generation
 *  runs at a time; a new `speak` cancels the previous one, which still ends with `done`. */
export function servePocket(engine: PocketEngine, send: (line: string) => void): (line: string) => void {
  const emit = (event: Record<string, unknown>) => send(JSON.stringify(event));
  let current: Job | undefined;
  let queue: Promise<void> = Promise.resolve();
  emit({ type: "ready" });

  return line => {
    let message: unknown;
    try { message = JSON.parse(line); } catch { return; }
    if (!message || typeof message !== "object") return;
    const { type, id, text } = message as { type?: unknown; id?: unknown; text?: unknown };
    if (typeof id !== "string" || !VALID_ID.test(id)) return;
    if (type === "cancel") {
      if (current?.id === id) current.cancelled = true;
      return;
    }
    if (type !== "speak") return;
    const utterance = typeof text === "string" && Buffer.byteLength(text, "utf8") <= MAX_TEXT_BYTES ? text.trim() : "";
    if (!utterance) {
      emit({ type: "error", id, error: "The text must be 1 to 4,096 bytes." });
      return;
    }
    if (current) current.cancelled = true;
    const job: Job = { id, cancelled: false };
    current = job;
    queue = queue.then(async () => {
      try {
        if (!job.cancelled) {
          await engine.generate(utterance, samples => {
            if (job.cancelled) return false;
            for (const chunk of pcm16Chunks(samples)) {
              emit({ type: "audio", id, pcm: chunk.toString("base64"), sampleRate: engine.sampleRate });
            }
            return true;
          });
        }
        emit({ type: "done", id });
      } catch (error) {
        emit({ type: "error", id, error: error instanceof Error ? error.message : "Speech generation failed." });
      } finally {
        if (current === job) current = undefined;
      }
    });
  };
}
