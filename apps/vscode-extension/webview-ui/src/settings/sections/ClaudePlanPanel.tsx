import { useEffect, useRef, useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { CLAUDE_PLAN_URL, CLAUDE_USAGE_URL } from "../../claude";
import type { ClaudeAccount, ClaudeSignIn, ClaudeModel } from "../claude-types";
import type { SettingsOutMsg } from "../types";
import { FIELD } from "../ui";
import { ClaudeLogo, ContinueWithClaudeButton } from "../../components/shared/ClaudeBrand";
import { vscode } from "../vscodeApi";
import type { SectionProps } from "./meta";

export function ClaudePlanPanel({ state, busy, send }: SectionProps) {
  const [accounts, setAccounts] = useState<ClaudeAccount[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [attempt, setAttempt] = useState<ClaudeSignIn | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [disconnected, setDisconnected] = useState(false);
  const [model, setModel] = useState(state.provider?.backend === "claude_subscription"
    ? state.provider.model : "claude-sonnet-5-5");
  const [catalog, setCatalog] = useState<{ registrationId: string; models: ClaudeModel[] } | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const models = catalog?.registrationId === selected ? catalog.models : [];
  const selectedModel = models.find(m => m.slug === model);
  const account = accounts.find(a => a.registrationId === selected);
  const using = state.provider?.backend === "claude_subscription"
    && accounts.some(a => a.registrationId === activeId && a.signedIn);
  const pending = attempt?.state === "pending";


  useEffect(() => {
    const listener = (event: MessageEvent<SettingsOutMsg>) => {
      const msg = event.data;
      if (!msg) return;
      if (msg.type === "settings/claude") {
        setAccounts(msg.accounts);
        setActiveId(msg.activeRegistrationId);
        setSelected(prev => msg.accounts.some(a => a.registrationId === prev) ? prev
          : msg.accounts.find(a => a.registrationId === msg.activeRegistrationId)?.registrationId
            ?? msg.accounts.find(a => a.signedIn)?.registrationId ?? msg.accounts[0]?.registrationId ?? "");
      } else if (msg.type === "settings/claudeSignIn") {
        setAttempt(msg.signIn);
        setError(msg.signIn.state === "failed" && msg.signIn.reason !== "cancelled" ? msg.signIn.message : null);
        if (msg.signIn.state === "succeeded") {
          setSelected(msg.signIn.registrationId ?? "");
          setDisconnected(false);
        }
      } else if (msg.type === "settings/claudeModels") {
        if (msg.registrationId !== selectedRef.current) return;
        setCatalog({ registrationId: msg.registrationId, models: msg.models });
        setLoadingModels(false);
        setCatalogError(null);
        setModel(prev => msg.models.some(m => m.slug === prev) ? prev
          : msg.models.find(m => m.slug === "claude-sonnet-5-5")?.slug
            ?? msg.models.find(m => m.contextWindow !== null)?.slug ?? "");
      } else if (msg.type === "settings/claudeError") {
        if (msg.registrationId) {
          if (msg.registrationId === selectedRef.current) {
            setLoadingModels(false); setCatalogError(msg.message);
          }
          return;
        }
        setError(msg.message);
        setAttempt(null);
      } else if (msg.type === "settings/claudeSignedOut") {
        setDisconnected(true);
        if (msg.registrationId === selectedRef.current) setCatalog(null);
      }
    };
    window.addEventListener("message", listener);
    vscode.postMessage({ type: "settings/claudeLoad" });
    return () => window.removeEventListener("message", listener);
  }, []);

  useEffect(() => {
    setCatalog(null); setCatalogError(null);
    if (!account?.signedIn) { setLoadingModels(false); return; }
    setLoadingModels(true);
    vscode.postMessage({ type: "settings/claudeModels", registrationId: selected });
  }, [selected, account?.signedIn]);

  const open = (url: string) => vscode.postMessage({ type: "settings/openExternal", url });
  return <CardShell icon="spark" title="Claude subscription">
    <div className="flex flex-col gap-3 px-3 pb-3 pt-1">
      <div className="flex items-center gap-2 text-sm font-medium text-text"><ClaudeLogo size={22} />Claude</div>
      <p className="text-xs text-text-2">Experimental connection. Use your Claude subscription with Crucible's agents and tools.</p>
      {using && <p className="text-xs text-text" role="status">Using Claude subscription · {state.provider?.model}</p>}
      {accounts.length > 0 && <label className="flex flex-col gap-1 text-xs text-text-2">Claude connection
        <select className={FIELD} value={selected} disabled={pending || busy} onChange={e => setSelected(e.target.value)}>
          {accounts.map(a => <option key={a.registrationId} value={a.registrationId}>{a.label} · {a.signedIn ? "Connected" : "Disconnected"}</option>)}
        </select>
      </label>}
      {pending ? <div role="status" className="text-xs text-text-2">
        Finish signing in in your browser, then return here.
        <BtnGhost onClick={() => send({ type: "settings/claudeCancel" })}>Cancel</BtnGhost>
      </div> : account?.signedIn ? <BtnGhost disabled={busy} onClick={() => {
        setError(null); setDisconnected(false); send({ type: "settings/claudeSignIn" });
      }}>Connect another Claude account</BtnGhost> : <ContinueWithClaudeButton disabled={busy} onClick={() => {
        setError(null); setDisconnected(false); send({ type: "settings/claudeSignIn" });
      }} />}
      {account?.signedIn && <>
        <label className="flex flex-col gap-1 text-xs text-text-2">Claude model
          <select className={FIELD} value={selectedModel ? model : ""} disabled={loadingModels || busy || pending}
            onChange={e => setModel(e.target.value)}>
            {!selectedModel && <option value="">{loadingModels ? "Loading Claude models…" : "Choose a model"}</option>}
            {models.map(m => <option key={m.slug} value={m.slug} disabled={m.contextWindow === null}>{m.displayName}</option>)}
          </select>
        </label>
        {selectedModel?.contextWindow && <p className="text-[11px] text-text-3">
          Context window: {selectedModel.contextWindow.toLocaleString("en-US")} tokens · From Claude's catalog
        </p>}
        {catalogError && <p role="alert" className="text-xs" style={{ color: "var(--color-red)" }}>{catalogError}</p>}
        {!loadingModels && models.length === 0 && !catalogError && <p className="text-xs text-text-2">No models available from Claude's catalog.</p>}
        <p className="text-[11px] text-text-3">Crucible sets the context window automatically. Saving checks access to the selected model.</p>
        <div className="flex flex-wrap gap-2">
          <BtnPrimary disabled={busy || pending || loadingModels || !selectedModel?.contextWindow}
            onClick={() => { setError(null); send({ type: "settings/useClaude", registrationId: selected, model }); }}>
            {using && selected === activeId ? "Save & validate Claude" : "Use this connection"}
          </BtnPrimary>
          <BtnGhost disabled={busy || pending || loadingModels} onClick={() => {
            setLoadingModels(true); setCatalog(null); setCatalogError(null);
            send({ type: "settings/claudeModels", registrationId: selected, refresh: true });
          }}>Refresh models</BtnGhost>
          <BtnGhost disabled={busy || pending} onClick={() => send({ type: "settings/claudeSignOut", registrationId: selected })}>Disconnect</BtnGhost>
        </div>
      </>}
      <div className="flex flex-wrap gap-2">
        <BtnGhost onClick={() => open(CLAUDE_USAGE_URL)}>Manage usage</BtnGhost>
        <BtnGhost onClick={() => open(CLAUDE_PLAN_URL)}>Manage plan</BtnGhost>
      </div>
      <p className="text-[11px] leading-relaxed text-text-3">All agents share your Claude allowance. Usage credits enabled in Claude may apply beyond your plan limits. Crucible does not switch to API billing automatically.</p>
      {disconnected && <p role="status" className="text-xs text-text-2">Disconnected on this computer. Your Claude subscription is unchanged.</p>}
      {error && <p role="alert" className="text-xs" style={{ color: "var(--color-red)" }}>{error}</p>}
    </div>
  </CardShell>;
}
