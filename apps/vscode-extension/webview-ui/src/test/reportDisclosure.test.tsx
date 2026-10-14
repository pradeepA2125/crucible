import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { closeOpenFence, ReportDisclosure } from "../components/shared/ReportDisclosure";

vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));

describe("ReportDisclosure", () => {
  it("shows short markdown in full without extra controls", () => {
    render(<ReportDisclosure text="Verified **all callers**." label="review report" />);
    expect(screen.getByText("all callers").tagName).toBe("STRONG");
    expect(screen.queryByRole("button", { name: /Show more/ })).toBeNull();
  });

  it("folds reports with many short lines, exposes full content, and restores the excerpt", () => {
    const text = "Summary\n\n" + Array.from({ length: 10 }, (_, i) => `- Item ${i}`).join("\n");
    render(<ReportDisclosure text={text} label="review report" />);
    expect(screen.queryByText("Item 9")).toBeNull();
    const more = screen.getByRole("button", { name: "Show more of review report" });
    expect(document.getElementById(more.getAttribute("aria-controls")!)).toBeInTheDocument();
    fireEvent.click(more);
    expect(screen.getByText("Item 9")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Show less of review report" }));
    expect(screen.queryByText("Item 9")).toBeNull();
  });

  it("starts a replacement report collapsed, and uses distinct disclosure ids", () => {
    const first = "First report ".repeat(60) + "FIRST_END";
    const second = "Second report ".repeat(60) + "SECOND_END";
    const { rerender, container } = render(<ReportDisclosure text={first} label="review report" />);
    fireEvent.click(screen.getByRole("button", { name: "Show more of review report" }));
    expect(container.textContent).toContain("FIRST_END");
    rerender(<ReportDisclosure text={second} label="review report" />);
    expect(container.textContent).not.toContain("SECOND_END");
    expect(screen.getByRole("button", { name: "Show more of review report" })).toHaveAttribute("aria-expanded", "false");
    rerender(<><ReportDisclosure text={second} label="review report" /><ReportDisclosure text={first} label="helper report" /></>);
    const controls = screen.getAllByRole("button").map(button => button.getAttribute("aria-controls"));
    expect(new Set(controls).size).toBe(2);
  });

  it("closes a code fence the excerpt cut open, so later prose is not rendered as code", () => {
    expect(closeOpenFence("Ran:\n```bash\nnpm test")).toBe("Ran:\n```bash\nnpm test\n```\n\n…");
    expect(closeOpenFence("Ran:\n```bash\nnpm test\n```\nDone")).toBe("Ran:\n```bash\nnpm test\n```\nDone…");
  });
});
