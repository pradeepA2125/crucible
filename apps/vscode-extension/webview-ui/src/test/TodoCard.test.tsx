import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { TodoCard } from "../components/messages/TodoCard";

describe("TodoCard", () => {
  it("bounds the item list to a slice of the viewport and scrolls it", () => {
    // A long ledger must not push the whole transcript off screen.
    const many = Array.from({ length: 30 }, (_, i) => ({
      title: `Task ${i}`, status: "pending" as const, note: "",
    }));
    const { container } = render(<TodoCard items={many} />);
    const list = container.querySelector("[data-todo-list]");
    expect(list).not.toBeNull();
    expect(list!.className).toContain("max-h-[40vh]");
    expect(list!.className).toContain("overflow-y-auto");
    // offsetTop of a row is measured against this box, so it must be positioned.
    expect(list!.className).toContain("relative");
    expect(list!.querySelectorAll("li").length).toBe(30);
  });

  it("keeps the header and progress bar outside the scrolling box", () => {
    const { container } = render(<TodoCard items={[
      { title: "A", status: "in_progress", note: "" },
    ]} />);
    const list = container.querySelector("[data-todo-list]")!;
    // The count lives in the card header; scrolling the items must not move it.
    expect(list.textContent).not.toContain("0/1");
    expect(screen.getByText("0/1")).toBeTruthy();
  });


  it("renders a progress header and each item with a status glyph", () => {
    render(<TodoCard items={[
      { title: "Enemies", status: "done", note: "" },
      { title: "Jump", status: "in_progress", note: "" },
      { title: "Timer", status: "pending", note: "" },
      { title: "Sound", status: "blocked", note: "needs asset" },
    ]} />);
    expect(screen.getByText("1/4")).toBeTruthy();   // done/total count (cancelled excluded from total)
    expect(screen.getByText("Enemies")).toBeTruthy();
    expect(screen.getByText("Jump")).toBeTruthy();
    expect(screen.getByText(/needs asset/i)).toBeTruthy();  // blocked reason shown
  });

  it("excludes cancelled items from the done/total count but still lists them", () => {
    render(<TodoCard items={[
      { title: "A", status: "done", note: "" },
      { title: "B", status: "cancelled", note: "superseded" },
    ]} />);
    expect(screen.getByText("1/1")).toBeTruthy();   // cancelled not counted in total
    expect(screen.getByText("B")).toBeTruthy();
  });
});
