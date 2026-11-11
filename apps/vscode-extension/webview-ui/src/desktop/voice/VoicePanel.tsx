import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { BtnDanger, BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { CardShell } from "../../components/shared/CardShell";
import { readVoiceStatus, voiceRequest, type VoiceBridge, type VoiceIntent, type VoiceModelAsset, type VoiceReceipt, type VoiceRecognizer, type VoiceStatus, type VoiceSynthesizer } from "./voiceBridge";
import { INITIAL_VOICE_STATE, VoiceSession } from "./voiceSession";
import { heardLabel, voicePhase } from "./voiceStatus";
import { readVoiceModel, voiceModelChoices, writeVoiceModel } from "./voiceModelPreference";
import { unsettledVoiceTurns, voiceTurns, type VoiceTurn } from "./voiceTurns";
import type { ChatMsg, ModelOption } from "../../types";
import { vscode } from "../../vscodeApi";
import "./voice.css";

const RECOGNIZERS: ReadonlyArray<{ id: VoiceRecognizer; label: string }> = [
  { id: "whisper-small", label: "Whisper Small" },
  { id: "whisper-base", label: "Whisper Base" },
];
const SYNTHESIZERS: Record<VoiceSynthesizer, string> = {
  system: "macOS system voice",
  pocket: "Pocket TTS",
  none: "Text only",
};

/** The spoken-reply download, named the way each platform knows it. */
function pocketInstallLabel(status: VoiceStatus): string {
  if (status.pocketEngine !== "onnx") return "Install Pocket TTS";
  return `Install spoken replies (about ${Math.round(status.pocketDownloadBytes / 1e7) * 10} MB)`;
}
const RECEIPT_LABELS: Record<VoiceReceipt["status"], string> = {
  answered: "Answered",
  queued: "Correction queued",
  incorporated: "Correction reached the model",
  expired: "Correction expired",
};

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "The voice request failed. Try again.";
}

function initialRecognizer(installed: VoiceRecognizer[]) {
  return installed.includes("whisper-small") || !installed.includes("whisper-base") ? "whisper-small" : "whisper-base";
}

interface Props {
  bridge: VoiceBridge;
  threadId: string;
  id: string;
  /** The thread's saved messages: the voice conversation is read from them. */
  messages: ChatMsg[];
}

