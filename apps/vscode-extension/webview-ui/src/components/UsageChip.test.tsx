import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { UsageChip } from "./UsageChip";

const u = (requests: number, input: number, output: number, cached = 0) =>
  ({ requests, input, output, cached });

describe("UsageChip", () => {
  it("shows the thread total and a breakdown on hover", () => {
    render(<UsageChip teamNames={{ t1: "tetris" }} usage={{
      total: u(9, 1_200_000, 30_000, 600_000), main: u(3, 200_000, 10_000),
      agents: { a: u(6, 1_000_000, 20_000, 600_000) },
      teams: { t1: { total: u(6, 1_000_000, 20_000, 600_000), members: {} } },
    }} />);
    const chip = screen.getByTestId("usage-chip");
    expect(chip).toHaveTextContent("↑1.2M ↓30k");
    expect(screen.queryByTestId("usage-breakdown")).toBeNull();
    fireEvent.mouseEnter(chip.parentElement!);
    const breakdown = screen.getByTestId("usage-breakdown");
    expect(breakdown).toHaveTextContent("Main agent");
    expect(breakdown).toHaveTextContent("Team tetris");
    expect(breakdown).toHaveTextContent("↑1M ↓20k · 6 req · 60% cached");
    expect(breakdown).not.toHaveTextContent("Other agents");   // all agents are in the team
  });

  it("renders nothing before any request", () => {
    const { container } = render(<UsageChip teamNames={{}} usage={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
