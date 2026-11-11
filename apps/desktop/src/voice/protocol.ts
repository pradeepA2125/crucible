import { z } from "zod";

export const VOICE_REQUEST = "crucible:voice";
export const VOICE_EVENT = "crucible:voice-event";
const id = z.string().regex(/^[a-zA-Z0-9_-]{1,128}$/);
const model = z.enum(["whisper-base", "whisper-small"]);
const asset = z.enum(["whisper-base", "whisper-small", "pocket"]);
const session = { sessionId: id };
// A provider model id ("gpt-6-luna", "claude-haiku-5-5"); null means the automatic choice.
const voiceModel = z.string().regex(/^[A-Za-z0-9._:/-]{1,128}$/).nullable();
const utterance = { ...session, utteranceId: id };
const pcm = z.custom<ArrayBuffer>(value => value instanceof ArrayBuffer && value.byteLength > 0 &&
  value.byteLength <= 960_000 && value.byteLength % 2 === 0, "Expected at most 30 seconds of 16 kHz PCM audio.");
const vadPcm = z.custom<ArrayBuffer>(value => value instanceof ArrayBuffer && value.byteLength > 0 &&
  value.byteLength <= 32_000 && value.byteLength % 2 === 0, "Expected at most one second of 16 kHz PCM audio.");
export const voiceRequest = z.discriminatedUnion("type", [
  z.object({ type: z.literal("status") }).strict(),
  z.object({ type: z.literal("install"), model: asset }).strict(),
  z.object({ type: z.literal("cancelInstall") }).strict(),
  z.object({ type: z.literal("remove"), model: asset }).strict(),
  z.object({ type: z.literal("start"), threadId: id, stt: model, tts: z.enum(["system", "pocket", "none"]),
    voiceModel: voiceModel.optional() }).strict(),
  z.object({ type: z.literal("voiceModel"), ...session, model: voiceModel }).strict(),
  z.object({ type: z.literal("stop"), ...session }).strict(),
  z.object({ type: z.literal("interrupt"), ...session }).strict(),
  z.object({ type: z.literal("snapshot"), ...utterance }).strict(),
  z.object({ type: z.literal("decode"), ...utterance, pcm, final: z.boolean() }).strict(),
  z.object({ type: z.literal("submit"), ...utterance, text: z.string().trim().min(1).max(4000),
    intent: z.enum(["auto", "question", "steer"]) }).strict(),
  z.object({ type: z.literal("decide"), ...utterance, text: z.string().trim().min(1).max(4000),
    silenceMs: z.number().int().min(0).max(600_000), finalCall: z.boolean() }).strict(),
  z.object({ type: z.literal("commit"), ...utterance, decisionId: id }).strict(),
  z.object({ type: z.literal("vad"), ...session, pcm: vadPcm }).strict(),
  // Diagnostics from the renderer's speech detection: small, flat, local-only.
  z.object({ type: z.literal("trace"), ...session, event: z.string().regex(/^[a-z_]{1,32}$/),
    data: z.record(z.string().regex(/^[a-zA-Z]{1,32}$/),
      z.union([z.number().finite(), z.boolean(), z.string().max(1000), z.null()]))
      .refine(data => Object.keys(data).length <= 24, "Too many trace fields.") }).strict(),
]);
export type VoiceRequest = z.infer<typeof voiceRequest>;
