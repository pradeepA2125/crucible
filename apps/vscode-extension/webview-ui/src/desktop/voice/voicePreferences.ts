/**
 * The voice choices made in Settings › Voice, shared with the conversation window through
 * localStorage (every desktop page is on the same origin). A missing or invalid value is the
 * default, never an error: voice must start with nothing saved at all.
 */
import type { VoiceRecognizer, VoiceStatus, VoiceSynthesizer } from "./voiceBridge";

export type MicrophoneMode = "hands-free" | "push-to-talk";

const KEYS = {
  recognizer: "crucible.voice.recognizer",
  synthesizer: "crucible.voice.synthesizer",
  microphone: "crucible.voice.microphone",
} as const;
/** The prefix the per-provider voice-model choice uses (voiceModelPreference.ts). */
const VOICE_MODEL_PREFIX = "crucible.voiceModel.";

const RECOGNIZERS: readonly VoiceRecognizer[] = ["whisper-small", "whisper-base"];
const SYNTHESIZERS: readonly VoiceSynthesizer[] = ["pocket", "system", "none"];
const MICROPHONE_MODES: readonly MicrophoneMode[] = ["hands-free", "push-to-talk"];

function read<T extends string>(key: string, allowed: readonly T[]): T | null {
  try {
    const stored = localStorage.getItem(key);
    return allowed.includes(stored as T) ? stored as T : null;
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Storage unavailable: the choice still applies where it was made.
  }
}

export const readRecognizer = () => read(KEYS.recognizer, RECOGNIZERS);
export const writeRecognizer = (value: VoiceRecognizer | null) => write(KEYS.recognizer, value);
export const readSynthesizer = () => read(KEYS.synthesizer, SYNTHESIZERS);
export const writeSynthesizer = (value: VoiceSynthesizer | null) => write(KEYS.synthesizer, value);
export const readMicrophoneMode = (): MicrophoneMode => read(KEYS.microphone, MICROPHONE_MODES) ?? "hands-free";
export const writeMicrophoneMode = (value: MicrophoneMode) => write(KEYS.microphone, value);

/** Whisper Small unless Base is the only model installed. */
export function defaultRecognizer(installed: readonly VoiceRecognizer[]): VoiceRecognizer {
  return installed.includes("whisper-small") || !installed.includes("whisper-base") ? "whisper-small" : "whisper-base";
}

/** The engine a session starts with now. With no choice, Pocket is the default wherever this
 *  computer supports it; until it is installed the system voice speaks, else text only. */
export function resolveSynthesizer(choice: VoiceSynthesizer | null, status: VoiceStatus): VoiceSynthesizer {
  if (choice === "none") return "none";
  const preferred: VoiceSynthesizer = choice ?? "pocket";
  if (status.tts.includes(preferred as Exclude<VoiceSynthesizer, "none">)) return preferred;
  return status.tts.includes("system") ? "system" : status.tts.includes("pocket") ? "pocket" : "none";
}

/** Pocket is wanted but not installed: the next session picks it up once downloaded. */
export function wantsPocket(choice: VoiceSynthesizer | null, status: VoiceStatus): boolean {
  return (choice === null || choice === "pocket") && status.pocketAvailable && !status.tts.includes("pocket");
}

export interface SynthesizerChoice { value: VoiceSynthesizer; label: string; installed: boolean }

/** The spoken-reply engines this machine supports: Pocket where it runs, the system voice
 *  where there is one, and text only everywhere. */
export function synthesizerChoices(status: VoiceStatus): SynthesizerChoice[] {
  const choices: SynthesizerChoice[] = [];
  if (status.pocketAvailable || status.tts.includes("pocket")) {
    choices.push({ value: "pocket", label: "Pocket TTS", installed: status.tts.includes("pocket") });
  }
  if (status.tts.includes("system")) choices.push({ value: "system", label: "macOS system voice", installed: true });
  choices.push({ value: "none", label: "Text only", installed: true });
  return choices;
}

/** Calls back with the key when another window (Settings) changes a voice preference. */
export function subscribeVoicePreferences(listener: (key: string) => void): () => void {
  const onStorage = (event: StorageEvent) => {
    const key = event.key;
    if (!key) return;
    if (Object.values(KEYS).includes(key as typeof KEYS[keyof typeof KEYS]) || key.startsWith(VOICE_MODEL_PREFIX)) listener(key);
  };
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}
