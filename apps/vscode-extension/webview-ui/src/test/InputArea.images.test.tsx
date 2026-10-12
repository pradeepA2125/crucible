import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InputArea } from "../components/InputArea";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

let postMessage: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  const mod = await import("../vscodeApi");
  postMessage = mod.vscode.postMessage as ReturnType<typeof vi.fn>;
  postMessage.mockClear();
});

function renderInput(draft = "") {
  return render(
    <InputArea
      availability={{ disabled: false, placeholder: "Message", showStop: false, taskStop: false }}
      draft={draft}
      onDraftChange={vi.fn()}
      onOpenSettings={vi.fn()}
      threadUsage={null}
      teamNames={{}}
    />,
  );
}

describe("InputArea image support", () => {
  it("renders an image attachment control", () => {
    renderInput();

    expect(screen.getByRole("button", { name: "Attach images" })).toBeInTheDocument();
    const fileInput = document.querySelector('input[type="file"][accept*="image/png"]');
    expect(fileInput).not.toBeNull();
  });

  it("converts an attached image file to a data URL and queues it", async () => {
    renderInput();
    const file = new File(["png bytes"], "diagram.png", { type: "image/png" });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;

    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => expect(screen.getByText("1 image")).toBeInTheDocument());
    expect(screen.getByTitle("diagram.png")).toBeInTheDocument();
  });

  it("converts a pasted image and sends it without text", async () => {
    const onDraftChange = vi.fn();
    render(
      <InputArea
        availability={{ disabled: false, placeholder: "Message", showStop: false, taskStop: false }}
        draft=""
        onDraftChange={onDraftChange}
        onOpenSettings={vi.fn()}
        threadUsage={null}
        teamNames={{}}
      />,
    );
    const file = new File(["paste bytes"], "pasted.png", { type: "image/png" });
    const textarea = screen.getByRole("textbox");

    fireEvent.paste(textarea, { clipboardData: { files: [file] } });

    await waitFor(() => expect(screen.getByText("1 image")).toBeInTheDocument());
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });

    expect(postMessage).toHaveBeenCalledWith({
      type: "sendMessage",
      draftId: expect.any(String),
      originalDraftText: "",
      text: "",
      stepReview: true,
      planMode: false,
      imageAttachments: [
        expect.objectContaining({
          name: "pasted.png",
          mediaType: "image/png",
          bytes: file.size,
          dataUrl: expect.stringMatching(/^data:image\/png;base64,/),
        }),
      ],
    });
    expect(onDraftChange).toHaveBeenCalledWith("");
  });
});