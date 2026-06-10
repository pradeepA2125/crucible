import { useState } from "react";
import { Icon } from "../../Icon";
import { vscode } from "../../../vscodeApi";
import { CardShell } from "../../shared/CardShell";
import { BtnPrimary, BtnGhost, BtnDanger } from "../../shared/buttons";

// ── shlexJoin ─────────────────────────────────────────────────────────────────
//
// Ported EXACTLY from apps/vscode-extension/media/chat.js lines ~192-199.
// Backend rule-matching depends on identical quoting — do not change the regex
// or escaping without updating the backend allowlist logic too.

export function shlexJoin(tokens: string[]): string {
  return tokens
    .map((t) =>
      /[ \t\n"'\\$|&;<>()*?\[\]{}#~`]/.test(t)
        ? `'${t.replace(/'/g, `'"'"'`)}'`
        : t
    )
    .join(" ");
}

// ── Types ─────────────────────────────────────────────────────────────────────

type ScopeKind = "exact" | "prefix" | "binary";

interface Props {
  taskId: string;
  payload: Record<string, unknown>;
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * CommandGate — shell command approval card.
 *
 * Lets the user decide: allow once (exact), allow & remember (exact/prefix/binary),
 * or reject. Mirrors the legacy chat.js command_card with custom radio group
 * and shlexJoin-based preview.
 */
export function CommandGate({ taskId, payload }: Props) {
  const command = String(payload.command ?? "");
  const args = Array.isArray(payload.args) ? payload.args.map(String) : [];
  const stepId = String(payload.step_id ?? "");

  const tokens = [command, ...args].filter(Boolean);
  const basename = command.split("/").pop() || command;

  const [scope, setScope] = useState<ScopeKind>("exact");
  const [prefixCount, setPrefixCount] = useState(1);
  const [resolved, setResolved] = useState<string | null>(null);

  // ── helpers ──

  function ruleValue(): string {
    if (scope === "binary") return basename;
    if (scope === "exact") return shlexJoin(tokens);
    // prefix
    const n = Math.max(1, Math.min(tokens.length, prefixCount));
    return shlexJoin(tokens.slice(0, n));
  }

  function previewText(): string {
    if (scope === "exact") return "auto-approves: this exact command";
    if (scope === "binary") return `auto-approves: any "${basename} …"`;
    const n = Math.max(1, Math.min(tokens.length, prefixCount));
    return `auto-approves: ${shlexJoin(tokens.slice(0, n))} …`;
  }

  // ── actions ──

  function handleAllowOnce() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Allowed once");
    vscode.postMessage({
      type: "commandDecision",
      taskId,
      approve: true,
      remember: false,
      scope: "exact",
    });
  }

  function handleAllowRemember() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Allowed & remembered");
    vscode.postMessage({
      type: "commandDecision",
      taskId,
      approve: true,
      remember: true,
      scope,
      ruleValue: ruleValue(),
    });
  }

  function handleReject() {
    if (resolved !== null) return; // one-shot guard
    setResolved("Rejected");
    vscode.postMessage({ type: "commandDecision", taskId, approve: false });
  }

  // ── render ──

  return (
    <CardShell
      icon="term"
      title="Run command?"
      subtitle={stepId ? `step ${stepId}` : undefined}
      borderColor="var(--accent-brd)"
      headerTint="linear-gradient(180deg, var(--accent-bg), transparent)"
    >
      {/* ── Command block ── */}
      <div
        className="mx-3 mb-2.5 px-[11px] py-2 rounded-[7px] font-mono text-[11px] text-text-2 overflow-x-auto whitespace-nowrap"
        style={{ background: "var(--color-panel)", border: "1px solid var(--color-border)" }}
      >
        <span className="select-none" style={{ color: "var(--color-accent)" }}>$ </span>
        {shlexJoin(tokens)}
      </div>

      {/* ── Radio group ── */}
      <div className="flex flex-col gap-[6px] px-3 pb-2.5">
        {/* exact */}
        <RadioOption
          selected={scope === "exact"}
          onSelect={() => setScope("exact")}
          label="Exact command only"
        />

        {/* prefix */}
        <RadioOption
          selected={scope === "prefix"}
          onSelect={() => setScope("prefix")}
          label={
            <span className="flex items-center gap-1">
              Prefix — lock first{" "}
              <input
                type="number"
                min={1}
                max={tokens.length || 1}
                value={prefixCount}
                onChange={(e) => {
                  setScope("prefix");
                  setPrefixCount(Math.max(1, Math.min(tokens.length || 1, Number(e.target.value) || 1)));
                }}
                onClick={(e) => { e.stopPropagation(); setScope("prefix"); }}
                className="w-10 bg-surface-2 border border-border-strong rounded px-1 py-0.5 text-[10px] text-text text-center outline-none"
              />{" "}
              token(s)
            </span>
          }
        />

        {/* binary */}
        <RadioOption
          selected={scope === "binary"}
          onSelect={() => setScope("binary")}
          label={
            <span>
              Any{" "}
              <code className="font-mono text-[10px] text-text-3">{basename}</code>{" "}
              command
            </span>
          }
        />
      </div>

      {/* ── Preview line ── */}
      <p className="px-3 pb-2.5 text-text-3 text-[10px]">{previewText()}</p>

      {/* ── Actions row ── */}
      <div className="flex gap-1.5 px-2.5 py-2 border-t border-border">
        {resolved === null ? (
          <>
            <BtnPrimary flex onClick={handleAllowOnce}>
              Allow once
            </BtnPrimary>
            <BtnGhost onClick={handleAllowRemember}>
              Allow &amp; remember
            </BtnGhost>
            <BtnDanger onClick={handleReject}>
              Reject
            </BtnDanger>
          </>
        ) : (
          <ResolvedRow resolved={resolved} />
        )}
      </div>
    </CardShell>
  );
}

// ── sub-components ────────────────────────────────────────────────────────────

interface RadioOptionProps {
  selected: boolean;
  onSelect: () => void;
  label: React.ReactNode;
}

function RadioOption({ selected, onSelect, label }: RadioOptionProps) {
  return (
    <div
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className="flex items-center gap-2 text-[11px] cursor-pointer"
      style={{ color: selected ? "var(--color-text)" : "var(--color-text-2)" }}
    >
      {/* Custom radio dot — 13px circle, violet dot when selected */}
      <span
        className="relative flex-shrink-0 rounded-full"
        style={{
          width: 13,
          height: 13,
          border: `1.5px solid ${selected ? "var(--color-accent)" : "var(--color-border-strong)"}`,
          transition: "border-color .12s",
        }}
      >
        {selected && (
          <span
            className="absolute rounded-full"
            style={{
              inset: 2.5,
              background: "var(--color-accent)",
              boxShadow: "0 0 6px var(--accent-glow)",
            }}
          />
        )}
      </span>
      {label}
    </div>
  );
}

interface ResolvedRowProps {
  resolved: string;
}

function ResolvedRow({ resolved }: ResolvedRowProps) {
  const isApproval = resolved !== "Rejected";
  return (
    <div className="flex items-center gap-1.5">
      <span style={{ color: isApproval ? "var(--color-green)" : "var(--color-red)" }}>
        <Icon name={isApproval ? "check" : "x"} size={12} />
      </span>
      <span className="text-[11px] text-text-2">{resolved}</span>
    </div>
  );
}
