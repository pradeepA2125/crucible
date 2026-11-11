// Headless voice smoke for a desktop build on its own platform (CI on Windows and Linux):
//   node scripts/voice/voice-smoke.mjs --helpers <dir with crucible-whisper> --models <download dir>
// 1. the CPU check passes; 2. the pinned Whisper base, VAD and Pocket ONNX files download and verify;
// 3. Pocket (sherpa-onnx, through the worker protocol) says a sentence; 4. whisper transcribes it back;
// 5. the VAD arm scores one second of it. Exits non-zero naming the failed step.
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream, createWriteStream, existsSync, readFileSync, statSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

const root = resolve(import.meta.dirname, "../..");
const desktop = join(root, "apps/desktop");
const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, index, all) =>
  value.startsWith("--") ? [...pairs, [value.slice(2), all[index + 1]]] : pairs, []));
if (!args.helpers || !args.models) {
  console.error("Usage: voice-smoke.mjs --helpers <dir> --models <dir>");
  process.exit(2);
}
const helper = join(resolve(args.helpers), process.platform === "win32" ? "crucible-whisper.exe" : "crucible-whisper");
const models = resolve(args.models);
const SENTENCE = "Open the controller loop file.";

/** The minimal environment the app gives its helpers (mirrors helperEnvironment in
 *  apps/desktop/src/voice/workers.ts), so the smoke proves they start the way users start them. */
function helperEnvironment(helpers) {
  if (process.platform === "win32") {
    const systemRoot = process.env.SystemRoot ?? process.env.windir ?? "C:\\Windows";
    const temp = process.env.TEMP ?? process.env.TMP;
    return { SystemRoot: systemRoot, windir: systemRoot, ...(temp ? { TEMP: temp, TMP: temp } : {}),
      PATH: `${helpers};${systemRoot}\\System32` };
  }
  return { PATH: "/usr/bin:/bin", LANG: "en_US.UTF-8", ...(process.env.TMPDIR ? { TMPDIR: process.env.TMPDIR } : {}) };
}
const helperEnv = helperEnvironment(resolve(args.helpers));

async function step(name, run) {
  const started = Date.now();
  try {
    const detail = await run();
    const summary = typeof detail === "string" ? `: ${detail}` : Buffer.isBuffer(detail) ? `: ${detail.length} bytes` : "";
    console.log(`ok   ${name} (${Date.now() - started} ms)${summary}`);
    return detail;
  } catch (error) {
    console.error(`FAIL ${name}: ${error instanceof Error ? error.message : error}`);
    process.exit(1);
  }
}

/** The app's own pins, read from assets.ts so CI can never test different files than it ships. */
function appPins() {
  const source = readFileSync(join(desktop, "src/voice/assets.ts"), "utf8");
  const revision = source.match(/const revision = "([0-9a-f]{40})"/)?.[1];
  const base = source.match(/"whisper-base": \{\s*file: "([^"]+)", bytes: (\d+),\s*sha256: "([0-9a-f]{64})"/);
  const vad = source.match(/export const VAD_MODEL = \{\s*file: "([^"]+)", bytes: (\d+),\s*sha256: "([0-9a-f]{64})",\s*url: "([^"]+)"/);
  if (!revision || !base || !vad) throw new Error("could not read the model pins from assets.ts");
  return [
    { path: base[1], bytes: Number(base[2]), sha256: base[3],
      url: `https://huggingface.co/ggerganov/whisper.cpp/resolve/${revision}/${base[1]}` },
    { path: vad[1], bytes: Number(vad[2]), sha256: vad[3], url: vad[4] },
  ];
}

async function sha256(path) {
  const hash = createHash("sha256");
  await pipeline(createReadStream(path), hash);
  return hash.digest("hex");
}

async function fetchVerified(file, directory) {
  const path = join(directory, file.path);
  if (!(existsSync(path) && statSync(path).size === file.bytes && await sha256(path) === file.sha256)) {
    const response = await fetch(file.url);
    if (!response.ok || !response.body) throw new Error(`${file.path}: HTTP ${response.status}`);
    await pipeline(Readable.fromWeb(response.body), createWriteStream(path));
  }
  if (statSync(path).size !== file.bytes || await sha256(path) !== file.sha256) throw new Error(`${file.path}: checksum mismatch`);
}

/** Starts a helper, waits for `ready`, sends one line and resolves with the first reply line. */
function ask(command, helperArgs, line) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, helperArgs, { stdio: ["pipe", "pipe", "ignore"], env: helperEnv, shell: false });
    let buffer = "", ready = false;
    const timer = setTimeout(() => { child.kill(); reject(new Error("helper timed out")); }, 120_000);
    child.on("error", reject);
    child.on("exit", code => { clearTimeout(timer); reject(new Error(`helper exited (${code}) before answering`)); });
    child.stdout.on("data", chunk => {
      buffer += chunk;
      for (let at = buffer.indexOf("\n"); at >= 0; at = buffer.indexOf("\n")) {
        const reply = buffer.slice(0, at).trimEnd();
        buffer = buffer.slice(at + 1);
        if (!ready && reply === "ready") { ready = true; child.stdin.write(`${line}\n`); continue; }
        if (ready && reply) { clearTimeout(timer); child.stdin.end(); child.kill(); resolvePromise(reply); return; }
      }
    });
  });
}

