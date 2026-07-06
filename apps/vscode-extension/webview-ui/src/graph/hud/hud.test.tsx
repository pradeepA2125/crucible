import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Breadcrumb } from "./Breadcrumb";
import { EdgeLayers } from "./EdgeLayers";
import { InfoCard } from "./InfoCard";

describe("EdgeLayers", () => {
  const layers = { Imports: true, Calls: true, Inherits: true, References: false };

  it("disables References below focus level 2", () => {
    render(<EdgeLayers layers={layers} focusLevel={0} onToggle={vi.fn()} />);
    expect(screen.getByRole("button", { name: /references/i })).toHaveProperty("disabled", true);
  });

  it("enables References at level 2 and reports toggles", () => {
    const onToggle = vi.fn();
    render(<EdgeLayers layers={layers} focusLevel={2} onToggle={onToggle} />);
    fireEvent.click(screen.getByRole("button", { name: /references/i }));
    expect(onToggle).toHaveBeenCalledWith("References", true);
  });
});

describe("Breadcrumb", () => {
  it("renders the focus path and pops on ancestor click", () => {
    const onPop = vi.fn();
    render(
      <Breadcrumb
        focus={{ level: 2, pkg: "apps/web", fileId: "apps/web/src/a.ts" }}
        onPop={onPop}
        onReset={vi.fn()}
      />
    );
    expect(screen.getByText("apps/web")).toBeTruthy();
    fireEvent.click(screen.getByText("apps/web"));
    expect(onPop).toHaveBeenCalled();
  });
});

describe("InfoCard", () => {
  it("shows rollups and fires open/dive", () => {
    const onOpen = vi.fn();
    const onDive = vi.fn();
    render(
      <InfoCard
        star={{
          id: "apps/web/src/a.ts",
          pkg: "apps/web",
          dir: "apps/web/src",
          symbolCount: 7,
          inDeg: 3,
          outDeg: 5,
          kindMix: {},
          isEntry: false,
          isHub: true,
        }}
        detail={null}
        onOpen={onOpen}
        onDive={onDive}
      />
    );
    expect(screen.getByText("a.ts")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /open in editor/i }));
    fireEvent.click(screen.getByRole("button", { name: /dive inside/i }));
    expect(onOpen).toHaveBeenCalled();
    expect(onDive).toHaveBeenCalled();
  });
});
