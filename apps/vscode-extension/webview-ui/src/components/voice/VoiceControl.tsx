import { useId } from "react";
import { Icon } from "../Icon";
import { voicePhase } from "../../desktop/voice/voiceStatus";
import type { VoiceComposerModel } from "./VoiceContext";

const CHIP = "flex h-7 shrink-0 items-center gap-1 rounded-control px-1.5 text-meta hover:bg-surface-2 disabled:cursor-default disabled:opacity-50";
const PILL = "flex h-7 shrink-0 items-center gap-1.5 rounded-full border border-border-strong bg-surface-2 px-2.5 text-meta text-text hover:bg-surface-3 disabled:cursor-default disabled:opacity-50";

/**
 * The composer's voice control (desktop only). Idle: a "Voice" button that starts a session
 * with the saved Settings. Running: a status pill (hands-free: click ends the session;
 * push to talk: hold it to speak, with a separate End button), mute, and Stop speaking
 * while a reply plays. A model download shows its own line with Cancel.
 */
export function VoiceControl({ voice }: { voice: VoiceComposerModel }) {
  const { status, state, active, starting, pushToTalk, download } = voice;
  const reasonId = useId();

  const downloading = download && <div role="status" className="flex min-w-0 shrink items-center gap-1 text-meta text-text-2">
    <span className="px-1 text-accent"><Icon name="waveform" size={13} /></span>
    <span className="truncate">Downloading {download.label}…</span>
    <button type="button" onClick={voice.cancelDownload} className={`${CHIP} text-text-2`}>Cancel</button>
  </div>;

  // A speech-recognition download started by the first Voice press takes the control's place
  // until the session can start; a Pocket download only sits beside it.
  if (!active && downloading && download.model !== "pocket") return downloading;

  if (!active) {
    const reason = status && !status.available ? status.reason ?? "Local voice is unavailable on this desktop." : undefined;
    const disabled = starting || !status?.available;
    return <>
      <button type="button" onClick={voice.start} disabled={disabled} aria-label="Voice" aria-describedby={reason ? reasonId : undefined}
        title={reason ?? (starting ? "Starting voice…" : "Talk with the voice agent")}
        className={CHIP} style={{ color: "var(--color-text-3)" }}>
        <Icon name="waveform" size={13} />{starting ? "Starting voice…" : "Voice"}
      </button>
      {reason && <span id={reasonId} className="sr-only">{reason}</span>}
      {downloading}
    </>;
  }

  const phase = voicePhase(state, pushToTalk);
  const listening = state.capturing || (!pushToTalk && state.microphone === "on");
  const dot = <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full"
    style={{ background: listening ? "var(--success)" : state.speaking ? "var(--color-accent)" : "var(--color-text-3)" }} />;
  return <div className="flex min-w-0 flex-wrap items-center gap-1" data-testid="voice-session">
    <span role="status" className="sr-only">{phase}</span>
    {pushToTalk ? <>
      <span aria-hidden="true" className="flex items-center gap-1.5 px-1 text-meta text-text-3">{dot}{phase}</span>
      <button type="button" className={PILL} disabled={state.microphone !== "on"} aria-label="Hold to talk, release to send"
        style={{ touchAction: "none", userSelect: "none" }}
        onPointerDown={event => {
          if (event.button !== 0 || !event.isPrimary) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          voice.beginHold();
        }}
        onPointerUp={event => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) voice.sendNow();
        }}
        onLostPointerCapture={voice.cancelHold}
        onKeyDown={event => {
          if (event.key !== " " && event.key !== "Enter") return;
          event.preventDefault();
          if (!event.repeat) voice.beginHold();
        }}
        onKeyUp={event => {
          if (event.key !== " " && event.key !== "Enter") return;
          event.preventDefault();
          voice.sendNow();
        }}
        onBlur={voice.cancelHold}>
        <Icon name="waveform" size={13} />Hold to talk
      </button>
      <button type="button" onClick={voice.end} aria-label="End voice session" className={`${CHIP} text-text-2`}>End voice</button>
    </> : <button type="button" onClick={voice.end} aria-label="End voice session" title="End voice session" className={PILL}>
      {dot}<span aria-hidden="true">{phase}</span>
    </button>}
    <button type="button" onClick={voice.toggleMicrophone} className={`${CHIP} text-text-2`}
      aria-label={state.microphone === "off" ? "Turn microphone on" : "Mute microphone"}>
      {state.microphone === "off" ? "Unmute" : "Mute"}
    </button>
    {state.speaking && <button type="button" onClick={voice.stopSpeaking} className={`${CHIP} text-text-2`}>Stop speaking</button>}
    {downloading}
  </div>;
}
