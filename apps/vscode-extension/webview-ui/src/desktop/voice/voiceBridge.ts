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
  | ({ type: "submit"; text: string; intent: VoiceIntent } & UtteranceCommand);

export type VoiceEvent =
  | ({ type: "audio"; replyId: string; pcm: ArrayBuffer; sampleRate: number } & UtteranceCommand)
  | ({ type: "reply"; messageId: string; text: string; final: boolean } & UtteranceCommand)
  | ({ type: "receipt"; receipt: VoiceReceipt } & SessionCommand)
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
