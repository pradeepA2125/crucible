import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InputArea } from "./InputArea";
import { contextUsage } from "../test/contextUsageFixture";
import type { ContextUsage } from "../types";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const availability = { disabled: false, placeholder: "Message", showStop: false, taskStop: false };
function composer(usage: ContextUsage | null = contextUsage, stale = false, stop = false) {
  return <InputArea availability={{ ...availability, showStop: stop }} draft="hello"
    onDraftChange={vi.fn()} contextUsage={usage} contextUsageStale={stale} />;
}

describe("ContextMeter in the composer", () => {
  it.each([
    ["in_flight", "estimated", "Context ~18%"],
    ["last_request", "provider_reported", "Last context 18%"],
    ["last_request", "estimated", "Last context ~18%"],
  ] as const)("labels %s %s", (status, input_quality, label) => {
    render(composer({ ...contextUsage, status, input_quality }));
    expect(screen.getByRole("button", { name: /context.*details/i })).toHaveTextContent(label);
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "18");
  });

  it("uses a known input count without inventing an unknown limit or percentage", () => {
    render(composer({ ...contextUsage, context_window: null, capacity_source: "unknown" }));
    const button = screen.getByRole("button", { name: /context.*details/i });
    expect(button).toHaveTextContent("18k tokens");
    expect(button).toHaveTextContent("limit unknown");
    expect(button).not.toHaveTextContent("%");
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });

  it.each(["awaiting_request", "invalidated", "unavailable"] as const)("does not show zero for %s", (status) => {
    render(composer({ ...contextUsage, status, input_tokens: null, input_quality: "unavailable" }));
    const button = screen.getByRole("button", { name: /context.*details/i });
    expect(button).toHaveTextContent(status === "awaiting_request" ? "ready · 100k limit" : status === "invalidated" ? "invalidated" : "unavailable");
    expect(button).not.toHaveTextContent(/0%|0 tokens/);
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });

  it("shows unavailable for a null snapshot and stale independently", () => {
    render(composer(null, true));
    expect(screen.getByRole("button", { name: /context.*details/i })).toHaveTextContent("unavailable · stale");
  });

  it("opens on focus and click, shows provenance, and dismisses with Escape", () => {
    render(composer());
    const button = screen.getByRole("button", { name: /context.*details/i });
    fireEvent.focus(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    const details = screen.getByRole("tooltip");
    expect(details).toHaveTextContent("18,000");
    expect(details).toHaveTextContent("100,000");
    expect(details).toHaveTextContent("Estimated");
    expect(details).toHaveTextContent("ChatGPT catalog");
    expect(details).toHaveTextContent("gpt-context");
    expect(details).toHaveTextContent("2026-10-08T12:00:00Z");
    expect(details).toHaveTextContent(/main.*input/i);
    expect(details).toHaveTextContent(/not.*account.*plan.*usage/i);
    expect(button).toHaveAttribute("aria-describedby", details.id);
    fireEvent.keyDown(button, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    fireEvent.click(button);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    fireEvent.blur(button);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("caps visual fill but retains the raw ratio and count in details", () => {
    render(composer({ ...contextUsage, input_tokens: 120000 }));
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByTestId("context-meter-fill")).toHaveStyle({ width: "100%" });
    fireEvent.click(screen.getByRole("button", { name: /context.*details/i }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("120%");
    expect(screen.getByRole("tooltip")).toHaveTextContent("120,000");
  });

  it("distinguishes a real zero and preserves Send, Stop and thread usage", () => {
    const total = { requests: 1, input: 99, output: 3, cached: 0 };
    const { rerender } = render(<InputArea availability={availability} draft="hello" onDraftChange={vi.fn()}
      contextUsage={{ ...contextUsage, input_tokens: 0 }} threadUsage={{ total, main: total, agents: {}, teams: {} }} />);
    expect(screen.getByRole("button", { name: /context.*details/i })).toHaveTextContent("~0%");
    expect(screen.getByTestId("context-meter-fill")).toHaveStyle({ width: "0%" });
    expect(screen.getByTestId("usage-chip")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    rerender(composer(contextUsage, true, true));
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
    expect(screen.getByRole("button", { name: /context.*details/i })).toHaveTextContent("stale");
  });
});

it("shows catalog capacity before the first request without inventing free space", () => {
  render(composer({ ...contextUsage, status: "awaiting_request", input_tokens: null,
    input_quality: "unavailable", observed_at: null, context_window: 1000000 }));
  const button = screen.getByRole("button", { name: /context.*details/i });
  expect(button).toHaveTextContent("Context ready · 1M limit");
  expect(button).not.toHaveTextContent(/unavailable|100%/);
  expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  fireEvent.focus(button);
  const details = screen.getByRole("tooltip");
  expect(details).toHaveTextContent("Context window: 1,000,000 tokens");
  expect(details).toHaveTextContent("Main request input: Not measured yet");
  expect(details).toHaveTextContent("Awaiting first request");
  expect(details).toHaveTextContent("including instructions and tool definitions");
  expect(details).not.toHaveTextContent("Unavailable");
});

it("keeps the limit unknown until catalog metadata arrives", () => {
  const initial = { ...contextUsage, status: "awaiting_request" as const,
    input_tokens: null, input_quality: "unavailable" as const, observed_at: null,
    context_window: null, capacity_source: "unknown" as const };
  const { rerender } = render(composer(initial));
  expect(screen.getByRole("button", { name: /context.*details/i })).toHaveTextContent("awaiting request");
  expect(screen.getByRole("button", { name: /context.*details/i })).not.toHaveTextContent(/ready|%/);
  rerender(composer({ ...initial, context_window: 1000000, capacity_source: "chatgpt_catalog" }));
  expect(screen.getByRole("button", { name: /context.*details/i })).toHaveTextContent("ready · 1M limit");
  rerender(composer({ ...contextUsage, input_tokens: 180000, context_window: 1000000 }));
  expect(screen.getByRole("button", { name: /context.*details/i })).toHaveTextContent("Context ~18%");
});
