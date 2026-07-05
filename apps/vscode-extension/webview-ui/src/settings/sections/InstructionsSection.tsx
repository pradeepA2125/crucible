import { useEffect, useState } from "react";
import { CardShell } from "../../components/shared/CardShell";
import { BtnPrimary } from "../../components/shared/buttons";
import { Icon } from "../../components/Icon";
import { SectionHeader } from "../SectionHeader";
import type { SettingsInMsg } from "../types";

interface Props {
  instructions: { content: string; exists: boolean } | null;
  busy: boolean;
  send: (msg: SettingsInMsg) => void;
}

/**
 * InstructionsSection — a plain AGENTS.md editor. The backend mtime-watches
 * the file, so a save here applies from the next turn with no restart.
 */
export function InstructionsSection({ instructions, busy, send }: Props) {
  const [draft, setDraft] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);

  // Sync the draft whenever a fresh file state arrives (load or post-save echo).
  useEffect(() => {
    if (instructions) setDraft(instructions.content);
  }, [instructions?.content, instructions?.exists]);

  const header = (
    <SectionHeader
      title="Instructions"
      description="Project instructions the agent reads on every turn (AGENTS.md at the workspace root). Saves apply from the next message — no restart."
    />
  );

  if (!instructions) {
    return (
      <div>
        {header}
        <p className="py-8 text-center text-[11px] text-text-3">Loading AGENTS.md…</p>
      </div>
    );
  }

  if (!instructions.exists) {
    return (
      <div>
        {header}
        <CardShell icon="book" title="AGENTS.md">
          <div className="flex flex-col items-center gap-3 px-3 py-8">
            <span
              className="flex h-10 w-10 items-center justify-center rounded-[10px]"
              style={{ background: "var(--accent-bg)", color: "var(--color-accent)" }}
            >
              <Icon name="book" size={18} />
            </span>
            <p className="text-xs font-semibold text-text">No AGENTS.md yet in this workspace</p>
            <p className="max-w-[340px] text-center text-[11px] leading-relaxed text-text-3">
              Create one to give the agent always-on project rules — conventions, commands, gotchas.
            </p>
            <BtnPrimary icon="plus" onClick={() => send({ type: "settings/saveInstructions", content: "" })}>
              Create AGENTS.md
            </BtnPrimary>
          </div>
        </CardShell>
      </div>
    );
  }

  const dirty = draft !== instructions.content;

  return (
    <div>
      {header}
      <CardShell
        icon="book"
        title="AGENTS.md"
        badge={dirty ? (
          <span className="h-[6px] w-[6px] rounded-full" style={{ background: "var(--color-amber)" }} aria-label="unsaved changes" />
        ) : undefined}
      >
        <div className="flex flex-col gap-2.5 px-3 pb-3 pt-1">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            className="min-h-[320px] w-full resize-y rounded-md border border-border-strong bg-surface-2 p-2.5 text-[11.5px] leading-relaxed text-text outline-none transition-colors duration-150 focus:border-[var(--color-accent)]"
            style={{ fontFamily: "var(--vscode-editor-font-family, ui-monospace, Menlo, monospace)" }}
          />
          <div className="flex items-center gap-2">
            <BtnPrimary
              disabled={busy || !dirty}
              onClick={() => {
                send({ type: "settings/saveInstructions", content: draft });
                setSavedFlash(true);
                setTimeout(() => setSavedFlash(false), 2000);
              }}
            >
              Save
            </BtnPrimary>
            {savedFlash && (
              <span className="anim-pop flex items-center gap-1 text-[11px]" style={{ color: "var(--color-green)" }}>
                <Icon name="check" size={11} /> Saved — applies next turn
              </span>
            )}
          </div>
        </div>
      </CardShell>
    </div>
  );
}
