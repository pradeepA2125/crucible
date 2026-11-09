import { useEffect, useRef, useState, type FormEvent } from "react";
import { BtnDanger, BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { CardShell } from "../../components/shared/CardShell";
import { readVoiceStatus, voiceRequest, type VoiceBridge, type VoiceIntent, type VoiceModelAsset, type VoiceReceipt, type VoiceRecognizer, type VoiceStatus, type VoiceSynthesizer } from "./voiceBridge";
import { INITIAL_VOICE_STATE, VoiceSession, type VoiceSessionState } from "./voiceSession";
import "./voice.css";

const RECOGNIZERS: ReadonlyArray<{ id: VoiceRecognizer; label: string }> = [
  { id: "whisper-small", label: "Whisper Small" },
  { id: "whisper-base", label: "Whisper Base" },
];
const SYNTHESIZERS: Record<VoiceSynthesizer, string> = {
  system: "macOS system voice",
  pocket: "Pocket TTS",
};
const RECEIPT_LABELS: Record<VoiceReceipt["status"], string> = {
  answered: "Answered",
  queued: "Correction queued",
  incorporated: "Correction reached the model",
  expired: "Correction expired",
};

interface VoiceTurn {
  id: string;
  text: string;
  reply?: string;
  final?: boolean;
  receipt?: VoiceReceipt;
}

function phase(state: VoiceSessionState, pushToTalk: boolean) {
  if (state.waiting) return "Still listening…";
  if (state.checking) return "Thinking about that";
  if (state.capturing) return "Hearing you";
  if (state.speaking) return "Speaking";
  if (state.microphone === "starting") return "Opening microphone";
  if (state.pending) return "Processing message";
  if (state.microphone === "off") return "Microphone off";
  return pushToTalk ? "Push to talk ready" : "Listening";
}

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
}

export function VoicePanel({ bridge, threadId, id }: Props) {
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
  const [turns, setTurns] = useState<VoiceTurn[]>([]);
  const [text, setText] = useState("");
  const [intent, setIntent] = useState<VoiceIntent>("auto");
  const [sending, setSending] = useState(false);
  const transcript = useRef<HTMLDivElement>(null);

  function updateStatus(initial = false) {
    setStatusLoading(true);
    return readVoiceStatus(bridge).then(next => {
      if (!alive.current) return;
      setStatus(next);
      if (initial) {
        setStt(initialRecognizer(next.installed));
        setTts(next.tts.includes("system") ? "system" : next.tts[0] ?? (next.pocketAvailable ? "pocket" : "system"));
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
    const node = transcript.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns, state.partial]);

  const modelReady = status?.installed.includes(stt) ?? false;
  const synthReady = status?.tts.includes(tts) ?? false;
  const manualMode = pushToTalk;
  const synthOptions = [...(status?.tts ?? [])];
  if (status?.pocketAvailable && !synthOptions.includes("pocket")) synthOptions.push("pocket");
  const start = async () => {
    if (session.current || starting || !status?.available || !modelReady || !status.tts.includes(tts)) return;
    setStarting(true);
    setError("");
    setState(INITIAL_VOICE_STATE);
    setSending(false);
    setTurns([]);
    let next: VoiceSession | undefined;
    try {
      next = new VoiceSession(bridge, {
        state: patch => setState(previous => ({ ...previous, ...patch })),
        utterance: (utteranceId, message) => setTurns(previous => [
          ...previous, { id: utteranceId, text: message },
        ].slice(-30)),
        reply: (utteranceId, reply, final) => setTurns(previous => previous.map(turn =>
          turn.id === utteranceId ? { ...turn, reply, final } : turn)),
        receipt: receipt => setTurns(previous => previous.map(turn =>
          turn.id === receipt.message_id ? { ...turn, receipt } : turn)),
        error: setError,
        ended: reason => {
          session.current = undefined;
          setState(INITIAL_VOICE_STATE);
          if (reason) setError(reason);
        },
      });
      session.current = next;
      next.setPushToTalk(pushToTalk);
      await next.start(threadId, stt, tts);
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
              data-listening={state.capturing || (!manualMode && state.microphone === "on")}>{phase(state, manualMode)}</span>
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
            <label>Speech recognition
              <select value={stt} disabled={!!operation || starting} onChange={event => setStt(event.target.value as VoiceRecognizer)}>
                {RECOGNIZERS.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}
              </select>
            </label>
            <label>Spoken replies
              <select value={tts} disabled={!!operation || starting || !synthOptions.length} onChange={event => setTts(event.target.value as VoiceSynthesizer)}>
                {synthOptions.length ? synthOptions.map(model => <option key={model} value={model}>
                  {SYNTHESIZERS[model]}{status.tts.includes(model) ? "" : " · download required"}
                </option>) : <option value="system">Unavailable</option>}
              </select>
            </label>
            <div className="crucible-voice-actions">
              {operation?.type === "install" ? <>
                <span role="status">{operation.model === "pocket" ? "Installing Pocket TTS…" : "Installing speech model…"}</span>
                <BtnGhost onClick={cancelInstallation}>Cancel download</BtnGhost>
              </> : !modelReady ? <BtnPrimary onClick={() => void changeInstallation("install", stt)} disabled={!!operation}>
                Install speech model
              </BtnPrimary> : tts === "pocket" && !synthReady && status.pocketAvailable ? <BtnPrimary
                onClick={() => void changeInstallation("install", "pocket")} disabled={!!operation}>Install Pocket TTS</BtnPrimary> : <>
                <BtnPrimary onClick={() => void start()} disabled={starting || !!operation || !synthReady}>
                  {starting ? "Starting voice…" : "Start voice session"}
                </BtnPrimary>
                <BtnGhost onClick={() => void changeInstallation("remove", stt)} disabled={starting || !!operation}>
                  {operation?.model === stt ? "Removing…" : "Remove speech model"}
                </BtnGhost>
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
          {state.partial && <p className="crucible-voice-partial"><strong>Hearing</strong> {state.partial}</p>}
        </div>}
        {error && <p className="crucible-voice-error" role="alert">{error}</p>}
      </div>
    </CardShell>
  </section>;
}