function resampleTo16k(pcm24k) {
  const input = new Int16Array(pcm24k.buffer, pcm24k.byteOffset, pcm24k.length / 2);
  const output = new Int16Array(Math.floor(input.length * 16_000 / 24_000));
  for (let index = 0; index < output.length; index += 1) {
    const at = index * 24_000 / 16_000, low = Math.floor(at), high = Math.min(low + 1, input.length - 1);
    output[index] = Math.round(input[low] + (input[high] - input[low]) * (at - low));
  }
  return Buffer.from(output.buffer);
}

await step("cpu check", () => {
  const result = spawnSync(helper, ["--check-cpu"], { encoding: "utf8", env: helperEnv, shell: false });
  if (result.error) throw new Error(`${helper} could not start: ${result.error.message}`);
  if (result.stdout.trim() !== "cpu ok") throw new Error(`answered ${JSON.stringify(result.stdout.trim())}, exit ${result.status}`);
  return "cpu ok";
});

const pocketPack = JSON.parse(readFileSync(join(desktop, "voice-native/pocket-onnx-assets.json"), "utf8"));
const pocketDir = join(models, "pocket");
const [baseModel, vadModel] = appPins();
await step("download pinned models", async () => {
  await mkdir(pocketDir, { recursive: true });
  await fetchVerified(baseModel, models);
  await fetchVerified(vadModel, models);
  for (const file of pocketPack.files) await fetchVerified(file, pocketDir);
  return `${pocketPack.files.length + 2} files verified`;
});

const pcm24k = await step("Pocket speaks through the worker protocol", async () => {
  const worker = createRequire(import.meta.url)(join(desktop, "dist/voice-pocket-worker.cjs"));
  const engine = await worker.createSherpaPocket(pocketDir);
  const audio = [];
  await new Promise((done, fail) => {
    const receive = worker.servePocket(engine, line => {
      const event = JSON.parse(line);
      if (event.type === "audio") {
        if (event.sampleRate !== 24_000) fail(new Error(`unexpected sample rate ${event.sampleRate}`));
        audio.push(Buffer.from(event.pcm, "base64"));
      } else if (event.type === "done") done();
      else if (event.type === "error") fail(new Error(event.error));
    });
    receive(JSON.stringify({ type: "speak", id: "smoke", text: SENTENCE }));
  });
  const pcm = Buffer.concat(audio);
  if (pcm.length < 24_000) throw new Error(`only ${pcm.length} bytes of audio`);
  return pcm;
});

const pcm16k = resampleTo16k(pcm24k);
await step("whisper transcribes Pocket's sentence", async () => {
  const reply = await ask(helper, ["--model", join(models, baseModel.path)], `smoke\t${pcm16k.toString("base64")}`);
  const [, status, ...text] = reply.split("\t");
  const transcript = text.join("\t");
  if (status !== "result" || !/controller/i.test(transcript)) throw new Error(`heard ${JSON.stringify(transcript || reply)}`);
  return transcript;
});

await step("VAD scores one second", async () => {
  const reply = await ask(helper, ["--vad", join(models, vadModel.path)], `vad\t${pcm16k.subarray(0, 32_000).toString("base64")}`);
  const [, status, probs = ""] = reply.split("\t");
  const values = probs.split(",").map(Number);
  if (status !== "result" || !values.length || values.some(value => !(value >= 0 && value <= 1))) throw new Error(`answered ${reply}`);
  return `${values.length} windows, peak ${Math.max(...values).toFixed(2)}`;
});
