import { useEffect, useMemo, useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnGhost, BtnPrimary } from "../../components/shared/buttons";
import { ChatGPTLogo, ContinueWithChatGPTButton } from "../../components/shared/ChatGPTBrand";
import { ChatGPTWelcomeModal } from "../../components/shared/ChatGPTWelcomeModal";
import { Icon } from "../../components/Icon";
import { CHATGPT_USAGE_URL } from "../../chatgpt";
import { vscode } from "../vscodeApi";
import type { ChatGPTAccount, ChatGPTModel, ChatGPTSignIn, SettingsOutMsg } from "../types";
import { FIELD } from "../ui";
import type { SectionProps } from "./meta";

/**
 * "Use your ChatGPT plan" (spec 2026-10-06 §6; OpenAI UI/UX guidelines): offer the plan
 * with Continue with ChatGPT, keep saved accounts selectable, show which one is in use,
 * link to ChatGPT usage settings, and keep API-key billing a separate, explicit path.
 */
export function ChatGPTPlanPanel({ state, busy, send }: SectionProps) {
  const [accounts, setAccounts] = useState<ChatGPTAccount[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [signIn, setSignIn] = useState<ChatGPTSignIn | null>(null);
  const [models, setModels] = useState<{ registrationId: string; list: ChatGPTModel[] } | null>(null);
  const [model, setModel] = useState("");
  const [error, setError] = useState<{ message: string; kind?: string } | null>(null);
  const [signedOut, setSignedOut] = useState<{ remoteRevoked: boolean } | null>(null);
  const [welcome, setWelcome] = useState<string | null>(null);

  const usingPlan = state.provider?.backend === "chatgpt";

  useEffect(() => {
    const onMessage = (event: MessageEvent<SettingsOutMsg>) => {
      const msg = event.data;
      if (!msg || typeof msg !== "object") return;
      switch (msg.type) {
        case "settings/chatgpt":
          setAccounts(msg.accounts);
          setActiveId(msg.activeRegistrationId);
          break;
        case "settings/chatgptSignIn":
          setSignIn(msg.signIn);
          if (msg.signIn.state === "succeeded" && msg.signIn.registrationId) {
            setSelectedId(msg.signIn.registrationId);
            setError(null);
          }
          break;
        case "settings/chatgptModels":
          setModels({ registrationId: msg.registrationId, list: msg.models });
          setModel((current) => (msg.models.some((m) => m.slug === current) ? current
            : msg.models[0]?.slug ?? ""));
          break;
        case "settings/chatgptError":
          setError({ message: msg.message, ...(msg.kind ? { kind: msg.kind } : {}) });
          break;
        case "settings/chatgptSignedOut":
          setSignedOut({ remoteRevoked: msg.remoteRevoked });
          break;
        case "settings/chatgptWelcome":
          setWelcome(msg.registrationId);
          break;
      }
    };
    window.addEventListener("message", onMessage);
    vscode.postMessage({ type: "settings/chatgptLoad" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // The account being configured: an explicit pick, else the one in use, else the
  // first that can use the plan.
  const selected = useMemo(() => {
    if (!accounts?.length) return null;
    return accounts.find((a) => a.registrationId === selectedId)
      ?? accounts.find((a) => a.registrationId === activeId)
      ?? accounts.find((a) => a.signedIn && a.planEnabled)
      ?? null;
  }, [accounts, selectedId, activeId]);

  // The catalog is per account: fetch it whenever the configured account changes.
  const selectedUsable = selected !== null && selected.signedIn && selected.planEnabled;
  useEffect(() => {
    if (selected && selectedUsable && models?.registrationId !== selected.registrationId) {
      vscode.postMessage({ type: "settings/chatgptModels", registrationId: selected.registrationId });
    }
  }, [selected, selectedUsable, models?.registrationId]);

  useEffect(() => {
    if (usingPlan && state.provider?.model) setModel(state.provider.model);
  }, [usingPlan, state.provider?.model]);

  const pending = signIn?.state === "pending";
  const startSignIn = (registrationId?: string, reconsent?: boolean) => {
    setError(null);
    setSignedOut(null);
    send({
      type: "settings/chatgptSignIn",
      ...(registrationId ? { registrationId } : {}),
      ...(reconsent ? { reconsent: true } : {}),
    });
  };
  const manageUsage = () => send({ type: "settings/openExternal", url: CHATGPT_USAGE_URL });
  const catalog = selected && models?.registrationId === selected.registrationId ? models.list : null;

  return (
    <CardShell icon="spark" iconColor="var(--color-text)" title="ChatGPT plan">
      <div className="flex flex-col gap-3 px-3 pb-3 pt-1">
        {!accounts?.length ? (
          <PlanOffer pending={pending} busy={busy} onContinue={() => startSignIn()} />
        ) : (
          <>
            <ul className="flex flex-col gap-1.5" aria-label="ChatGPT accounts">
              {accounts.map((a) => (
                <AccountRow
                  key={a.registrationId}
                  account={a}
                  active={usingPlan && a.registrationId === activeId}
                  selected={a.registrationId === selected?.registrationId}
                  busy={busy || pending}
                  onSelect={() => setSelectedId(a.registrationId)}
                  onSignIn={() => startSignIn(a.registrationId)}
                  onEnablePlan={() => startSignIn(a.registrationId, true)}
                  onSignOut={() => {
                    setSignedOut(null);
                    send({ type: "settings/chatgptSignOut", registrationId: a.registrationId });
                  }}
                />
              ))}
            </ul>
            {selected && selectedUsable && (
              <div className="flex flex-col gap-2">
                <label className="flex flex-col gap-1 text-xs text-text-2">
                  Model
                  <select className={FIELD} value={model} disabled={!catalog?.length}
                          onChange={(e) => setModel(e.target.value)}>
                    {!catalog && <option value="">Loading this account's models…</option>}
                    {catalog?.length === 0 && <option value="">No models available</option>}
                    {catalog?.map((m) => (
                      <option key={m.slug} value={m.slug}>{m.displayName}</option>
                    ))}
                  </select>
                </label>
                <div className="flex items-center gap-2">
                  <BtnPrimary
                    disabled={busy || pending || !model}
                    onClick={() => {
                      // The catalog knows the model's window: compaction should use it
                      // rather than a guessed default.
                      const window = catalog?.find((m) => m.slug === model)?.contextWindow;
                      send({ type: "settings/useChatGPT", registrationId: selected.registrationId,
                             model, ...(window ? { contextWindow: window } : {}) });
                    }}
                  >
                    {usingPlan && selected.registrationId === activeId
                      ? "Switch model" : "Use this account"}
                  </BtnPrimary>
                </div>
              </div>
            )}
            <div>
              <BtnGhost disabled={busy || pending} onClick={() => startSignIn()}>
                Add another ChatGPT account
              </BtnGhost>
            </div>
          </>
        )}

        {pending && (
          <div className="flex items-center gap-2 text-xs text-text-2" role="status">
            <span>Finish signing in with ChatGPT in your browser…</span>
            <BtnGhost onClick={() => send({ type: "settings/chatgptCancel" })}>Cancel</BtnGhost>
          </div>
        )}
        {signIn?.state === "failed" && signIn.reason !== "cancelled" && (
          <p className="text-xs" style={{ color: "var(--color-red)" }}>{signIn.message}</p>
        )}
        {signIn?.state === "succeeded" && signIn.planEnabled === false && (
          <p className="text-xs leading-relaxed" style={{ color: "var(--color-amber)" }}>
            You're signed in, but ChatGPT plan usage wasn't allowed. Enable it for that
            account below, or choose a provider with your own API key instead.
          </p>
        )}
        {error && (
          <p className="text-xs" style={{ color: "var(--color-red)" }}>{error.message}</p>
        )}
        {signedOut && (
          <p className="text-xs leading-relaxed"
             style={{ color: signedOut.remoteRevoked ? "var(--color-text-2)" : "var(--color-amber)" }}>
            {signedOut.remoteRevoked
              ? "Signed out."
              : "Signed out on this computer, but ChatGPT didn't confirm the session ended. You can disconnect Crucible in ChatGPT settings."}
          </p>
        )}

        {usingPlan && (
          <p className="flex items-center gap-1.5 text-[11px] text-text-2">
            <ChatGPTLogo size={12} />
            <span>Using ChatGPT plan</span>
            <span className="text-text-4">·</span>
            <button type="button" className="underline-offset-2 hover:underline"
                    style={{ color: "var(--color-accent-ink)" }} onClick={manageUsage}>
              Manage usage
            </button>
          </p>
        )}
        <p className="text-[11px] leading-relaxed text-text-3">
          On ChatGPT Plus, Crucible shares your plan's five-hour usage limit with every
          other app that uses it; sub-agents and teams use it faster. ChatGPT plan usage is
          separate from API-key billing — choose a provider below to use your own key.
        </p>
      </div>
      {welcome && (
        <ChatGPTWelcomeModal
          onManageUsage={manageUsage}
          onDismiss={() => {
            send({ type: "settings/chatgptWelcomed", registrationId: welcome });
            setWelcome(null);
          }}
        />
      )}
    </CardShell>
  );
}

export function PlanOffer({ pending, busy, onContinue }: {
  pending: boolean; busy: boolean; onContinue: () => void;
}) {
  return (
    <div className="flex flex-col gap-2.5">
      <div>
        <p className="text-[13px] font-medium text-text">Use your ChatGPT plan</p>
        <p className="mt-1 text-xs leading-relaxed text-text-2">
          Complete eligible AI requests in Crucible with usage included in your ChatGPT
          plan or credits balance. Available on eligible ChatGPT plans.
        </p>
      </div>
      <ContinueWithChatGPTButton onClick={onContinue} disabled={busy || pending} />
    </div>
  );
}

function AccountRow({ account, active, selected, busy, onSelect, onSignIn, onEnablePlan, onSignOut }: {
  account: ChatGPTAccount; active: boolean; selected: boolean; busy: boolean;
  onSelect: () => void; onSignIn: () => void; onEnablePlan: () => void; onSignOut: () => void;
}) {
  const status = !account.signedIn ? "Signed out"
    : account.planEnabled ? "Plan usage on" : "Plan usage off";
  const statusColor = !account.signedIn ? "var(--color-text-3)"
    : account.planEnabled ? "var(--color-green)" : "var(--color-amber)";
  return (
    <li
      className="flex flex-wrap items-center gap-2 rounded-lg border px-2.5 py-2"
      style={{
        borderColor: selected ? "var(--accent-brd)" : "var(--color-border)",
        background: selected ? "var(--accent-bg)" : "var(--color-surface-2)",
      }}
    >
      <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left"
              onClick={onSelect} aria-pressed={selected}>
        <ChatGPTLogo size={14} color="var(--color-text)" />
        <span className="truncate text-xs text-text">{account.label}</span>
        {active && (
          <span className="rounded-full px-1.5 py-[1px] text-[10px]"
                style={{ background: "var(--accent-bg)", color: "var(--color-accent-ink)",
                         border: "1px solid var(--accent-brd)" }}>
            In use
          </span>
        )}
      </button>
      <span className="text-[10px]" style={{ color: statusColor }}>{status}</span>
      <div className="flex items-center gap-1.5">
        {!account.signedIn && <BtnGhost disabled={busy} onClick={onSignIn}>Sign in again</BtnGhost>}
        {account.signedIn && !account.planEnabled && (
          <BtnGhost disabled={busy} onClick={onEnablePlan}>Enable ChatGPT plan usage</BtnGhost>
        )}
        {account.signedIn && (
          <BtnGhost disabled={busy} onClick={onSignOut}>
            <Icon name="x" size={10} /> Sign out
          </BtnGhost>
        )}
      </div>
    </li>
  );
}
