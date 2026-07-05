import { CardShell } from "../../components/shared/CardShell";
import { SectionHeader } from "../SectionHeader";
import { ENV_FLAG_OPTIONS } from "../types";
import { FIELD } from "../ui";
import type { SectionProps } from "./meta";

/** PoliciesSection — env-flag dropdowns (shell/scope policy, memory flags). */
export function PoliciesSection({ state, send }: SectionProps) {
  return (
    <div>
      <SectionHeader
        title="Policies & Memory"
        description="Approval policies for shell commands and out-of-scope writes, plus the memory harness. Changes apply after a backend restart."
      />
      <CardShell icon="shield" title="Policies">
        <div className="flex flex-col px-3 pb-2 pt-1">
          {ENV_FLAG_OPTIONS.map((opt) => (
            <label
              key={opt.key}
              className="flex items-center justify-between gap-2 border-b py-2.5 text-xs text-text-2 last:border-b-0"
              style={{ borderColor: "var(--hairline)" }}
            >
              {opt.label}
              <select
                className={FIELD}
                value={state.envFlags[opt.key] ?? opt.options[0]}
                onChange={(e) => send({ type: "settings/setEnvFlag", key: opt.key, value: e.target.value })}
              >
                {opt.options.map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
            </label>
          ))}
        </div>
      </CardShell>
    </div>
  );
}
