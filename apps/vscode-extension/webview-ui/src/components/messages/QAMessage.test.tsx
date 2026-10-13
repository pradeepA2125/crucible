import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QAMessage } from "./QAMessage";

// The removed shared Avatar was the only element with h-5 + rounded-md.
const OLD_AVATAR = ".h-5.rounded-md";

describe("QAMessage (spec §2: no card, no border, no avatar)", () => {
  it("renders no avatar and no avatar spacer", () => {
    const { container } = render(<QAMessage content="Final answer." />);
    expect(container.querySelector("[data-testid='avatar-spacer']")).toBeNull();
    expect(container.querySelector(OLD_AVATAR)).toBeNull();
  });
});
