import { BtnGhost, BtnPrimary } from "./shared/buttons";
import { ChatGPTLogo } from "./shared/ChatGPTBrand";
import { CHATGPT_USAGE_URL } from "../chatgpt";
import type { ProviderAccessView } from "../types";
import { vscode } from "../vscodeApi";

interface Copy {
  title: string;
  body: string;
  action: { label: string; run: () => void };
  secondary?: { label: string; run: () => void };
}

const manageUsage = () =>
  vscode.postMessage({ type: "settings/openExternal", url: CHATGPT_USAGE_URL });
const openProviderSettings = () => vscode.postMessage({ type: "openSettings", section: "provider" });

function copyFor(access: ProviderAccessView): Copy {
  switch (access.kind) {
    // UI/UX guidelines: Manage usage is the primary action; the limit may be the plan's
    // or this app's. We sell no credits, so there is no "Buy app credits" action.
    case "usage_limit":
      return {
        title: "Usage limit reached",
        body: "Review your plan or this app's limit in ChatGPT settings.",
        action: { label: "Manage usage", run: manageUsage },
      };
    case "session_invalid":
      return {
        title: "ChatGPT sign-in ended",
        body: access.message,
        action: { label: "Sign in again", run: openProviderSettings },
      };
    case "plan_disabled":
      return {
        title: "ChatGPT plan usage is off",
        body: access.message,
        action: { label: "Enable in settings", run: openProviderSettings },
      };
    case "not_eligible":
      return {
        title: "ChatGPT plan usage isn't available",
        body: access.message,
        action: { label: "Use an API key instead", run: openProviderSettings },
      };
    default:
      return {
        title: "ChatGPT refused the request",
        body: access.message + (access.requestId ? ` (request ${access.requestId})` : ""),
        action: { label: "Provider settings", run: openProviderSettings },
        secondary: { label: "Manage usage", run: manageUsage },
      };
  }
}

/** Why the last turn stopped on the ChatGPT plan's access rules. Clears itself when
 * the next turn starts (the backend drops it from /live). */
export function ProviderAccessCard({ access }: { access: ProviderAccessView }) {
  const copy = copyFor(access);
  return (
    <div
      role="alert"
      className="anim-pop flex flex-col gap-2 rounded-[10px] border px-3 py-2.5"
      style={{ background: "var(--color-surface)", borderColor: "var(--color-border-strong)" }}
    >
      <div className="flex items-center gap-2">
        {/* Keep the ChatGPT identity visible in the compact message (UI/UX guidelines). */}
        <ChatGPTLogo size={14} color="var(--color-text)" />
        <span className="text-[11px] text-text-3">ChatGPT</span>
      </div>
      <div>
        <p className="text-xs font-medium text-text">{copy.title}</p>
        <p className="mt-0.5 text-[11px] leading-relaxed text-text-2">{copy.body}</p>
      </div>
      <div className="flex items-center gap-2">
        <BtnPrimary onClick={copy.action.run}>{copy.action.label}</BtnPrimary>
        {copy.secondary && <BtnGhost onClick={copy.secondary.run}>{copy.secondary.label}</BtnGhost>}
      </div>
    </div>
  );
}
