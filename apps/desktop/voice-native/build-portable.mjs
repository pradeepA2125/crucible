// Builds crucible-whisper (speech-to-text and Silero VAD) for the machine running it, on
// Windows, Linux or macOS. Apple Silicon releases keep using build.sh, which also builds the
// Swift speech helpers; this script is for platforms where only whisper is native.
import { spawnSync } from "node:child_process";
import { copyFile, mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const [outArg] = process.argv.slice(2);
if (!outArg) {
  console.error("Usage: node build-portable.mjs <output-directory>");
  process.exit(2);
}
const source = import.meta.dirname;
const out = resolve(outArg);
const build = await mkdtemp(join(tmpdir(), "crucible-whisper-build-"));
const exe = process.platform === "win32" ? "crucible-whisper.exe" : "crucible-whisper";

function run(command, args) {
  const result = spawnSync(command, args, { stdio: "inherit", shell: false });
  if (result.status !== 0) throw new Error(`${command} ${args[0]} failed (${result.status ?? result.signal})`);
}

try {
  const configure = ["-S", source, "-B", join(build, "whisper"), "-DCMAKE_BUILD_TYPE=Release"];
  if (process.env.CRUCIBLE_WHISPER_SOURCE_DIR) {
    configure.push(`-DFETCHCONTENT_SOURCE_DIR_WHISPER=${process.env.CRUCIBLE_WHISPER_SOURCE_DIR}`);
  }
  run("cmake", configure);
  run("cmake", ["--build", join(build, "whisper"), "--config", "Release", "--target", "crucible-whisper", "--parallel", "4"]);
  run("cmake", ["--install", join(build, "whisper"), "--config", "Release", "--prefix", join(build, "bin"),
    "--component", "VoiceWorker"]);
  await mkdir(out, { recursive: true });
  await copyFile(join(build, "bin", exe), join(out, exe));
  await copyFile(join(build, "bin", "LICENSE.whisper.cpp"), join(out, "LICENSE.whisper.cpp"));
} finally {
  await rm(build, { recursive: true, force: true });
}
