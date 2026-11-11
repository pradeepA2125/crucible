import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChatMsg, ModelOption } from "../../types";
import { vscode } from "../../vscodeApi";
import type { VoiceComposerModel, VoiceDownload } from "../../components/voice/VoiceContext";
import { readVoiceStatus, voiceRequest, type VoiceBridge, type VoiceModelAsset, type VoiceStatus } from "./voiceBridge";
import { INITIAL_VOICE_STATE, VoiceSession, type VoiceSessionState } from "./voiceSession";
import { readVoiceModel } from "./voiceModelPreference";
import {
  defaultRecognizer, readMicrophoneMode, readRecognizer, readSynthesizer, resolveSynthesizer, subscribeVoicePreferences,
  wantsPocket,
} from "./voicePreferences";
import { unsettledVoiceTurns, voiceTurns, type VoiceTurn } from "./voiceTurns";

const DOWNLOAD_LABELS: Record<VoiceModelAsset, string> = {
  "whisper-small": "Whisper Small", "whisper-base": "Whisper Base", pocket: "Pocket TTS",
};

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "The voice request failed. Try again.";
}

/** Everything the chat already shows: voice turns, plus typed messages (a typed message sent
 *  during a session is saved as an ordinary message, so it settles by its words). */
function settledTurns(messages: ChatMsg[]): VoiceTurn[] {
  const typed = messages.filter(message => message.role === "user" && message.metadata?.voice !== "exchange" && message.metadata?.voice !== true)
    .map((message, index): VoiceTurn => ({ id: message.id ?? `typed-${index}`, text: message.content, final: true }));
  return [...voiceTurns(messages), ...typed];
}

interface Options {
  bridge: VoiceBridge | undefined;
  threadId: string;
  /** The conversation the session belongs to: a change ends it. */
  identity: string;
  /** The conversation is open and live: a session can start. */
  ready: boolean;
  messages: ChatMsg[];
}

/**
 * The desktop's voice session for the current conversation, as the composer and transcript
 * see it. Owns the VoiceSession, the status and model downloads, and the saved preferences
 * (voice model and microphone mode apply live; recognizer and spoken-reply engine at the
 * next start). Returns undefined without a bridge or a conversation.
 */
