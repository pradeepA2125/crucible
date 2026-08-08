import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ToolPill } from "../components/shared/ToolPill";
import { ToolDetailPanel } from "../components/shared/ToolDetailPanel";
import { ThinkingBlock } from "../components/shared/ThinkingBlock";
import { AgentRow } from "../components/messages/AgentRow";
import { UserMessage } from "../components/messages/UserMessage";
import { MessageRow } from "../components/MessageRow";
import type { ToolEventView } from "../types";

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeEvent(overrides: Partial<ToolEventView> = {}): ToolEventView {
  return {
    id: 1,
    tool: "read_file",
    args: { path: "src/foo.ts", start_line: 10 },
    source: "execution",
    done: false,
    ...overrides,
  };
}

// ── 1. ToolPill running ───────────────────────────────────────────────────────

describe("ToolPill", () => {
  it("running: shows the tool name and does not call onToggle when clicked", () => {
    const onToggle = vi.fn();
    const event = makeEvent({ done: false });
    render(<ToolPill event={event} expanded={false} onToggle={onToggle} />);

    expect(screen.getByText("read_file")).toBeTruthy();
    fireEvent.click(screen.getByRole("button"));
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("done: clicking calls onToggle", () => {
    const onToggle = vi.fn();
    const event = makeEvent({ done: true, output: "line 1\nline 2" });
    render(<ToolPill event={event} expanded={false} onToggle={onToggle} />);

    fireEvent.click(screen.getByRole("button"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("done: reflects expanded state on aria-expanded", () => {
    const event = makeEvent({ done: true });
    const { rerender } = render(
      <ToolPill event={event} expanded={false} onToggle={() => {}} />
    );
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("false");

    rerender(<ToolPill event={event} expanded={true} onToggle={() => {}} />);
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");
  });

  it("never renders the detail panel itself", () => {
    const event = makeEvent({ done: true, output: "line 1" });
    render(<ToolPill event={event} expanded={true} onToggle={() => {}} />);
    expect(screen.queryByText("INPUT")).toBeNull();
    expect(screen.queryByText("OUTPUT")).toBeNull();
  });
});

describe("ToolDetailPanel", () => {
  it("renders the tool name, input args and output", () => {
    const event = makeEvent({
      done: true,
      isError: false,
      args: { path: "src/foo.ts" },
      output: "line 1\nline 2",
    });
    render(<ToolDetailPanel event={event} />);

    expect(screen.getByText("INPUT")).toBeTruthy();
    expect(screen.getByText("OUTPUT")).toBeTruthy();
    expect(screen.getByText("path:")).toBeTruthy();
    expect(screen.getByText(/line 1/)).toBeTruthy();
    // Line count badge comes from the output.
    expect(screen.getByText("2 lines")).toBeTruthy();
  });

  it("renders error output", () => {
    const event = makeEvent({
      done: true,
      isError: true,
      args: { cmd: "npm test" },
      output: "FAIL src/foo.ts",
    });
    render(<ToolDetailPanel event={event} />);
    expect(screen.getByText(/FAIL src\/foo\.ts/)).toBeTruthy();
  });
});

// ── 2. ThinkingBlock ──────────────────────────────────────────────────────────

describe("ThinkingBlock", () => {
  it("streaming: shows Thinking label", () => {
    render(<ThinkingBlock entries={[]} streaming={true} />);
    expect(screen.getByText(/Thinking/)).toBeTruthy();
  });

  it("idle with entries: shows count, collapsed by default, click expands", () => {
    render(<ThinkingBlock entries={["step one", "step two"]} streaming={false} />);
    expect(screen.getByText("Thinking (2 steps)")).toBeTruthy();

    // Detail not visible before click.
    expect(screen.queryByText("step one")).toBeNull();

    // Click the header pill.
    const btn = screen.getByRole("button");
    fireEvent.click(btn);

    // Entries visible after expand.
    expect(screen.getByText(/step one/)).toBeTruthy();
    expect(screen.getByText(/step two/)).toBeTruthy();
  });

  it("streaming → false: collapses via useEffect", () => {
    const { rerender } = render(
      <ThinkingBlock entries={["loaded weights"]} streaming={true} />
    );
    // Initially OPEN because streaming=true — detail visible without any click.
    expect(screen.getByText(/loaded weights/)).toBeTruthy();
    // Streaming ends → the effect auto-collapses the detail.
    rerender(<ThinkingBlock entries={["loaded weights"]} streaming={false} />);
    expect(screen.queryByText(/loaded weights/)).toBeNull();
  });
});

// ── 3. AgentRow breadcrumb ────────────────────────────────────────────────────

describe("AgentRow", () => {
  it("breadcrumb: renders text without leading marker character appearing twice", () => {
    render(
      <AgentRow content="✓ Plan approved" breadcrumb={true} />
    );
    const textEl = screen.getByText("Plan approved");
    expect(textEl).toBeTruthy();

    // The literal "✓" should NOT appear in the text node.
    expect(textEl.textContent).not.toContain("✓");
  });

  it("renders tool events on a track rather than a flat wrap", () => {
    const events: ToolEventView[] = [
      makeEvent({ id: 1, done: true, isError: false, output: "ok" }),
      makeEvent({ id: 2, tool: "search_code", done: true, isError: false, output: "ok" }),
    ];
    const { container } = render(<AgentRow content="" toolEvents={events} />);
    expect(container.querySelectorAll("[data-row]").length).toBeGreaterThan(0);
  });

  it("renders no track when the turn made no tool calls", () => {
    const { container } = render(<AgentRow content="done" />);
    expect(container.querySelector("[data-row]")).toBeNull();
  });
});

// ── 4. UserMessage backtick rendering ────────────────────────────────────────

describe("UserMessage", () => {
  it("renders inline backtick spans as <code> elements", () => {
    const { container } = render(
      <UserMessage content="Run `npm install` to start" />
    );
    const codeEls = container.querySelectorAll("code");
    expect(codeEls.length).toBe(1);
    expect(codeEls[0].textContent).toBe("npm install");
  });

  it("renders plain text without code when no backticks", () => {
    const { container } = render(<UserMessage content="Hello world" />);
    expect(container.querySelectorAll("code").length).toBe(0);
    expect(screen.getByText("Hello world")).toBeTruthy();
  });
});

// ── 5. MessageRow progress ────────────────────────────────────────────────────

describe("MessageRow", () => {
  it("renders a progress-tagged message as a muted progress line", () => {
    const { getByText } = render(
      <MessageRow
        msg={{
          role: "agent",
          content: "Implementing task 1.",
          type: "text",
          timestamp: new Date(0).toISOString(),
          metadata: { progress: true },
        }}
      />,
    );
    const line = getByText("Implementing task 1.");
    expect(line).toBeTruthy();
    // progress line is muted (text-text-3), distinct from a normal answer
    expect(line.className).toContain("text-text-3");
  });
});
