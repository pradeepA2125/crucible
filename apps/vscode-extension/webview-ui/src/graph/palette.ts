// AXON palettes — ported verbatim from the approved motion study
// (.superpowers/brainstorm/29677-1783313740/content/axon-design-language.html).
// Scene colors bake into GPU buffers at build time, so a palette switch rebuilds
// the scene; HUD colors ride CSS variables set on the panel root.
import type { EdgeKind } from "./types";

export interface Palette {
  name: string;
  bgTop: string;
  bgBot: string;
  clusterTints: string[];
  star: string;
  beacon: string;
  kinds: Record<EdgeKind, string>;
  out: string;
  inn: string;
  nebulaAlpha: number;
  // HUD glass tokens
  panel: string;
  panelBorder: string;
  ink: string;
  inkDim: string;
  accent: string;
  accentText: string;
}

export const PALETTES = {
  void: {
    name: "Void Violet",
    bgTop: "#08051a",
    bgBot: "#03020a",
    clusterTints: ["#a78bfa", "#67e8f9", "#f0abfc", "#818cf8", "#5eead4"],
    star: "#efedff",
    beacon: "#fbbf24",
    kinds: { Imports: "#a78bfa", Calls: "#67e8f9", Inherits: "#f0abfc", References: "#8b93b5" },
    out: "#67e8f9",
    inn: "#f0abfc",
    nebulaAlpha: 0.05,
    panel: "rgba(15, 12, 30, 0.55)",
    panelBorder: "rgba(167, 139, 250, 0.22)",
    ink: "#e9e6ff",
    inkDim: "rgba(233, 230, 255, 0.55)",
    accent: "#a78bfa",
    accentText: "#0d0a1f",
  },
  abyss: {
    name: "Abyss Cyan",
    bgTop: "#03121c",
    bgBot: "#010609",
    clusterTints: ["#22d3ee", "#60a5fa", "#2dd4bf", "#38bdf8", "#a5b4fc"],
    star: "#e6fbff",
    beacon: "#f59e0b",
    kinds: { Imports: "#60a5fa", Calls: "#22d3ee", Inherits: "#2dd4bf", References: "#7896ad" },
    out: "#22d3ee",
    inn: "#f59e0b",
    nebulaAlpha: 0.045,
    panel: "rgba(2, 18, 28, 0.55)",
    panelBorder: "rgba(34, 211, 238, 0.22)",
    ink: "#e6fbff",
    inkDim: "rgba(230, 251, 255, 0.55)",
    accent: "#22d3ee",
    accentText: "#03121c",
  },
  ember: {
    name: "Ember Dusk",
    bgTop: "#160709",
    bgBot: "#070203",
    clusterTints: ["#fb923c", "#f472b6", "#fbbf24", "#e879f9", "#93c5fd"],
    star: "#fff4ea",
    beacon: "#fde047",
    kinds: { Imports: "#fb923c", Calls: "#fbbf24", Inherits: "#f472b6", References: "#a58d92" },
    out: "#fbbf24",
    inn: "#f472b6",
    nebulaAlpha: 0.055,
    panel: "rgba(22, 7, 9, 0.6)",
    panelBorder: "rgba(251, 146, 60, 0.22)",
    ink: "#fff4ea",
    inkDim: "rgba(255, 244, 234, 0.55)",
    accent: "#fb923c",
    accentText: "#160709",
  },
} satisfies Record<string, Palette>;

export type PaletteName = keyof typeof PALETTES;

export const DEFAULT_PALETTE: PaletteName = "void";

export function isPaletteName(v: unknown): v is PaletteName {
  return typeof v === "string" && v in PALETTES;
}
