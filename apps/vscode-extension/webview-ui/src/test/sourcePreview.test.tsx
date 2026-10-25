import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { SourcePreview } from "../desktop/SourcePreview";
import { fileIcon } from "../desktop/fileIcons";
it("numbers source without HTML execution and bounds line DOM with honest paging", () => {
  const text = Array.from({ length: 2005 }, (_, index) => `line ${index + 1}`).join("\n");
  const { container } = render(<SourcePreview path="sample.ts" text={text} wrap />);
  expect(container.querySelectorAll(".desktop-source-line")).toHaveLength(2000); expect(screen.getByText("Lines 1–2000 of 2,005")).toBeInTheDocument();
  expect(container.querySelector(".desktop-line-number")).toHaveAttribute("aria-hidden", "true");
  fireEvent.click(screen.getByRole("button", { name: "Next lines" })); expect(container.querySelectorAll(".desktop-source-line")).toHaveLength(5); expect(screen.getByText("line 2005")).toBeInTheDocument();
});
it("maps icon hints by extension only", () => {
  expect(fileIcon("a.PNG")).toBe("image"); expect(fileIcon("a.html")).toBe("hash"); expect(fileIcon("a.json")).toBe("braces"); expect(fileIcon("a.md")).toBe("markdown"); expect(fileIcon("a.unknown")).toBe("file");
});
