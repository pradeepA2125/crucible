import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { Icon } from "../components/Icon";
import type { IconName } from "../components/Icon";

const NEW_ICONS: IconName[] = ["home", "key", "plug", "book", "shield", "chip", "gear"];

describe("Icon — settings/composer additions", () => {
  it.each(NEW_ICONS)("renders %s as a non-empty svg", (name) => {
    const { container } = render(<Icon name={name} size={14} />);
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg!.innerHTML.length).toBeGreaterThan(10);
  });
});
