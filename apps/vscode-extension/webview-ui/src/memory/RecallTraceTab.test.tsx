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
