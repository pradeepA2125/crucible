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
