import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { TriggerDropdown } from "./TriggerDropdown";

const items = [
  { id: "review", label: "review", badge: "Prompt" },
  { id: "git-commit", label: "git-commit", sublabel: "Commit staged changes", badge: "Skill" },
];

describe("TriggerDropdown", () => {
  it("renders each item's label, sublabel, and badge", () => {
    render(<TriggerDropdown items={items} activeIndex={0} onHover={() => {}} onSelect={() => {}} />);
    expect(screen.getByText("review")).toBeTruthy();
    expect(screen.getByText("Prompt")).toBeTruthy();
    expect(screen.getByText("Commit staged changes")).toBeTruthy();
    expect(screen.getByText("Skill")).toBeTruthy();
  });

  it("calls onSelect with the clicked item's id", () => {
    const onSelect = vi.fn();
    render(<TriggerDropdown items={items} activeIndex={0} onHover={() => {}} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("git-commit"));
    expect(onSelect).toHaveBeenCalledWith("git-commit");
  });

  it("marks the active-index row for keyboard navigation styling", () => {
    render(<TriggerDropdown items={items} activeIndex={1} onHover={() => {}} onSelect={() => {}} />);
    expect(screen.getByTestId("trigger-item-git-commit").getAttribute("data-active")).toBe("true");
    expect(screen.getByTestId("trigger-item-review").getAttribute("data-active")).toBe("false");
  });

  it("renders nothing for an empty item list", () => {
    const { container } = render(
      <TriggerDropdown items={[]} activeIndex={0} onHover={() => {}} onSelect={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
