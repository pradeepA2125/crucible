import { Icon } from "../../components/Icon";
import { SectionHeader } from "../SectionHeader";
import type { SettingsState } from "../types";
import { SECTIONS, type SectionId } from "./meta";

interface Props {
  state: SettingsState;
  onNavigate: (id: SectionId) => void;
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/**
 * OverviewSection — the landing card grid (one card per section), mirroring
 * Copilot CLI's Overview page. Cards stagger in and lift on hover; the
 * subline of each card surfaces the live state (active model, counts).
 */
export function OverviewSection({ state, onNavigate }: Props) {
  const sublines: Partial<Record<SectionId, string>> = {
    provider: state.provider ? `${state.provider.backend} · ${state.provider.model}` : "not configured",
    mcp: plural(state.mcp.servers.length, "server"),
    skills: plural(state.skills.length, "skill"),
    runtime: state.runtime ? `release ${state.runtime.releaseTag}` : "not installed",
  };

  return (
    <div>
      <SectionHeader
        title="Settings"
        description="Configure the provider, tools, and policies that shape how the agent works in this workspace."
      />
      <div className="grid grid-cols-2 gap-3">
        {SECTIONS.map((s, i) => (
          <button
            key={s.id}
            type="button"
            onClick={() => onNavigate(s.id)}
            className="hover-lift anim-section flex cursor-pointer flex-col items-start gap-2 rounded-[10px] border bg-surface p-3.5 text-left"
            style={{ borderColor: "var(--color-border)", animationDelay: `${i * 45}ms` }}
          >
            <span
              className="flex h-7 w-7 items-center justify-center rounded-[8px]"
              style={{ background: "var(--accent-bg)", color: "var(--color-accent)" }}
            >
              <Icon name={s.icon} size={14} />
            </span>
            <span className="text-xs font-semibold text-text">{s.label}</span>
            <span className="text-[11px] leading-relaxed text-text-3">{s.blurb}</span>
            {sublines[s.id] && (
              <span className="font-mono text-[9.5px]" style={{ color: "var(--color-text-4)" }}>
                {sublines[s.id]}
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
