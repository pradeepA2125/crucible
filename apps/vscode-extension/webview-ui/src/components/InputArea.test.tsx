import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InputArea } from "./InputArea";
import { vscode } from "../vscodeApi";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const availability = {
  disabled: false,
  showStop: false,
  taskStop: false,
  placeholder: "Message",
} as const;

function Harness() {
  const [draft, setDraft] = useState("/review src/a.py");
  return <InputArea availability={availability} draft={draft} onDraftChange={setDraft} />;
}

function EmptyHarness() {
  const [draft, setDraft] = useState("");
  return <InputArea availability={availability} draft={draft} onDraftChange={setDraft} />;
}

describe("InputArea slash-command expansion", () => {
  beforeEach(() => vi.clearAllMocks());

  it("Enter on a /name command posts expandPrompt, not sendMessage", () => {
    render(<Harness />);
    const ta = screen.getByLabelText("Chat input");
    fireEvent.keyDown(ta, { key: "Enter" });
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual({ type: "expandPrompt", name: "review", args: "src/a.py" });
    expect(calls.find((c) => c.type === "sendMessage")).toBeUndefined();
  });

  it("a promptExpanded message replaces the draft", () => {
    render(<Harness />);
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "promptExpanded", name: "review", found: true, text: "Review src/a.py" },
        })
      );
    });
    const ta = screen.getByLabelText("Chat input") as HTMLTextAreaElement;
    expect(ta.value).toBe("Review src/a.py");
  });

  it("an unmatched slash command (found=false) sends the original text, not a dead-end", () => {
    render(<Harness />);
    const ta = screen.getByLabelText("Chat input");
    // Enter posts expandPrompt and stashes the original text.
    fireEvent.keyDown(ta, { key: "Enter" });
    // Host: no such prompt.
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "promptExpanded", name: "review", found: false, text: "" },
        })
      );
    });
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual({
      type: "sendMessage",
      text: "/review src/a.py",
      stepReview: true,
    });
    // And the composer is cleared (message left the box).
    expect((ta as HTMLTextAreaElement).value).toBe("");
  });

  it("renders the model chip and the settings gear", () => {
    render(<Harness />);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "listModels" });
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openSettings" });
  });
});

describe("InputArea unified / dropdown", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows prompt and skill rows after typing / and requests both catalogs", () => {
    render(<EmptyHarness />);
    const ta = screen.getByLabelText("Chat input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "/" } });
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual({ type: "listSkills" });
    expect(calls).toContainEqual({ type: "listPrompts" });

    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "promptList", names: ["review"] } }));
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: "skillList", skills: [{ name: "git-commit", description: "Commit staged changes" }] },
      }));
    });
    expect(screen.getByText("review")).toBeTruthy();
    expect(screen.getByText("git-commit")).toBeTruthy();
    expect(screen.getByText("Prompt")).toBeTruthy();
    expect(screen.getByText("Skill")).toBeTruthy();
  });

  it("Enter on a dropdown row inserts the name without sending", () => {
    render(<EmptyHarness />);
    const ta = screen.getByLabelText("Chat input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "/" } });
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "promptList", names: ["review"] } }));
      window.dispatchEvent(new MessageEvent("message", { data: { type: "skillList", skills: [] } }));
    });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(ta.value).toBe("/review ");
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls.find((c) => c.type === "sendMessage")).toBeUndefined();
  });
});

describe("InputArea @-file mentions", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows matching files after typing @ and requests the file list once", () => {
    render(<EmptyHarness />);
    const ta = screen.getByLabelText("Chat input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "look at @src" } });
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual({ type: "listWorkspaceFiles" });

    act(() => {
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: "workspaceFileList", paths: ["src/foo.py", "src/bar.py", "readme.md"] },
      }));
    });
    expect(screen.getByText("src/foo.py")).toBeTruthy();
    expect(screen.getByText("src/bar.py")).toBeTruthy();
    expect(screen.queryByText("readme.md")).toBeNull();
  });

  it("selecting a file inserts @path and sending includes mentionedPaths", () => {
    render(<EmptyHarness />);
    const ta = screen.getByLabelText("Chat input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "@src" } });
    act(() => {
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: "workspaceFileList", paths: ["src/foo.py"] },
      }));
    });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(ta.value).toBe("@src/foo.py ");

    fireEvent.change(ta, { target: { value: "@src/foo.py look here" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContainEqual({
      type: "sendMessage", text: "@src/foo.py look here", stepReview: true,
      mentionedPaths: ["src/foo.py"],
    });
  });

  it("does not send mentionedPaths for a mention the user deleted before sending", () => {
    render(<EmptyHarness />);
    const ta = screen.getByLabelText("Chat input") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "@src" } });
    act(() => {
      window.dispatchEvent(new MessageEvent("message", {
        data: { type: "workspaceFileList", paths: ["src/foo.py"] },
      }));
    });
    fireEvent.keyDown(ta, { key: "Enter" });
    fireEvent.change(ta, { target: { value: "never mind" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    const calls = (vscode.postMessage as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    const sent = calls.find((c) => c.type === "sendMessage");
    expect(sent.mentionedPaths).toBeUndefined();
  });
});
