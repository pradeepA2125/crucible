import { useEffect, useState } from "react";
import { BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { CardShell } from "../../components/shared/CardShell";
import { readVoiceStatus, voiceRequest, type VoiceModelAsset, type VoiceStatus } from "../../desktop/voice/voiceBridge";
import { SectionHeader } from "../SectionHeader";

const models: { id: VoiceModelAsset; name: string; detail: string }[] = [
  { id: "whisper-small", name: "Whisper Small", detail: "Speech recognition · 465 MiB" },
  { id: "whisper-base", name: "Whisper Base", detail: "Speech recognition · 141 MiB" },
  { id: "pocket", name: "Pocket TTS", detail: "English voice output · 504 MiB" },
];

export function VoiceSection() {
  const [status, setStatus] = useState<VoiceStatus>();
  const [operation, setOperation] = useState<"install" | "remove">();
  const [message, setMessage] = useState("");
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

  return <div>
    <SectionHeader title="Local voice" description="Choose the models available when you start voice in a desktop conversation." />
    <CardShell title="Speech models" icon="spark">
      <div className="flex flex-col gap-3 px-3 pb-3">
        {!status?.available && <p className="text-meta text-text-2">{status?.reason ?? (message || "Checking speech models…")}</p>}
        {status?.available && <>
          <p className="text-meta text-text-2">The macOS system voice uses an installed system voice. Microphone access starts when you turn on voice in a conversation.</p>
          {models.filter(model => model.id !== "pocket" || status.pocketAvailable).map(model => {
            const installed = model.id === "pocket" ? status.tts.includes("pocket") : status.installed.includes(model.id);
            return <div key={model.id} className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2">
              <div className="min-w-0"><div className="text-body text-text">{model.name}</div><div className="text-meta text-text-3">{model.detail}</div></div>
              {installed
                ? <BtnGhost disabled={!!operation} aria-label={`Remove ${model.name}`} onClick={() => void change("remove", model.id)}>Remove</BtnGhost>
                : <BtnPrimary disabled={!!operation} aria-label={`Download ${model.name}`} onClick={() => void change("install", model.id)}>Download</BtnPrimary>}
            </div>;
          })}
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
