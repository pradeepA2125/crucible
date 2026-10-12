import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QAMessage } from "./QAMessage";

const AVATAR_SELECTOR = ".h-5.rounded-md";

describe("QAMessage avatar", () => {
  it("renders the spacer and no avatar when showAvatar is false", () => {
    const { container } = render(<QAMessage content="Final answer." showAvatar={false} />);
    expect(container.querySelector("[data-testid='avatar-spacer']")).not.toBeNull();
    expect(container.querySelector(AVATAR_SELECTOR)).toBeNull();
  });

  it("renders the avatar and no spacer by default", () => {
    const { container } = render(<QAMessage content="Final answer." />);
    expect(container.querySelector(AVATAR_SELECTOR)).not.toBeNull();
    expect(container.querySelector("[data-testid='avatar-spacer']")).toBeNull();
  });
});
