import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
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

describe("MarkdownContent as plain prose (spec §2)", () => {
  it("has no box: no border, background or radius", () => {
    const { container } = render(<MarkdownContent content="Plain answer." />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.border).toBe("");
    expect(root.style.background).toBe("");
    expect(root.style.borderRadius).toBe("");
  });

  it("uses the prose size, primary text and relaxed line height", () => {
    const { container } = render(<MarkdownContent content="Plain answer." />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain("text-prose");
    expect(root.className).toContain("text-text");
    expect(root.className).toContain("leading-[1.65]");
    expect(root.className).toContain("[&_p]:[text-wrap:pretty]");
  });

  it("renders inline code through the shared accent highlight at prose size", () => {
    const { container } = render(<MarkdownContent content="Use `retry()` here." />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain("message-markdown");
    expect(root.className).not.toContain(":not(pre)>code]");
    const css = readFileSync(join(__dirname, "..", "index.css"), "utf8");
    const rule = css.slice(css.indexOf(".message-markdown :not(pre) > code {"));
    expect(rule).toMatch(/^[^}]*background: var\(--accent-bg\);[^}]*font-size: inherit;/);
  });

  it("still wraps a very long unbroken token (Review Focus 1)", () => {
    const { container } = render(<MarkdownContent content={"x".repeat(400)} />);
    expect((container.firstElementChild as HTMLElement).className).toContain("[overflow-wrap:anywhere]");
  });
});
