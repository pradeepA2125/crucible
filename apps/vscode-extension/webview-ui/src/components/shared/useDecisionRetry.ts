import { useEffect, useRef } from "react";
/** Reopen a submitted card if the host could not admit its decision. Keep its draft. */
export function useDecisionRetry(threadId: string, kind: "mode" | "clarify" | "team_plan", reset: () => void, gateId?: string) {
  const resetRef = useRef(reset);
  resetRef.current = reset;
  useEffect(() => {
    const retry = (event: MessageEvent) => {
      const m = event.data;
      if (m?.type === "decisionRetry" && m.threadId === threadId && m.kind === kind && (!gateId || m.gateId === gateId)) resetRef.current();
    };
    window.addEventListener("message", retry);
    return () => window.removeEventListener("message", retry);
  }, [threadId, kind, gateId]);
}