export function useVoiceSession({ bridge, threadId, identity, ready, messages }: Options): VoiceComposerModel | undefined {
  const alive = useRef(false);
  const session = useRef<VoiceSession>();
  const installing = useRef(false);
  const latest = useRef({ threadId, ready });
  latest.current = { threadId, ready };
  const [status, setStatus] = useState<VoiceStatus>();
  const [state, setState] = useState<VoiceSessionState>(INITIAL_VOICE_STATE);
  const [starting, setStarting] = useState(false);
  const [download, setDownload] = useState<VoiceDownload>();
  const [error, setError] = useState("");
  const [liveTurns, setLiveTurns] = useState<VoiceTurn[]>([]);
  const [pushToTalk, setPushToTalk] = useState(() => readMicrophoneMode() === "push-to-talk");
  const [backend, setBackend] = useState("");

  const refreshStatus = useCallback(async () => {
    if (!bridge) return undefined;
    try {
      const next = await readVoiceStatus(bridge);
      if (alive.current) setStatus(next);
      return next;
    } catch (failure) {
      if (alive.current) setError(errorText(failure));
      return undefined;
    }
  }, [bridge]);

  const endSession = useCallback(() => {
    session.current?.close();
    session.current = undefined;
    setState(INITIAL_VOICE_STATE);
  }, []);

  useEffect(() => {
    if (!bridge) return;
    alive.current = true;
    void refreshStatus();
    return () => {
      alive.current = false;
      endSession();
      if (installing.current) void voiceRequest(bridge, { type: "cancelInstall" }).catch(() => {});
    };
  }, [bridge, refreshStatus, endSession]);

  // The session belongs to one conversation: switching ends it and clears what it showed.
  useEffect(() => () => {
    endSession();
    setLiveTurns([]);
    setError("");
  }, [identity, endSession]);

  // The provider's catalog names the backend whose saved voice model applies.
  useEffect(() => {
    if (!bridge) return;
    vscode.postMessage({ type: "listModels" });
    const onMessage = (event: MessageEvent) => {
      const message = event.data;
      if (message?.type !== "modelList" || typeof message.current?.backend !== "string") return;
      const options: ModelOption[] = Array.isArray(message.options) ? message.options : [];
      setBackend(options.length || message.current.backend ? message.current.backend : "");
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [bridge]);

  // A choice made in Settings applies to the running session where the session can take it.
  useEffect(() => {
    session.current?.setVoiceModel(backend ? readVoiceModel(backend) : null);
  }, [backend]);
  useEffect(() => subscribeVoicePreferences(key => {
    if (key === "crucible.voice.microphone") {
      const enabled = readMicrophoneMode() === "push-to-talk";
      setPushToTalk(enabled);
      session.current?.setPushToTalk(enabled);
    } else if (backend && key === `crucible.voiceModel.${backend}`) {
      session.current?.setVoiceModel(readVoiceModel(backend));
    }
  }), [backend]);

  const install = useCallback(async (model: VoiceModelAsset) => {
    if (!bridge) return;
    installing.current = true;
    setDownload({ model, label: DOWNLOAD_LABELS[model] });
    try {
      await voiceRequest(bridge, { type: "install", model });
    } finally {
      installing.current = false;
      await refreshStatus();
      if (alive.current) setDownload(undefined);
    }
  }, [bridge, refreshStatus]);

  const start = useCallback(async () => {
    const { threadId: thread, ready: live } = latest.current;
    if (!bridge || !thread || !live || session.current || starting || installing.current || !status?.available) return;
    setStarting(true);
    setError("");
    setLiveTurns([]);
    let next: VoiceSession | undefined;
    try {
      let current = status;
      const stt = readRecognizer() ?? defaultRecognizer(current.installed);
      if (!current.installed.includes(stt)) {
        await install(stt);
        current = (await refreshStatus()) ?? current;
        if (!alive.current || !current.installed.includes(stt)) return;
      }
      const synthesizer = readSynthesizer();
      next = new VoiceSession(bridge, {
        state: patch => setState(previous => ({ ...previous, ...patch })),
        utterance: (utteranceId, text, decisionId, typed) => {
          setError("");
          setLiveTurns(previous => [...previous, {
            id: utteranceId, text, ...(decisionId ? { decisionId } : {}), ...(typed ? { typed } : {}),
          }].slice(-30));
        },
        reply: (utteranceId, reply, final) => setLiveTurns(previous => previous.map(turn =>
          turn.id === utteranceId ? { ...turn, reply, final } : turn)),
        receipt: receipt => setLiveTurns(previous => previous.map(turn =>
          turn.id === receipt.message_id ? { ...turn, receipt } : turn)),
        error: setError,
        ended: reason => {
          session.current = undefined;
          setState(INITIAL_VOICE_STATE);
          if (reason) setError(reason);
        },
      }, (sessionId, event, data) => {
        void bridge.request({ type: "trace", sessionId, event, data }).catch(() => {});
      });
      session.current = next;
      next.setPushToTalk(readMicrophoneMode() === "push-to-talk");
      await next.start(thread, stt, resolveSynthesizer(synthesizer, current), backend ? readVoiceModel(backend) : null);
      // Spoken replies download behind the session; the next session speaks with Pocket.
      if (wantsPocket(synthesizer, current)) {
        void install("pocket").catch(failure => { if (alive.current) setError(errorText(failure)); });
      }
    } catch (failure) {
      next?.close();
      if (alive.current && session.current === next) {
        session.current = undefined;
        setState(INITIAL_VOICE_STATE);
      }
      if (alive.current) setError(errorText(failure));
    } finally {
      if (alive.current) setStarting(false);
    }
  }, [bridge, status, starting, install, refreshStatus, backend]);

  const settled = useMemo(() => settledTurns(messages), [messages]);
  const turns = useMemo(() => unsettledVoiceTurns(settled, liveTurns), [settled, liveTurns]);
  const reviews = useMemo(() => state.reviews.map(review => ({ id: review.id, transcript: review.transcript })), [state.reviews]);

  return useMemo<VoiceComposerModel | undefined>(() => {
    if (!bridge || !threadId) return undefined;
    return {
      status, active: state.connected, starting, state, pushToTalk, turns, reviews, download, error,
      start: () => { void start(); },
      end: endSession,
      toggleMicrophone: () => {
        const current = session.current;
        if (!current) return;
        setError("");
        if (state.microphone === "off") void current.unmute();
        else current.mute();
      },
      stopSpeaking: () => session.current?.stopSpeaking(),
      sendNow: () => session.current?.sendNow(),
      beginHold: () => session.current?.beginHold(),
      cancelHold: () => session.current?.cancelHold(),
      cancelDownload: () => {
        void voiceRequest(bridge, { type: "cancelInstall" }).catch(failure => { if (alive.current) setError(errorText(failure)); });
      },
      discardReview: id => session.current?.discardReview(id),
      submitText: async text => {
        const current = session.current;
        if (!current) throw new Error("Start a voice session before sending.");
        setError("");
        try {
          await current.submitText(text, "auto");
        } catch (failure) {
          if (alive.current && session.current === current) setError(errorText(failure));
          throw failure;
        }
      },
    };
  }, [bridge, threadId, status, state, starting, pushToTalk, turns, reviews, download, error, start, endSession]);
}
