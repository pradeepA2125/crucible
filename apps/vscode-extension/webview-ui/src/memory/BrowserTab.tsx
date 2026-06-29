import type { MemoryView } from "./types";

export function BrowserTab({
  memories,
}: {
  memories: MemoryView[];
  chains: Record<string, MemoryView[]>;
}) {
  return (
    <div data-testid="memory-browser-tab" className="p-3">
      {memories.length} memories
    </div>
  );
}
