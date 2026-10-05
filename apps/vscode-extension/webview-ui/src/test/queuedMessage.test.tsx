import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

import { UserMessage } from "../components/messages/UserMessage";

describe("a queued user message (spec v2 §5.3)", () => {
  it("says it waits for the agent's next step", () => {
    render(<UserMessage content="make a team" queued />);
    expect(screen.getByTestId("queued-tag")).toHaveTextContent(
      "Queued — the agent picks this up at its next step");
  });

  it("an ordinary message has no tag", () => {
    render(<UserMessage content="hi" />);
    expect(screen.queryByTestId("queued-tag")).toBeNull();
  });
});
