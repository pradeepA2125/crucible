import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MarkdownContent } from "../components/shared/MarkdownContent";

const TABLE = [
  "| Provider | Status |",
  "| --- | --- |",
  "| nemotron | ok |",
].join("\n");

describe("MarkdownContent GFM support", () => {
  it("renders a pipe table as a real table, not literal pipes", () => {
    // react-markdown is CommonMark-only without remark-gfm, which renders this
    // whole block as one `| Provider | Status |` paragraph.
    const { container } = render(<MarkdownContent content={TABLE} />);

    expect(container.querySelector("table")).not.toBeNull();
    expect(screen.getByRole("columnheader", { name: "Provider" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "nemotron" })).toBeTruthy();
    expect(container.textContent).not.toContain("| Provider |");
  });

  it("scrolls a wide table inside its own box so the transcript never scrolls sideways", () => {
    const { container } = render(<MarkdownContent content={TABLE} />);

    const wrapper = container.querySelector("table")?.parentElement;
    expect(wrapper?.className).toContain("overflow-x-auto");
  });

  it("still renders plain CommonMark", () => {
    render(<MarkdownContent content="Some **bold** text" />);
    expect(screen.getByText("bold").tagName).toBe("STRONG");
  });
});
