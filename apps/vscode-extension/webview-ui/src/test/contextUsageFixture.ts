import type { ContextUsage } from "../types";

export const contextUsage = {
  thread_id: "t1", process_id: "p1", provider: "chatgpt", model: "gpt-context",
  scope: "main", generation: 1, revision: 2, turn_id: "turn1", request_id: "req1",
  status: "in_flight", input_tokens: 18000, input_quality: "estimated",
  context_window: 100000, capacity_source: "chatgpt_catalog",
  observed_at: "2026-10-08T12:00:00Z",
} satisfies ContextUsage;
