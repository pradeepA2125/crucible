import { useEffect, useRef, useState } from "react";
import { BtnPrimary } from "../components/shared/buttons";
import { NavRail } from "./NavRail";
import { OverviewSection } from "./sections/OverviewSection";
import { ProviderSection } from "./sections/ProviderSection";
import { McpSection } from "./sections/McpSection";
import { SkillsSection } from "./sections/SkillsSection";
import { InstructionsSection } from "./sections/InstructionsSection";
import { PoliciesSection } from "./sections/PoliciesSection";
import { RuntimeSection } from "./sections/RuntimeSection";
import type { SectionId, SectionProps } from "./sections/meta";
import type { SettingsOutMsg, SettingsState } from "./types";
import { vscode } from "./vscodeApi";

/**
 * SettingsApp — thin shell: NavRail + one active section in an animated
 * content pane. Data flow is unchanged: `settings/load` populates one
 * SettingsState snapshot, every mutating action posts and receives a
 * rebuilt snapshot. Sections are pure presentation over {state, busy, send}.
 */
export default function SettingsApp() {
  const [state, setState] = useState<SettingsState | null>(null);
  const [instructions, setInstructions] = useState<{ content: string; exists: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [section, setSection] = useState<SectionId>("overview");
  const instructionsRequested = useRef(false);

  useEffect(() => {
    const onMessage = (event: MessageEvent<SettingsOutMsg>) => {
      const msg = event.data;
      if (!msg || typeof msg !== "object") return;
      setBusy(false);
      if (msg.type === "settings/state") {
        setState(msg.state);
        setError(null);
      } else if (msg.type === "settings/instructions") {
        setInstructions({ content: msg.content, exists: msg.exists });
      } else if (msg.type === "settings/error") {
        setError(msg.message);
      } else if (msg.type === "settings/navigate") {
        setSection(msg.section);
      }
    };
    window.addEventListener("message", onMessage);
    vscode.postMessage({ type: "settings/load" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Lazy-load AGENTS.md the first time the Instructions section opens.
  useEffect(() => {
    if (section === "instructions" && !instructionsRequested.current) {
      instructionsRequested.current = true;
      vscode.postMessage({ type: "settings/loadInstructions" });
    }
  }, [section]);

  const send: SectionProps["send"] = (msg) => {
    setBusy(true);
    setError(null);
    vscode.postMessage(msg);
  };

  if (!state) {
    // A buildState failure on the host (e.g. backend unreachable) posts settings/error
    // before any state arrives. Surface it here — otherwise the panel hangs on
    // "Loading settings…" forever, since the error banner below is gated on `state`.
    if (error) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
          <p className="text-sm font-medium" style={{ color: "var(--color-red)" }}>
            Couldn’t load settings
          </p>
          <p className="max-w-[420px] text-xs text-text-3">{error}</p>
          <BtnPrimary
            onClick={() => {
              setError(null);
              vscode.postMessage({ type: "settings/load" });
            }}
          >
            Retry
          </BtnPrimary>
        </div>
      );
    }
    return <div className="p-6 text-sm text-text-3">Loading settings…</div>;
  }

  const props: SectionProps = { state, busy, send };
  const counts: Partial<Record<SectionId, number>> = {
    mcp: state.mcp.servers.length,
    skills: state.skills.length,
  };

  return (
    <div className="flex h-full min-h-0">
      <NavRail active={section} counts={counts} onSelect={setSection} />
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[560px] p-5">
          {error && (
            <div
              className="anim-slide-down mb-4 rounded-[10px] border px-3 py-2 text-xs"
              style={{ borderColor: "var(--red-brd)", background: "var(--red-bg)", color: "var(--color-red)" }}
            >
              {error}
            </div>
          )}
          {state.restartRequired && (
            <div
              className="anim-slide-down mb-4 flex items-center justify-between gap-3 rounded-[10px] border px-3 py-2 text-xs"
              style={{ borderColor: "rgba(251,191,36,.3)", background: "var(--amber-bg)", color: "var(--color-amber)" }}
            >
              <span>Some changes require a backend restart to take effect.</span>
              <BtnPrimary disabled={busy} onClick={() => send({ type: "settings/restartBackend" })}>
                Restart backend
              </BtnPrimary>
            </div>
          )}
          <div key={section} className="anim-section">
            {section === "overview" && <OverviewSection state={state} onNavigate={setSection} />}
            {section === "provider" && <ProviderSection {...props} />}
            {section === "mcp" && <McpSection {...props} />}
            {section === "skills" && <SkillsSection {...props} />}
            {section === "instructions" && (
              <InstructionsSection instructions={instructions} busy={busy} send={send} />
            )}
            {section === "policies" && <PoliciesSection {...props} />}
            {section === "runtime" && <RuntimeSection {...props} />}
          </div>
        </div>
      </main>
    </div>
  );
}