export function VoicePanel({ bridge, threadId, id, messages }: Props) {
  const alive = useRef(false);
  const session = useRef<VoiceSession>();
  const activeInstall = useRef(false);
  const [status, setStatus] = useState<VoiceStatus>();
  const [statusLoading, setStatusLoading] = useState(false);
  const [state, setState] = useState(INITIAL_VOICE_STATE);
  const [starting, setStarting] = useState(false);
  const [operation, setOperation] = useState<{ type: "install" | "remove"; model: VoiceModelAsset }>();
  const [error, setError] = useState("");
  const [stt, setStt] = useState<VoiceRecognizer>("whisper-small");
  const [tts, setTts] = useState<VoiceSynthesizer>("system");
  const [pushToTalk, setPushToTalk] = useState(false);
  // This session's turns before they are saved (live reply text, correction receipts).
  const [liveTurns, setLiveTurns] = useState<VoiceTurn[]>([]);
  // The chat is the record of the voice conversation; the panel shows only what it cannot
  // show yet: the turn in flight and corrections still waiting.
  const saved = useMemo(() => voiceTurns(messages), [messages]);
  const turns = useMemo(() => unsettledVoiceTurns(saved, liveTurns), [saved, liveTurns]);
  const [text, setText] = useState("");
  const [intent, setIntent] = useState<VoiceIntent>("auto");
  const [sending, setSending] = useState(false);
  // The provider's model catalog, from the same list the composer's model menu reads.
  const [catalog, setCatalog] = useState<{ backend: string; options: ModelOption[] }>({ backend: "", options: [] });
  const [voiceModel, setVoiceModel] = useState<string | null>(null);
  const transcript = useRef<HTMLDivElement>(null);

  function updateStatus(initial = false) {
    setStatusLoading(true);
    return readVoiceStatus(bridge).then(next => {
      if (!alive.current) return;
      setStatus(next);
      if (initial) {
        setStt(initialRecognizer(next.installed));
        setTts(next.tts.includes("system") ? "system" : next.tts.includes("pocket") ? "pocket" : "none");
      }
    }).catch(failure => {
      if (alive.current) setError(errorText(failure));
    }).finally(() => {
      if (alive.current) setStatusLoading(false);
    });
  }

  useEffect(() => {
    alive.current = true;
    void updateStatus(true);
    return () => {
      alive.current = false;
      session.current?.close();
      session.current = undefined;
      if (activeInstall.current) void voiceRequest(bridge, { type: "cancelInstall" }).catch(() => {});
    };
  }, [bridge]);

  useEffect(() => {
    vscode.postMessage({ type: "listModels" });
    const onMessage = (event: MessageEvent) => {
      const message = event.data;
      if (message?.type !== "modelList" || typeof message.current?.backend !== "string") return;
      setCatalog({ backend: message.current.backend, options: Array.isArray(message.options) ? message.options : [] });
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // The choice is per provider: a provider switch (even mid-session) brings back that
  // provider's own choice, never the previous provider's model.
  useEffect(() => {
    const saved = catalog.backend ? readVoiceModel(catalog.backend) : null;
    setVoiceModel(saved);
    session.current?.setVoiceModel(saved);
  }, [catalog.backend]);

  const chooseVoiceModel = (value: string) => {
    const model = value || null;
    setVoiceModel(model);
    if (catalog.backend) writeVoiceModel(catalog.backend, model);
    session.current?.setVoiceModel(model);
  };
  const modelChoices = voiceModelChoices(catalog.backend, catalog.options, voiceModel);

  useEffect(() => {
    const node = transcript.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns, state.partial]);

  const modelReady = status?.installed.includes(stt) ?? false;
  const synthReady = tts === "none" || (status?.tts.includes(tts) ?? false);
  const manualMode = pushToTalk;
  const synthOptions: VoiceSynthesizer[] = [...(status?.tts ?? [])];
  if (status?.pocketAvailable && !synthOptions.includes("pocket")) synthOptions.push("pocket");
  synthOptions.push("none");
  const offerPocket = !!status?.pocketAvailable && !status.tts.includes("pocket");
  const start = async () => {
    if (session.current || starting || !status?.available || !modelReady || !synthReady) return;
    setStarting(true);
    setError("");
    setState(INITIAL_VOICE_STATE);
    setSending(false);
    setLiveTurns([]);
    let next: VoiceSession | undefined;
    try {
      next = new VoiceSession(bridge, {
        state: patch => setState(previous => ({ ...previous, ...patch })),
        utterance: (utteranceId, message, decisionId) => {
          // A later turn went through: an error about an earlier one is no longer true.
          setError("");
          setLiveTurns(previous => [
            ...previous, { id: utteranceId, text: message, ...(decisionId ? { decisionId } : {}) },
          ].slice(-30));
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
        // Local diagnostics file only; a failed write is never the user's problem.
        void bridge.request({ type: "trace", sessionId, event, data }).catch(() => {});
      });
      session.current = next;
      next.setPushToTalk(pushToTalk);
      await next.start(threadId, stt, tts, voiceModel);
    } catch (failure) {
      next?.close();
      if (alive.current && session.current === next) {
        session.current = undefined;
        setState(INITIAL_VOICE_STATE);
        setError(errorText(failure));
      }
    } finally {
      if (alive.current) setStarting(false);
    }
  };

  const end = () => {
    session.current?.close();
    session.current = undefined;
    setState(INITIAL_VOICE_STATE);
    setSending(false);
  };

  const changeInstallation = async (type: "install" | "remove", model: VoiceModelAsset) => {
    if (operation || starting || state.connected) return;
    setOperation({ type, model });
    activeInstall.current = type === "install";
    setError("");
    try {
      await voiceRequest(bridge, { type, model });
    } catch (failure) {
      if (alive.current) setError(errorText(failure));
    } finally {
      activeInstall.current = false;
      if (alive.current) {
        await updateStatus();
        setOperation(undefined);
      }
    }
  };

  const cancelInstallation = () => {
    void voiceRequest(bridge, { type: "cancelInstall" }).catch(failure => {
      if (alive.current) setError(errorText(failure));
    });
  };

  const sendTyped = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = text.trim();
    const current = session.current;
    if (!message || !current || sending || state.capturing) return;
    setSending(true);
    setError("");
    try {
      await current.submitText(message, intent);
      if (alive.current && session.current === current) setText(previous => previous.trim() === message ? "" : previous);
    } catch (failure) {
      if (alive.current && session.current === current) setError(errorText(failure));
    } finally {
      if (alive.current && session.current === current) setSending(false);
    }
  };

  const toggleMic = () => {
    const current = session.current;
    if (!current) return;
    setError("");
    if (state.microphone === "off") void current.unmute();
    else current.mute();
  };

  return <section id={id} className="crucible-voice-panel" aria-label="Local voice">
    <CardShell icon="spark" title="Local voice" subtitle={state.connected ? undefined : "This conversation"}>
      <div className="crucible-voice-body">
        {state.connected ? <>
          <div className="crucible-voice-actions">
            <span role="status" className="crucible-voice-phase"
              data-listening={state.capturing || (!manualMode && state.microphone === "on")}>{voicePhase(state, manualMode)}</span>
            <label><span className="sr-only">Microphone mode</span>
              <select value={manualMode ? "hold" : "auto"} disabled={state.capturing} onChange={event => {
                const enabled = event.target.value === "hold";
                session.current?.setPushToTalk(enabled);
                setPushToTalk(enabled);
              }}>
                <option value="auto">Hands-free</option>
                <option value="hold">Push to talk</option>
              </select>
            </label>
            <label><span className="sr-only">Voice model</span>
              <select value={voiceModel ?? ""} disabled={!catalog.backend} onChange={event => chooseVoiceModel(event.target.value)}>
                {modelChoices.map(choice => <option key={choice.value} value={choice.value}>Voice model: {choice.label}</option>)}
              </select>
            </label>
            <BtnGhost onClick={toggleMic}>{state.microphone === "off" ? "Turn microphone on" : "Mute microphone"}</BtnGhost>
            {state.speaking && <BtnGhost onClick={() => session.current?.stopSpeaking()}>Stop speaking</BtnGhost>}
            <BtnDanger onClick={end}>End session</BtnDanger>
          </div>
          <div className="crucible-voice-actions">
            {manualMode ? <button type="button" className="crucible-voice-send crucible-voice-hold"
              disabled={state.microphone !== "on"}
              aria-label="Hold to talk, release to send"
              onPointerDown={event => {
                if (event.button !== 0 || !event.isPrimary) return;
                event.currentTarget.setPointerCapture(event.pointerId);
                session.current?.beginHold();
              }}
              onPointerUp={event => {
                if (event.currentTarget.hasPointerCapture(event.pointerId)) session.current?.sendNow();
              }}
              onLostPointerCapture={() => session.current?.cancelHold()}
              onKeyDown={event => {
                if (event.key !== " " && event.key !== "Enter") return;
                event.preventDefault();
                if (!event.repeat) session.current?.beginHold();
              }}
              onKeyUp={event => {
                if (event.key !== " " && event.key !== "Enter") return;
                event.preventDefault();
                session.current?.sendNow();
              }}
              onBlur={() => session.current?.cancelHold()}>Hold to talk</button>
              : state.capturing && <BtnGhost onClick={() => session.current?.sendNow()}>Send now</BtnGhost>}
            {state.playbackStopMs !== undefined && <span>Estimated playback stop {state.playbackStopMs} ms</span>}
          </div>
          {state.reviews.map(review => <div className="crucible-voice-actions" key={review.id}>
            <p role="status">Review what Crucible heard. “{review.transcript}”</p>
            {review.status !== "failed" && <BtnGhost disabled={state.capturing || review.status === "sending"}
              onClick={() => session.current?.sendReviewed(review.id)}>
              {review.status === "sending" ? "Sending…" : "Send transcript"}
            </BtnGhost>}
            <BtnGhost disabled={!!text || review.status === "sending"} onClick={() => {
              setText(review.transcript);
              session.current?.discardReview(review.id);
            }}>Edit in composer</BtnGhost>
            <BtnGhost disabled={review.status === "sending"}
              onClick={() => session.current?.discardReview(review.id)}>Dismiss</BtnGhost>
          </div>)}
          <form className="crucible-voice-form" onSubmit={sendTyped}>
            <label><span className="sr-only">Message or correction</span>
              <input type="text" placeholder="Type a message or correction" value={text} maxLength={4000}
                onChange={event => setText(event.target.value)} />
            </label>
            <label><span className="sr-only">Message intent</span>
              <select value={intent} onChange={event => setIntent(event.target.value as VoiceIntent)}>
                <option value="auto">Automatic</option>
                <option value="question">Question</option>
                <option value="steer">Correction</option>
              </select>
            </label>
            <button type="submit" className="crucible-voice-send" disabled={sending || state.capturing || !text.trim()}>Send</button>
          </form>
        </> : <>
          {!status && statusLoading && <p role="status">Checking local voice…</p>}
          {status?.available && <div className="crucible-voice-setup">
            <label>Voice model
              <select value={voiceModel ?? ""} disabled={!catalog.backend || starting} onChange={event => chooseVoiceModel(event.target.value)}>
                {modelChoices.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
              </select>
            </label>
            <label>Speech recognition
              <select value={stt} disabled={!!operation || starting} onChange={event => setStt(event.target.value as VoiceRecognizer)}>
                {RECOGNIZERS.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}
              </select>
            </label>
            <label>Spoken replies
              <select value={tts} disabled={!!operation || starting} onChange={event => setTts(event.target.value as VoiceSynthesizer)}>
                {synthOptions.map(model => <option key={model} value={model}>
                  {SYNTHESIZERS[model]}{model === "none" || status.tts.includes(model) ? "" : " · download required"}
                </option>)}
              </select>
            </label>
            <div className="crucible-voice-actions">
              {operation?.type === "install" ? <>
                <span role="status">{operation.model !== "pocket" ? "Installing speech model…"
                  : status.pocketEngine === "onnx" ? "Installing spoken replies…" : "Installing Pocket TTS…"}</span>
                <BtnGhost onClick={cancelInstallation}>Cancel download</BtnGhost>
                {/* Text-only voice works while spoken replies download. */}
                {operation.model === "pocket" && tts === "none" && modelReady && <BtnPrimary onClick={() => void start()} disabled={starting}>
                  {starting ? "Starting voice…" : "Start voice session"}
                </BtnPrimary>}
              </> : !modelReady ? <BtnPrimary onClick={() => void changeInstallation("install", stt)} disabled={!!operation}>
                Install speech model
              </BtnPrimary> : tts === "pocket" && !synthReady && status.pocketAvailable ? <BtnPrimary
                onClick={() => void changeInstallation("install", "pocket")} disabled={!!operation}>{pocketInstallLabel(status)}</BtnPrimary> : <>
                <BtnPrimary onClick={() => void start()} disabled={starting || !!operation || !synthReady}>
                  {starting ? "Starting voice…" : "Start voice session"}
                </BtnPrimary>
                <BtnGhost onClick={() => void changeInstallation("remove", stt)} disabled={starting || !!operation}>
                  {operation?.model === stt ? "Removing…" : "Remove speech model"}
                </BtnGhost>
                {tts === "none" && offerPocket && <BtnGhost onClick={() => void changeInstallation("install", "pocket")} disabled={starting || !!operation}>
                  {pocketInstallLabel(status)}
                </BtnGhost>}
                {tts === "pocket" && synthReady && <BtnGhost onClick={() => void changeInstallation("remove", "pocket")} disabled={starting || !!operation}>
                  {operation?.model === "pocket" ? "Removing…" : "Remove Pocket TTS"}
                </BtnGhost>}
              </>}
            </div>
          </div>}
          {status && !status.available && <p>{status.reason ?? "Local voice is unavailable on this desktop."}</p>}
          {!statusLoading && !status && <BtnGhost onClick={() => { setError(""); void updateStatus(true); }}>Retry voice status</BtnGhost>}
        </>}
        {(turns.length > 0 || state.capturing) && <div ref={transcript} className="crucible-voice-transcript" aria-label="Voice transcript" tabIndex={0}>
          <ol>
            {turns.map(turn => <li key={turn.id}>
              <p><strong>You</strong> {turn.text}</p>
              {turn.reply && <p aria-live={turn.final ? "polite" : "off"}><strong>Crucible</strong> {turn.reply}</p>}
              {turn.receipt && turn.receipt.status !== "answered" && <p className="crucible-voice-receipt" role="status">
                <strong>{RECEIPT_LABELS[turn.receipt.status]}</strong>
                {turn.receipt.text && turn.receipt.text !== turn.reply && <> {turn.receipt.text}</>}
              </p>}
            </li>)}
          </ol>
          {state.partial && <p className="crucible-voice-partial" data-phase={state.deciding ? "deciding" : state.checking ? "transcribing" : undefined}><strong>{heardLabel(state)}</strong> {state.partial}</p>}
        </div>}
        {error && <p className="crucible-voice-error" role="alert">{error}</p>}
      </div>
    </CardShell>
  </section>;
}
