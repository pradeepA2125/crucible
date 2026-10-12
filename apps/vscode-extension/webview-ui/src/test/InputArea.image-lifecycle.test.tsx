import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InputArea } from "../components/InputArea";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const availability = { disabled: false, placeholder: "Message", showStop: false, taskStop: false };
beforeEach(() => vi.clearAllMocks());
function input(key: string) { return <InputArea key={key} availability={availability} draft="" onDraftChange={vi.fn()} />; }
function attach(files: File[]) { fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files } }); }
describe("image draft lifecycle", () => {
  it("keeps different content with identical filename and size", async () => {
    render(input("t1"));
    attach([new File(["a"], "same.png", { type: "image/png" }), new File(["b"], "same.png", { type: "image/png" })]);
    await waitFor(() => expect(screen.getByText("2 images")).toBeInTheDocument());
  });
  it("does not carry image drafts into a new thread", async () => {
    const view = render(input("t1"));
    attach([new File(["a"], "one.png", { type: "image/png" })]);
    await waitFor(() => expect(screen.getByText("1 image")).toBeInTheDocument());
    view.rerender(input("t2"));
    expect(screen.queryByText("1 image")).not.toBeInTheDocument();
  });
  it("rejects an oversized file visibly", () => {
    render(input("t1"));
    attach([new File([new Uint8Array(8 * 1024 * 1024 + 1)], "big.png", { type: "image/png" })]);
    expect(screen.getByText(/Use up to 8 supported images/)).toBeInTheDocument();
  });
});
