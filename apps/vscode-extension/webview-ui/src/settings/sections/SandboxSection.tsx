import { useEffect, useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnGhost } from "../../components/shared/buttons";
import { Switch } from "../../components/shared/Switch";
import { SectionHeader } from "../SectionHeader";
import type { SandboxConfig, SettingsOutMsg } from "../types";
import { FIELD } from "../ui";
import type { SectionProps } from "./meta";

function statusText(status: SandboxConfig["status"]): string {
  if (!status.enabled) return "Off";
  if (status.available) return "On";
  // No reason yet: the startup check has not finished.
  return status.reason ? `Unavailable: ${status.reason} — commands run unprotected` : "Checking…";
}

function ListRow({ value, onRemove }: { value: string; onRemove?: () => void }) {
  return (
    <li className="flex min-w-0 items-center justify-between gap-2 border-b py-1.5 text-xs text-text-2 last:border-b-0"
        style={{ borderColor: "var(--hairline)" }}>
      <span className="min-w-0 font-mono [overflow-wrap:anywhere]">{value}</span>
      {onRemove && <BtnGhost onClick={onRemove}><span aria-label={`Remove ${value}`}>Remove</span></BtnGhost>}
    </li>
  );
}

/** One input plus Add; the list it adds to refreshes when the backend accepts the value. */
function AddRow({ label, noun, placeholder, busy, onAdd }: {
  label: string; noun: string; placeholder: string; busy: boolean; onAdd: (value: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const value = draft.trim();
  const add = () => {
    if (!value || busy) return;
    onAdd(value);
    setDraft("");
  };
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2 pt-2">
      <input aria-label={label} className={`${FIELD} min-w-0 flex-1`} placeholder={placeholder} value={draft}
             onChange={(e) => setDraft(e.target.value)}
             onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
      <BtnGhost disabled={busy || !value} onClick={add}>
        <span aria-label={`Add ${noun}`}>Add</span>
      </BtnGhost>
    </div>
  );
}

/** Settings › Sandbox: its state is loaded apart from the settings snapshot, so a backend
 * without a sandbox only affects this section. */
export function SandboxSection({ busy, send }: SectionProps) {
  const [config, setConfig] = useState<SandboxConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const onMessage = (event: MessageEvent<SettingsOutMsg>) => {
      const msg = event.data;
      if (!msg || typeof msg !== "object") return;
      if (msg.type === "settings/sandboxState") {
        setConfig(msg.config);
        setError(null);
      } else if (msg.type === "settings/sandboxError") {
        setError(msg.message);
      }
    };
    window.addEventListener("message", onMessage);
    send({ type: "settings/sandbox" });
    return () => window.removeEventListener("message", onMessage);
    // Mount-only: `send` is rebuilt on every SettingsApp render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div>
      <SectionHeader
        title="Sandbox"
        description="Agent commands can write only inside this workspace and reach only allowed hosts. Credential folders are unreadable."
      />
      {error && <p role="alert" className="mb-3 text-xs text-red [overflow-wrap:anywhere]">{error}</p>}
      {config && <>
        <CardShell icon="term" title="Command sandbox">
          <div className="flex flex-col gap-1 px-3 py-2.5">
            <label className="flex min-w-0 items-center justify-between gap-2 text-xs text-text-2">
              Run agent commands in a sandbox
              <Switch checked={config.status.enabled} disabled={busy} label="Run agent commands in a sandbox"
                      onChange={(enabled) => send({ type: "settings/setSandboxEnabled", enabled })} />
            </label>
            <p className="text-xs text-text-3 [overflow-wrap:anywhere]">{statusText(config.status)}</p>
          </div>
        </CardShell>
        <CardShell icon="plug" title="Allowed hosts for this workspace">
          <div className="px-3 py-2.5">
            <ul>
              {config.defaultHosts.map((host) => <ListRow key={host} value={host} />)}
              {config.hosts.map((host) => (
                <ListRow key={host} value={host} onRemove={() => send({
                  type: "settings/setSandboxHosts", hosts: config.hosts.filter((h) => h !== host) })} />
              ))}
            </ul>
            <AddRow label="Add a host" noun="host" placeholder="example.com" busy={busy}
                    onAdd={(host) => send({ type: "settings/setSandboxHosts", hosts: [...config.hosts, host] })} />
          </div>
        </CardShell>
        <CardShell icon="book" title="Extra writable folders">
          <div className="px-3 py-2.5">
            {config.folders.length > 0 && (
              <ul>
                {config.folders.map((folder) => (
                  <ListRow key={folder} value={folder} onRemove={() => send({
                    type: "settings/setSandboxFolders", folders: config.folders.filter((f) => f !== folder) })} />
                ))}
              </ul>
            )}
            <AddRow label="Add a folder" noun="folder" placeholder="/absolute/path" busy={busy}
                    onAdd={(folder) => send({ type: "settings/setSandboxFolders", folders: [...config.folders, folder] })} />
          </div>
        </CardShell>
      </>}
    </div>
  );
}
