import type { TodoItem } from "../../types";

const GLYPH: Record<TodoItem["status"], string> = {
  pending: "☐", in_progress: "▶", done: "✓", blocked: "⛔", cancelled: "✕",
};

/**
 * TodoCard — read-only flat checklist of the controller's live todo ledger.
 * Nested items + per-mutation approval are deferred (spec §9); v1 is a flat list.
 */
export function TodoCard({ items }: { items: TodoItem[] }) {
  // cancelled items are listed (audit) but excluded from the progress denominator.
  const counted = items.filter((i) => i.status !== "cancelled");
  const done = counted.filter((i) => i.status === "done").length;
  return (
    <div className="rounded border border-[var(--vscode-panel-border)] p-2 text-sm">
      <div className="mb-1 font-semibold">
        Todo — {done} of {counted.length} done
      </div>
      <ul className="flex flex-col gap-0.5">
        {items.map((it, idx) => (
          <li
            key={`${idx}:${it.title}`}
            className={it.status === "cancelled" ? "line-through opacity-60" : ""}
          >
            <span className="mr-1">{GLYPH[it.status]}</span>
            <span>{it.title}</span>
            {it.note && (it.status === "blocked" || it.status === "cancelled") && (
              <span className="ml-1 opacity-70">— {it.note}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
