import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { RecallTraceTab } from "./RecallTraceTab";
import type { RecallTrace } from "./types";

function trace(overrides: Partial<RecallTrace> = {}): RecallTrace {
  return {
    query: "what does X do",
    scopeKind: "workspace",
    scopeId: "/ws",
    k: 8,
    floor: 0.15,
    reranked: true,
    entries: [
      {
        memoryId: "a", kind: "semantic", content: "auth flow", importance: 5,
        signals: { semantic: 1, lexical: 0.5, structural: 0.2, importance: 0.4, recency: 0.9 },
        fusedScore: 0.99, rerankScore: 0.88, finalRank: 0, injected: true,
      },
      {
        memoryId: "b", kind: "episodic", content: "below floor item", importance: 1,
        signals: { semantic: 0.1, lexical: 0, structural: 0, importance: 0, recency: 0.1 },
        fusedScore: 0.05, rerankScore: null, finalRank: 1, injected: false,
      },
    ],
    ...overrides,
  };
}

describe("RecallTraceTab", () => {
  test("catalog offers do not imply relevance, a full read or use", () => {
    render(<RecallTraceTab trace={{
      version: 2, trigger: "turn_start", status: "injected", judge: "main_model",
      rule: "candidate_catalog_v1", selectionMethod: "keyword + BGE ordering",
      signals: { Q: "request", C: "", R: "" },
      candidates: [{ id: "a", catalog: "[m:a@1] when: preview: fixture data", sources: ["keyword:Q"] }],
      scores: { a: { combined: 0.01 } }, reasons: {}, verdicts: {}, delivered: ["a"],
      invalidIds: [], invalidatedIds: [],
    }} />);
    expect(screen.getByText("Catalog offered")).toBeTruthy();
    expect(screen.getByText(/does not mean the model read or used it/)).toBeTruthy();
    expect(screen.getByText(/no acceptance cutoff/)).toBeTruthy();
    expect(screen.getByText(/Left to the main model/)).toBeTruthy();
    expect(screen.queryByText("Relevant", { exact: true })).toBeNull();
  });
  test("v2 shows the combined score and actual shortened reranker input", () => {
    render(<RecallTraceTab trace={{
      version: 2, trigger: "turn_start", status: "injected", judge: "reranker",
      signals: { Q: "request", C: "", R: "" },
      candidates: [{ id: "a", catalog: "[m:a@1] lesson", sources: ["keyword:Q"] }],
      scores: { a: { combined: 0.76 } }, reasons: {}, verdicts: { a: true }, delivered: ["a"],
      invalidIds: [], invalidatedIds: [], rule: "combined_rcq_body_v1",
      rerankInput: { query: "Current request: request", truncatedSignals: ["Q"],
        truncatedMemoryIds: [], maxPairTokens: 511 },
    }} />);
    expect(screen.getByText("combined: 0.760")).toBeTruthy();
    expect(screen.getByText(/511 tokens maximum/)).toBeTruthy();
    expect(screen.getByText("Shortened context: Q")).toBeTruthy();
    expect(screen.getByText("Shortened memories: none")).toBeTruthy();
  });
  test("v2 displays actual delivery, reasons and empty status without invented legacy scores", () => {
    render(<RecallTraceTab trace={{
      version: 2, trigger: "turn_start", status: "injected", judge: "llm",
      signals: { Q: "request", C: "previous", R: "recap" },
      candidates: [{ id: "a", catalog: "[m:a@1] lesson", sources: ["keyword:Q"] }],
      scores: {}, reasons: { a: "needed now" }, verdicts: { a: true }, delivered: ["a"],
      invalidIds: [], invalidatedIds: [],
    }} />);
    expect(screen.getByText("needed now")).toBeTruthy();
    expect(screen.getByText("Delivered", { exact: true })).toBeTruthy();
    expect(screen.queryByText(/importance/)).toBeNull();
  });
  test("renders the summary line with reranked + candidate count", () => {
    render(<RecallTraceTab trace={trace()} />);
    expect(screen.getByText(/what does X do/)).toBeTruthy();
    expect(screen.getByText(/2 candidates/)).toBeTruthy();
    expect(screen.getByText(/reranked/i).textContent).toMatch(/✓/);
  });

  test("renders five labeled signal bars per entry", () => {
    render(<RecallTraceTab trace={trace()} />);
    const row = screen.getByTestId("trace-entry-a");
    for (const label of ["semantic", "lexical", "structural", "importance", "recency"]) {
      expect(row.querySelector(`[data-signal="${label}"]`)).toBeTruthy();
    }
  });

  test("injected entry shows injected badge; below-floor shows below-floor badge", () => {
    render(<RecallTraceTab trace={trace()} />);
    expect(screen.getByTestId("trace-entry-a").textContent).toMatch(/injected/i);
    expect(screen.getByTestId("trace-entry-b").textContent).toMatch(/below floor/i);
  });

  test("empty trace shows the no-recall message", () => {
    render(<RecallTraceTab trace={null} />);
    expect(screen.getByText(/no recall recorded/i)).toBeTruthy();
  });

  test("trace with zero entries shows the zero-candidate message", () => {
    render(<RecallTraceTab trace={trace({ entries: [] })} />);
    expect(screen.getByText(/recall returned nothing/i)).toBeTruthy();
  });
});
