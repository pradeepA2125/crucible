import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentRow } from "../components/messages/AgentRow";
import { QAMessage } from "../components/messages/QAMessage";
import { ActivityLine } from "../components/activity/ActivityLine";
import { CopyButton } from "../components/shared/CopyButton";
import type { ToolEventView } from "../types";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const writeText = vi.fn<(text: string) => Promise<void>>();
const command: ToolEventView = { id: 1, tool: "run_command", args: {
  command: "npm test\nnpm run typecheck", cwd: "/workspace",
}, source: "execution", done: true, output: "41 passed\n" };

beforeEach(() => {
  writeText.mockReset().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
});
afterEach(() => vi.unstubAllGlobals());

describe("contextual copy actions", () => {
  it.each(["answer", "tools and answer"])("copies only %s prose, preserving its Markdown", async kind => {
    const content = "Use **this** answer.\n\n`npm test`";
    render(kind === "answer" ? <QAMessage content={content} />
      : <AgentRow content={content} toolEvents={[command]} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
    await screen.findByText("Copied");
    expect(writeText).toHaveBeenCalledExactlyOnceWith(content);
  });

  it("keeps command and output copying inside expanded command details", async () => {
    render(<ActivityLine event={command} />);
    expect(screen.queryByRole("button", { name: "Copy command" })).toBeNull();
    const disclosure = screen.getByRole("button", { name: /^Ran/ });
    fireEvent.click(disclosure);
    fireEvent.click(screen.getByRole("button", { name: "Copy command" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(command.args.command));
    fireEvent.click(screen.getByRole("button", { name: "Copy output" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(command.output));
    expect(disclosure).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByRole("button", { name: "Copy message" })).toBeNull();
  });

  it("copies tool arguments as JSON and does not offer an absent output", async () => {
    const args = { path: "src/a.ts", start_line: 10 };
    render(<ActivityLine event={{ ...command, tool: "read_file", args, done: false, output: undefined }} />);
    fireEvent.click(screen.getByRole("button", { name: /^Reading/ }));
    fireEvent.click(screen.getByRole("button", { name: "Copy input" }));
    await screen.findByText("Copied");
    expect(writeText).toHaveBeenCalledExactlyOnceWith(JSON.stringify(args, null, 2));
    expect(screen.queryByRole("button", { name: "Copy output" })).toBeNull();
  });

  it.each([{ content: "", toolEvents: [command] }, { content: "Still streaming", streaming: true }])(
    "does not put message copy on tool-only or streaming rows", props => {
      render(<AgentRow {...props} />);
      expect(screen.queryByRole("button", { name: "Copy message" })).toBeNull();
    });

  it("acknowledges a clipboard write only after it succeeds", async () => {
    let resolve!: () => void;
    writeText.mockImplementation(() => new Promise<void>(r => { resolve = r; }));
    render(<CopyButton text="exact content" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(screen.queryByText("Copied")).toBeNull();
    expect(screen.getByRole("button", { name: "Copy" })).toBeDisabled();
    await act(async () => resolve());
    expect(screen.getByText("Copied")).toBeInTheDocument();
  });

  it("reports clipboard denial and allows retry", async () => {
    writeText.mockRejectedValueOnce(new Error("denied"));
    render(<CopyButton text="exact content" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await screen.findByText("Copy failed");
    expect(screen.queryByText("Copied")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await screen.findByText("Copied");
    expect(writeText).toHaveBeenCalledTimes(2);
  });

  it("does not show success for old content after an in-flight copy", async () => {
    let resolve!: () => void;
    writeText.mockImplementation(() => new Promise<void>(r => { resolve = r; }));
    const view = render(<CopyButton text="old output" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    view.rerender(<CopyButton text="new output" />);
    await act(async () => resolve());
    expect(screen.queryByText("Copied")).toBeNull();
    expect(screen.getByRole("button", { name: "Copy" })).toBeEnabled();
  });
});
