import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SessionStrip } from "./SessionStrip";

const items = [{
  id: "sess-1",
  command: "python -m http.server 8765",
  status: "running" as const,
  exit_code: null,
  started_at: Date.now() / 1000 - 42,
}];

describe("SessionStrip", () => {
  it("renders a running session row with a locally computed age", () => {
    render(<SessionStrip items={items} transcripts={{}} onExpand={() => {}} />);
    expect(screen.getByText(/http\.server 8765/)).toBeTruthy();
    expect(screen.getByText(/running/)).toBeTruthy();
    expect(screen.getByText(/42s/)).toBeTruthy(); // age from started_at, not a backend field
  });

  it("renders exited state with exit code", () => {
    render(
      <SessionStrip
        items={[{ ...items[0], status: "exited" as const, exit_code: 1 }]}
        transcripts={{}}
        onExpand={() => {}}
      />
    );
    expect(screen.getByText(/exited/)).toBeTruthy();
    expect(screen.getByText(/exit 1/)).toBeTruthy();
  });

  it("expand requests the transcript and renders it when supplied", () => {
    const onExpand = vi.fn();
    const { rerender } = render(
      <SessionStrip items={items} transcripts={{}} onExpand={onExpand} />);
    fireEvent.click(screen.getByText(/http\.server 8765/));
    expect(onExpand).toHaveBeenCalledWith("sess-1");
    rerender(<SessionStrip items={items} onExpand={onExpand} transcripts={{
      "sess-1": {
        output_tail: "Serving HTTP on :: port 8765",
        stdin_history: [{ ts: 1, chars: "y\n" }],
        status: "running", exit_code: null,
      },
    }} />);
    expect(screen.getByText(/Serving HTTP/)).toBeTruthy();
    expect(screen.getByText(/y\\n/)).toBeTruthy(); // control chars rendered escaped
  });

  it("shows unavailable when the transcript fetch failed (null)", () => {
    render(
      <SessionStrip items={items} transcripts={{ "sess-1": null }} onExpand={() => {}} />);
    fireEvent.click(screen.getByText(/http\.server 8765/));
    expect(screen.getByText(/unavailable/i)).toBeTruthy();
  });

  it("renders nothing for an empty list", () => {
    const { container } = render(
      <SessionStrip items={[]} transcripts={{}} onExpand={() => {}} />);
    expect(container.firstChild).toBeNull();
  });
});
