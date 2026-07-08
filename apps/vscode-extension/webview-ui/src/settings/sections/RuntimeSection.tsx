import { CardShell } from "../../components/shared/CardShell";
import { BtnGhost } from "../../components/shared/buttons";
import { SectionHeader } from "../SectionHeader";
import type { SectionProps } from "./meta";

/** RuntimeSection — installed component versions + restart. */
export function RuntimeSection({ state, busy, send }: SectionProps) {
  return (
    <div>
      <SectionHeader
        title="Runtime"
        description="The managed runtime installed under ~/.crucible/runtime."
      />
      <CardShell
        icon="chip"
        title="Installed runtime"
        subtitle={state.runtime ? `release ${state.runtime.releaseTag}` : "not installed"}
      >
        <div className="flex flex-col gap-3 px-3 pb-3 pt-1">
          {state.runtime ? (
            <ul className="flex flex-col">
              {Object.entries(state.runtime.components).map(([id, version]) => (
                <li
                  key={id}
                  className="flex items-center justify-between border-b py-1.5 text-[11px] last:border-b-0"
                  style={{ borderColor: "var(--hairline)" }}
                >
                  <span className="text-text-2">{id}</span>
                  <span className="font-mono text-text-3">{version}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[11px] text-text-3">Runtime not installed — run "Crucible: Run Setup".</p>
          )}
          <BtnGhost className="self-start" disabled={busy} onClick={() => send({ type: "settings/restartBackend" })}>
            Restart backend
          </BtnGhost>
        </div>
      </CardShell>
    </div>
  );
}
