import { useEffect, useState } from "react";
import { BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { CardShell } from "../../components/shared/CardShell";
import { readVoiceStatus, voiceRequest, type VoiceModelAsset, type VoiceRecognizer, type VoiceStatus, type VoiceSynthesizer } from "../../desktop/voice/voiceBridge";
import { readVoiceModel, voiceModelChoices, writeVoiceModel } from "../../desktop/voice/voiceModelPreference";
import {
  defaultRecognizer, readMicrophoneMode, readRecognizer, readSynthesizer, synthesizerChoices, writeMicrophoneMode,
  writeRecognizer, writeSynthesizer, type MicrophoneMode,
} from "../../desktop/voice/voicePreferences";
import { SectionHeader } from "../SectionHeader";
import type { SectionProps } from "./meta";
import { FIELD } from "../ui";

const RECOGNIZERS: { id: VoiceRecognizer; name: string; mib: number }[] = [
  { id: "whisper-small", name: "Whisper Small", mib: 465 },
  { id: "whisper-base", name: "Whisper Base", mib: 141 },
];
const mib = (bytes: number) => `${Math.round(bytes / 1_048_576)} MiB`;

type VoiceCatalog = { backend: string | null; options: { backend: string; model: string; display?: string }[] };

export function VoiceSection({ send }: Pick<SectionProps, "send">) {
  const [status, setStatus] = useState<VoiceStatus>();
  const [operation, setOperation] = useState<"install" | "remove">();
  const [message, setMessage] = useState("");
  const [catalog, setCatalog] = useState<VoiceCatalog>({ backend: null, options: [] });
  const [recognizer, setRecognizer] = useState<VoiceRecognizer | null>(() => readRecognizer());
  const [synthesizer, setSynthesizer] = useState<VoiceSynthesizer | null>(() => readSynthesizer());
  const [microphone, setMicrophone] = useState<MicrophoneMode>(() => readMicrophoneMode());
  const [voiceModel, setVoiceModel] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    const bridge = window.crucibleVoice;
    if (!bridge) setMessage("Local voice is unavailable in this build.");
    else void readVoiceStatus(bridge).then(
      result => { if (current) setStatus(result); },
      () => { if (current) setMessage("Local voice status could not be loaded."); },
    );
    return () => { current = false; };
  }, []);

  // The voice model is chosen per provider, from the same catalog the composer's model menu shows.
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const data = event.data;
      if (data?.type !== "settings/voiceModels") return;
      const backend = typeof data.backend === "string" ? data.backend : null;
      setCatalog({ backend, options: Array.isArray(data.options) ? data.options : [] });
      setVoiceModel(backend ? readVoiceModel(backend) : null);
    };
    window.addEventListener("message", onMessage);
    send({ type: "settings/listVoiceModels" });
    return () => window.removeEventListener("message", onMessage);
    // The catalog is requested once per open; send is the panel's stable helper.
  }, []);

  async function change(type: "install" | "remove", model: VoiceModelAsset) {
    const bridge = window.crucibleVoice;
    if (!bridge || operation) return;
    setOperation(type);
    setMessage(type === "install" ? "Downloading and verifying the model…" : "");
    try {
      await voiceRequest(bridge, { type, model });
      setStatus(await readVoiceStatus(bridge));
      setMessage(type === "install" ? "Model installed." : "Model removed.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Voice model action could not be completed.");
    } finally { setOperation(undefined); }
  }

  const chooseModel = (value: string) => {
    const model = value || null;
    setVoiceModel(model);
    if (catalog.backend) writeVoiceModel(catalog.backend, model);
  };
  const modelChoices = voiceModelChoices(catalog.backend ?? "", catalog.options, voiceModel);
  const replies = status ? synthesizerChoices(status) : [];
  const repliesValue = synthesizer && replies.some(choice => choice.value === synthesizer) ? synthesizer : replies[0]?.value ?? "none";
  const recognizerValue = recognizer ?? defaultRecognizer(status?.installed ?? []);
  const models = status?.available ? [
    ...RECOGNIZERS.map(model => ({ id: model.id as VoiceModelAsset, name: model.name,
      detail: `Speech recognition · ${model.mib} MiB`, installed: status.installed.includes(model.id) })),
    ...(status.pocketAvailable || status.tts.includes("pocket") ? [{ id: "pocket" as VoiceModelAsset, name: "Pocket TTS",
      detail: `Spoken replies · ${mib(status.pocketDownloadBytes)}`, installed: status.tts.includes("pocket") }] : []),
  ] : [];

  return <div>
    <SectionHeader title="Local voice" description="How voice works when you press Voice in a desktop conversation. Models download during setup, or on the first press." />
    {status?.available && <div className="mb-3"><CardShell title="Preferences" icon="waveform">
      <div className="grid gap-3 px-3 pb-3 pt-1 sm:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-1">
          <label className="flex min-w-0 flex-col gap-1 text-xs text-text-2">
            Voice model
          <select className={FIELD} value={voiceModel ?? ""} disabled={!catalog.backend} onChange={event => chooseModel(event.target.value)}>
            {modelChoices.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
          </select>
          </label>
          <span className="text-meta text-text-3">Per provider. Automatic uses the provider's default; a change applies to a running session.</span>
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <label className="flex min-w-0 flex-col gap-1 text-xs text-text-2">
            Speech recognition
          <select className={FIELD} value={recognizerValue} onChange={event => {
            const next = event.target.value as VoiceRecognizer;
            setRecognizer(next);
            writeRecognizer(next);
          }}>
            {RECOGNIZERS.map(model => <option key={model.id} value={model.id}>
              {model.name}{status.installed.includes(model.id) ? "" : " · download required"}
            </option>)}
          </select>
          </label>
          <span className="text-meta text-text-3">Applies to the next session.</span>
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <label className="flex min-w-0 flex-col gap-1 text-xs text-text-2">
            Spoken replies
          <select className={FIELD} value={repliesValue} onChange={event => {
            const next = event.target.value as VoiceSynthesizer;
            setSynthesizer(next);
            writeSynthesizer(next);
          }}>
            {replies.map(choice => <option key={choice.value} value={choice.value}>
              {choice.label}{choice.installed ? "" : " · download required"}
            </option>)}
          </select>
          </label>
          <span className="text-meta text-text-3">Until Pocket TTS is downloaded, the system voice speaks where there is one. Applies to the next session.</span>
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <label className="flex min-w-0 flex-col gap-1 text-xs text-text-2">
            Microphone mode
          <select className={FIELD} value={microphone} onChange={event => {
            const next = event.target.value as MicrophoneMode;
            setMicrophone(next);
            writeMicrophoneMode(next);
          }}>
            <option value="hands-free">Hands-free</option>
            <option value="push-to-talk">Push to talk</option>
          </select>
          </label>
          <span className="text-meta text-text-3">Hands-free listens for pauses; push to talk sends when you release. Applies to a running session.</span>
        </div>
      </div>
    </CardShell></div>}
    <CardShell title="Speech models" icon="spark">
      <div className="flex flex-col gap-3 px-3 pb-3">
        {!status?.available && <p className="text-meta text-text-2">{status?.reason ?? (message || "Checking speech models…")}</p>}
        {status?.available && <>
          <p className="text-meta text-text-2">Microphone access starts when you press Voice in a conversation.</p>
          {models.map(model => <div key={model.id} className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2">
            <div className="min-w-0"><div className="text-body text-text">{model.name}</div><div className="text-meta text-text-3">{model.detail}</div></div>
            {model.installed
              ? <BtnGhost disabled={!!operation} aria-label={`Remove ${model.name}`} onClick={() => void change("remove", model.id)}>Remove</BtnGhost>
              : <BtnPrimary disabled={!!operation} aria-label={`Download ${model.name}`} onClick={() => void change("install", model.id)}>Download</BtnPrimary>}
          </div>)}
          {message && <p className="text-meta text-text-2" role="status">{message}</p>}
          {operation === "install" && <BtnGhost className="self-start" onClick={() => {
            const bridge = window.crucibleVoice;
            if (bridge) void voiceRequest(bridge, { type: "cancelInstall" }).catch(() => {});
          }}>Cancel download</BtnGhost>}
        </>}
      </div>
    </CardShell>
  </div>;
}
