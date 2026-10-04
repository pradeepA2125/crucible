import type { RewindPreviewView } from "../types";

/**
 * Confirm gate for a permanent rewind. Built on the shared .scrim/.surface-card
 * primitives so it matches the rest of the webview design language.
 *
 * The copy is deliberately blunt about the limits: rewind restores the files the
 * agent wrote and the conversation, and nothing else. Commands it ran already
 * happened, and background sessions keep running — saying so here is cheaper than
 * a user discovering it afterwards.
 */
export function RewindDialog({
  preview,
  onCancel,
  onConfirm,
  onStopAllAgents,
}: {
  preview: RewindPreviewView;
  onCancel: () => void;
  onConfirm: () => void;
  onStopAllAgents?: () => void;
}) {
  const runningAgents = preview.blockedByAgents ?? [];
  const blocked = preview.blockedByTask != null || runningAgents.length > 0;
  const plural = (n: number) => (n === 1 ? "" : "s");
  return (
    <div
      className="scrim fixed inset-0 z-50 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      onClick={onCancel}
    >
      <div
        className="surface-card max-w-sm p-4 m-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-text font-medium mb-2">Rewind to here?</div>
        <div className="text-text-2 text-xs mb-3 leading-relaxed">
          This permanently removes {preview.messages} message{plural(preview.messages)} and
          restores {preview.files} file{plural(preview.files)} the agent changed. It cannot
          be undone.
        </div>
        {preview.commandsRun > 0 && (
          <div className="text-text-2 text-xs mb-2 leading-relaxed">
            {preview.commandsRun} command{plural(preview.commandsRun)} the agent ran are not
            undone.
          </div>
        )}
        {preview.sessions.length > 0 && (
          <div className="text-text-2 text-xs mb-2 leading-relaxed">
            These sessions keep running: {preview.sessions.map((s) => s.command).join(", ")}
          </div>
        )}
        {preview.blockedByTask != null && (
          <div className="text-xs mb-2 leading-relaxed" style={{ color: "var(--color-red)" }}>
            Task {preview.blockedByTask} is still running and would re-write these files.
            Cancel or abort it first.
          </div>
        )}
        {runningAgents.length > 0 && (
          <div className="text-xs mb-2 leading-relaxed" style={{ color: "var(--color-red)" }}>
            {runningAgents.length === 1
              ? `Agent ${runningAgents[0]} is still running and could edit these files.`
              : `Agents ${runningAgents.join(", ")} are still running and could edit these files.`}
            {onStopAllAgents && (
              <button className="menu-item px-2 py-0.5 rounded ml-2" onClick={onStopAllAgents}>
                Stop all agents
              </button>
            )}
          </div>
        )}
        <div className="flex gap-2 justify-end mt-3">
          <button className="menu-item px-3 py-1 rounded" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="menu-item px-3 py-1 rounded"
            disabled={blocked}
            onClick={onConfirm}
            style={blocked ? { opacity: 0.5, cursor: "not-allowed" } : undefined}
          >
            Rewind
          </button>
        </div>
      </div>
    </div>
  );
}
