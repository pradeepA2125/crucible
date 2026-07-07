import { fireEvent, render, screen, act } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { SearchBar } from "./SearchBar";
import type { StarRecord } from "../types";

function star(id: string): StarRecord {
  return {
    id,
    pkg: "apps/web",
    dir: "",
    symbolCount: 0,
    inDeg: 0,
    outDeg: 0,
    kindMix: {},
    isEntry: false,
    isHub: false,
  };
}

describe("SearchBar", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows instant file matches and debounces symbol queries", () => {
    const onQuerySymbols = vi.fn();
    render(
      <SearchBar
        stars={[star("apps/web/src/engine.ts"), star("apps/web/src/other.ts")]}
        symbolHits={[]}
        onQuerySymbols={onQuerySymbols}
        onGoFile={vi.fn()}
        onGoSymbol={vi.fn()}
      />
    );
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "engi" } });
    expect(screen.getByText("apps/web/src/engine.ts")).toBeTruthy();
    expect(onQuerySymbols).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(onQuerySymbols).toHaveBeenCalledWith("engi");
  });

  it("Enter selects the first result", () => {
    const onGoFile = vi.fn();
    render(
      <SearchBar
        stars={[star("apps/web/src/engine.ts")]}
        symbolHits={[]}
        onQuerySymbols={vi.fn()}
        onGoFile={onGoFile}
        onGoSymbol={vi.fn()}
      />
    );
    const input = screen.getByPlaceholderText(/search/i);
    fireEvent.change(input, { target: { value: "engine" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onGoFile).toHaveBeenCalledWith("apps/web/src/engine.ts");
  });

  it("symbol hits render beneath file hits with kind badges", () => {
    render(
      <SearchBar
        stars={[]}
        symbolHits={[{ symbolId: "class:x:Engine", name: "Engine", kind: "Class", fileId: "a.ts", line: 4 }]}
        onQuerySymbols={vi.fn()}
        onGoFile={vi.fn()}
        onGoSymbol={vi.fn()}
      />
    );
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: "eng" } });
    expect(screen.getByText("Engine")).toBeTruthy();
    expect(screen.getByText("Class")).toBeTruthy();
  });
});
