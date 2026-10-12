import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { InputArea } from "../components/InputArea";
import { UserMessage } from "../components/messages/UserMessage";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

const PNG = "data:image/png;base64,iVBORw0KGgo=";

describe("image previews (above the composer field and above the message)", () => {
  it("shows attachment tiles above the text field, each removable", async () => {
    render(
      <InputArea
        availability={{ disabled: false, placeholder: "Message", showStop: false, taskStop: false }}
        draft="" onDraftChange={vi.fn()} onOpenSettings={vi.fn()} threadUsage={null} teamNames={{}}
      />,
    );
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["png"], "shot.png", { type: "image/png" })] } });

    await screen.findByAltText("shot.png");  // the strip shows a pending tile first
    const strip = screen.getByTestId("composer-images");
    const textarea = screen.getByRole("textbox");
    // The strip precedes the field in document order: it renders above it.
    expect(strip.compareDocumentPosition(textarea) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(within(strip).getByRole("button", { name: "Remove shot.png" }));
    await waitFor(() => expect(screen.queryByTestId("composer-images")).not.toBeInTheDocument());
  });

  it("puts a sent message's images above its bubble and opens one full size", () => {
    render(<UserMessage content="what is this?" images={[{ name: "shot.png", dataUrl: PNG }]} />);
    const images = screen.getByTestId("message-images");
    const text = screen.getByText("what is this?");
    expect(images.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Open shot.png" }));
    expect(screen.getByRole("dialog", { name: "shot.png" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders no empty bubble for an image-only message", () => {
    const { container } = render(<UserMessage content="" images={[{ name: "a.png", dataUrl: PNG }]} />);
    expect(screen.getByTestId("message-images")).toBeInTheDocument();
    expect(container.querySelector(".whitespace-pre-wrap")).toBeNull();
  });
});
