import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { UserMessage } from "./UserMessage";
import { vscode } from "../../vscodeApi";

vi.mock("../../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

describe("UserMessage mention rendering", () => {
  it("renders a tracked mention as clickable and posts openFile on click", () => {
    render(<UserMessage content="check @src/foo.py please" mentionedFiles={["src/foo.py"]} />);
    const link = screen.getByText("@src/foo.py");
    fireEvent.click(link);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openFile", path: "src/foo.py" });
  });

  it("does not linkify an @ token that isn't a tracked mention", () => {
    render(<UserMessage content="email me @not-a-file" mentionedFiles={[]} />);
    expect(screen.queryByRole("button", { name: "@not-a-file" })).toBeNull();
  });

  it("still renders backtick code spans unchanged", () => {
    render(<UserMessage content="run `ls -la` please" mentionedFiles={[]} />);
    expect(screen.getByText("ls -la").tagName).toBe("CODE");
  });
});

describe("UserMessage bubble (spec §2)", () => {
  it("is an accent-filled bubble with on-accent body text, at most 80% wide", () => {
    render(<UserMessage content="Add a retry" />);
    const bubble = screen.getByText("Add a retry").closest("[data-testid='user-bubble']") as HTMLElement;
    expect(bubble.style.background).toBe("var(--accent-fill)");
    expect(bubble.style.color).toBe("var(--on-accent)");
    expect(bubble.className).toContain("text-body");
    expect(bubble.className).toContain("rounded-composer");
    expect((bubble.parentElement as HTMLElement).className).toContain("max-w-[80%]");
  });

  it("renders a mention as a real button, readable on the accent fill, opening with the keyboard (Review Focus 4)", () => {
    render(<UserMessage content="look at @src/a.ts please" mentionedFiles={["src/a.ts"]} />);
    const mention = screen.getByRole("button", { name: "@src/a.ts" });
    expect(mention.tagName).toBe("BUTTON");
    expect(mention.style.color).toBe("var(--on-accent)");
    expect(mention.style.textDecoration).toBe("underline");
    mention.focus();
    fireEvent.click(mention);
    expect(vscode.postMessage).toHaveBeenCalledWith({ type: "openFile", path: "src/a.ts" });
  });

  it("shows the rewind action on keyboard focus as well as hover", () => {
    render(<UserMessage content="hi" onRewind={() => {}} />);
    const rewind = screen.getByRole("button", { name: "Rewind to here" });
    expect(rewind.className).toContain("group-hover:opacity-100");
    expect(rewind.className).toContain("group-focus-within:opacity-100");
    expect(rewind.className).toContain("focus-visible:opacity-100");
  });
});
