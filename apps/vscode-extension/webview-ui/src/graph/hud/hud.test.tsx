import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Breadcrumb } from "./Breadcrumb";
import { EdgeLayers } from "./EdgeLayers";
import { InfoCard } from "./InfoCard";
import { ThemePanel } from "./ThemePanel";
import { ViewPanel } from "./ViewPanel";

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

const STAR = {
  id: "apps/web/src/a.ts",
  pkg: "apps/web",
  dir: "apps/web/src",
  symbolCount: 7,
  inDeg: 3,
  outDeg: 5,
  kindMix: {},
  isEntry: false,
  isHub: true,
} as const;

describe("InfoCard", () => {
  it("shows rollups and fires open/dive", () => {
    const onOpen = vi.fn();
    const onDive = vi.fn();
    render(<InfoCard star={STAR} detail={null} onOpen={onOpen} onDive={onDive} onGoEdge={vi.fn()} />);
    expect(screen.getByText("a.ts")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /open in editor/i }));
    fireEvent.click(screen.getByRole("button", { name: /dive inside/i }));
    expect(onOpen).toHaveBeenCalled();
    expect(onDive).toHaveBeenCalled();
  });

  it("lists deduped connections and rides an edge on click", () => {
    const onGoEdge = vi.fn();
    render(
      <InfoCard
        star={STAR}
        detail={{
          fileId: "apps/web/src/a.ts",
          symbols: [],
          withinFileCount: 2,
          edges: [
            { dir: "out", kind: "Imports", otherFile: "apps/web/src/b.ts", crossPackage: false },
            { dir: "out", kind: "Imports", otherFile: "apps/web/src/b.ts", crossPackage: false },
            { dir: "in", kind: "Calls", otherFile: "services/api/m.py", crossPackage: true, symbolName: "run", line: 12 },
          ],
        }}
        onOpen={vi.fn()}
        onDive={vi.fn()}
        onGoEdge={onGoEdge}
      />
    );
    // deduped: b.ts appears once with a ×2 count
    const bRow = screen.getByRole("button", { name: /go to apps\/web\/src\/b\.ts/i });
    expect(bRow.textContent).toContain("×2");
    // incoming row carries the symbol context
    const mRow = screen.getByRole("button", { name: /go to services\/api\/m\.py/i });
    expect(mRow.textContent).toContain("run:12");
    fireEvent.click(bRow);
    expect(onGoEdge).toHaveBeenCalledWith("apps/web/src/b.ts");
  });

  it("switches to symbol edges when a symbol is picked", () => {
    const onGoEdge = vi.fn();
    render(
      <InfoCard
        star={STAR}
        detail={{ fileId: "apps/web/src/a.ts", symbols: [], withinFileCount: 0, edges: [] }}
        symbolDetail={{
          symbolId: "class:file:/ws/apps/web/src/a.ts:A",
          edges: [
            { dir: "out", kind: "Calls", name: "serve", fileId: "services/api/m.py", line: 9 },
            { dir: "in", kind: "Calls", name: "boot", fileId: null },
          ],
        }}
        onOpen={vi.fn()}
        onDive={vi.fn()}
        onGoEdge={onGoEdge}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /go to services\/api\/m\.py/i }));
    expect(onGoEdge).toHaveBeenCalledWith("services/api/m.py");
    // an unresolvable symbol edge renders but is not clickable
    expect(screen.getByText(/boot/).closest("button")).toBeNull();
  });
});

describe("ViewPanel", () => {
  it("fires overview always; gates enter-a-file to level 2", () => {
    const onOverview = vi.fn();
    const onEnterFile = vi.fn();
    const { rerender } = render(
      <ViewPanel
        focusLevel={0}
        canTraceHub={true}
        canRideBeam={true}
        onOverview={onOverview}
        onTraceHub={vi.fn()}
        onRideBeam={vi.fn()}
        onEnterFile={onEnterFile}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /overview/i }));
    expect(onOverview).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /enter a file/i })).toHaveProperty("disabled", true);
    rerender(
      <ViewPanel
        focusLevel={2}
        canTraceHub={true}
        canRideBeam={true}
        onOverview={onOverview}
        onTraceHub={vi.fn()}
        onRideBeam={vi.fn()}
        onEnterFile={onEnterFile}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /enter a file/i }));
    expect(onEnterFile).toHaveBeenCalled();
  });

  it("disables ride/hub when unavailable", () => {
    render(
      <ViewPanel
        focusLevel={0}
        canTraceHub={false}
        canRideBeam={false}
        onOverview={vi.fn()}
        onTraceHub={vi.fn()}
        onRideBeam={vi.fn()}
        onEnterFile={vi.fn()}
      />
    );
    expect(screen.getByRole("button", { name: /ride a beam/i })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: /trace the hub/i })).toHaveProperty("disabled", true);
  });
});

describe("ThemePanel", () => {
  it("marks the active palette and reports switches", () => {
    const onPalette = vi.fn();
    render(<ThemePanel palette="void" onPalette={onPalette} />);
    expect(screen.getByRole("button", { name: /void violet/i }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /ember dusk/i }));
    expect(onPalette).toHaveBeenCalledWith("ember");
  });
});
