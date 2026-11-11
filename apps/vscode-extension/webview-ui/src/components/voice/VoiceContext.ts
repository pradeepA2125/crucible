import { createContext } from "react";
import type { VoiceModelAsset, VoiceStatus } from "../../desktop/voice/voiceBridge";
import type { VoiceSessionState } from "../../desktop/voice/voiceSession";
import type { VoiceTurn } from "../../desktop/voice/voiceTurns";

/** A transcript the session could not send on its own; the composer takes it as a draft. */
export interface VoiceReviewDraft { id: string; transcript: string }

/** A voice model download in flight, started by the first Voice press or Settings. */
export interface VoiceDownload { model: VoiceModelAsset; label: string }

/**
 * What the composer and the transcript need from the desktop's voice session. Provided by
 * the desktop shell only; the VS Code webview has no bridge, so the context stays undefined
 * there and the voice controls never render.
 */
export interface VoiceComposerModel {
  /** Local voice on this computer; undefined while the status loads or failed to load. */
  status: VoiceStatus | undefined;
  /** A session is connected (the composer control is the status pill). */
  active: boolean;
  starting: boolean;
  state: VoiceSessionState;
  pushToTalk: boolean;
  /** This session's turns the chat cannot show yet: in flight, or with a queued/expired receipt. */
  turns: readonly VoiceTurn[];
  /** Transcripts held for review: the composer takes each one as a draft and discards it. */
  reviews: readonly VoiceReviewDraft[];
  download: VoiceDownload | undefined;
  error: string;
  start(): void;
  end(): void;
  toggleMicrophone(): void;
  stopSpeaking(): void;
  sendNow(): void;
  beginHold(): void;
  cancelHold(): void;
  cancelDownload(): void;
  discardReview(id: string): void;
  /** A typed message or correction for the running session (intent decided by the voice agent). */
  submitText(text: string): Promise<void>;
}

export const VoiceContext = createContext<VoiceComposerModel | undefined>(undefined);
