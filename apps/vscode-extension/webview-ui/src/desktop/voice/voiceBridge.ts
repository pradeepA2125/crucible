export type VoiceRecognizer = "whisper-base" | "whisper-small";
export type VoiceSynthesizer = "system" | "pocket";
export type VoiceIntent = "auto" | "question" | "steer";
export type VoiceModelAsset = VoiceRecognizer | "pocket";

export interface VoiceStatus {
  available: boolean;
  reason?: string;
  installed: VoiceRecognizer[];
  tts: VoiceSynthesizer[];
  pocketAvailable: boolean;
}

export interface VoiceReceipt {
  message_id: string;
  turn_id: string;
  kind: "reply" | "steer" | "clarify";
  text: string;
  status: "answered" | "queued" | "incorporated" | "expired";
}

type SessionCommand = { sessionId: string };
type UtteranceCommand = SessionCommand & { utteranceId: string };
export type VoiceCommand =
  | { type: "status" | "cancelInstall" }
  | { type: "install" | "remove"; model: VoiceModelAsset }
  | { type: "start"; threadId: string; stt: VoiceRecognizer; tts: VoiceSynthesizer }
  | ({ type: "stop" | "interrupt" } & SessionCommand)
  | ({ type: "snapshot" } & UtteranceCommand)
  | ({ type: "decode"; pcm: ArrayBuffer; final: boolean } & UtteranceCommand)
  | ({ type: "submit"; text: string; intent: VoiceIntent } & UtteranceCommand)
  | ({ type: "decide"; text: string; silenceMs: number; finalCall: boolean } & UtteranceCommand)
  | ({ type: "commit"; decisionId: string } & UtteranceCommand)
  | ({ type: "trace"; event: string; data: Record<string, string | number | boolean | null> } & SessionCommand)
  | ({ type: "vad"; pcm: ArrayBuffer } & SessionCommand);

export type VoiceAction = "listen" | "reply" | "clarify" | "start_task" | "steer";
export interface VoiceDecision {
  decisionId: string;
  turn: "complete" | "cut_off" | "thinking";
  action: VoiceAction;
  say: string;
  request: string;
}
/** A refusal the host reports as data: nothing happened, so the session may act again. */
export type VoiceRefusal = "turn_active" | "turn_not_active" | "voice_agent_unavailable";

const ACTIONS: readonly VoiceAction[] = ["listen", "reply", "clarify", "start_task", "steer"];
const TURNS: readonly VoiceDecision["turn"][] = ["complete", "cut_off", "thinking"];

export function refusalOf(result: Record<string, unknown>): VoiceRefusal | undefined {
  const refused = result.refused;
  return refused === "turn_active" || refused === "turn_not_active" || refused === "voice_agent_unavailable"
    ? refused : undefined;
}

/** The backend's reason for a refusal (a provider sign-in or usage message), if it sent one. */
export function refusalMessageOf(result: Record<string, unknown>): string | undefined {
  const message = result.message;
  return typeof message === "string" && message.trim() ? message.trim() : undefined;
}

export function decisionOf(result: Record<string, unknown>): VoiceDecision | undefined {
  const { decisionId, turn, action, say, request } = result;
  if (typeof decisionId !== "string" || !decisionId || typeof say !== "string" || typeof request !== "string"
    || !TURNS.includes(turn as VoiceDecision["turn"]) || !ACTIONS.includes(action as VoiceAction)) return undefined;
  return { decisionId, turn: turn as VoiceDecision["turn"], action: action as VoiceAction, say, request };
}

export type VoiceEvent =
  | ({ type: "audio"; replyId: string; pcm: ArrayBuffer; sampleRate: number } & UtteranceCommand)
  | ({ type: "reply"; messageId: string; text: string; final: boolean } & UtteranceCommand)
  | ({ type: "receipt"; receipt: VoiceReceipt } & SessionCommand)
  | ({ type: "narrating" } & UtteranceCommand)
  | ({ type: "ended"; reason?: string } & SessionCommand)
  | { type: "error"; sessionId?: string; message: string };

export interface VoiceBridge {
  request(command: VoiceCommand): Promise<unknown>;
  subscribe(listener: (event: VoiceEvent) => void): () => void;
}

declare global {
  interface Window {
    crucibleVoice?: VoiceBridge;
  }
}

function object(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? value as Record<string, unknown> : {};
}

export async function voiceRequest(bridge: VoiceBridge, command: VoiceCommand): Promise<Record<string, unknown>> {
  const result = object(await bridge.request(command));
  if (typeof result.error === "string") throw new Error(result.error);
  return result;
}

export async function readVoiceStatus(bridge: VoiceBridge): Promise<VoiceStatus> {
  const status = await voiceRequest(bridge, { type: "status" });
  const installed = Array.isArray(status.installed) ? status.installed : [];
  const tts = Array.isArray(status.tts) ? status.tts : [];
  return {
    available: status.available === true,
    pocketAvailable: status.pocketAvailable === true,
    reason: typeof status.reason === "string" ? status.reason : undefined,
    installed: installed.filter((value): value is VoiceRecognizer => value === "whisper-base" || value === "whisper-small"),
    tts: tts.filter((value): value is VoiceSynthesizer => value === "system" || value === "pocket"),
  };
}
