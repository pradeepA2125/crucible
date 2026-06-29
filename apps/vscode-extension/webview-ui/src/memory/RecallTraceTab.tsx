import type { RecallTrace } from "./types";

export function RecallTraceTab({ trace }: { trace: RecallTrace | null }) {
  return (
    <div data-testid="memory-trace-tab" className="p-3">
      {trace ? `${trace.entries.length} candidates` : "No recall recorded yet."}
    </div>
  );
}
