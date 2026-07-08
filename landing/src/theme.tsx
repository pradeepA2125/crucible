import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

export type ThemeId = "violet" | "cyan" | "ember";

/** Palettes lifted from the editor's Axon dependency-space visualizer. */
export interface SpaceTheme {
  id: ThemeId;
  label: string;
  /** page/void background — hue-shifted per theme like Axon's bgTop/bgBot */
  bg: string;
  accent: string;
  accentInk: string;
  accentDeep: string;
  /** accent-2: calls-edge / energy color */
  secondary: string;
  /** inherits-edge color */
  tertiary: string;
  beacon: string;
  star: string;
  nodePalette: string[];
  nebulae: string[];
}

export const THEMES: Record<ThemeId, SpaceTheme> = {
  violet: {
    id: "violet",
    label: "01 Void Violet",
    bg: "#08051a",
    accent: "#a78bfa",
    accentInk: "#c4b5fd",
    accentDeep: "#7c5cf0",
    secondary: "#67e8f9",
    tertiary: "#f0abfc",
    beacon: "#fbbf24",
    star: "#efedff",
    nodePalette: ["#a78bfa", "#a78bfa", "#a78bfa", "#c4b5fd", "#67e8f9", "#67e8f9", "#f0abfc", "#818cf8", "#5eead4", "#efedff"],
    nebulae: ["#a78bfa", "#67e8f9", "#f0abfc", "#818cf8", "#5eead4", "#a78bfa", "#67e8f9"],
  },
  cyan: {
    id: "cyan",
    label: "02 Abyss Cyan",
    bg: "#041019",
    accent: "#22d3ee",
    accentInk: "#a5f3fc",
    accentDeep: "#0284c7",
    secondary: "#2dd4bf",
    tertiary: "#60a5fa",
    beacon: "#f59e0b",
    star: "#e6fbff",
    nodePalette: ["#22d3ee", "#22d3ee", "#22d3ee", "#a5f3fc", "#60a5fa", "#2dd4bf", "#38bdf8", "#a5b4fc", "#2dd4bf", "#e6fbff"],
    nebulae: ["#22d3ee", "#60a5fa", "#2dd4bf", "#38bdf8", "#a5b4fc", "#22d3ee", "#60a5fa"],
  },
  ember: {
    id: "ember",
    label: "03 Ember Dusk",
    bg: "#140608",
    accent: "#fb923c",
    accentInk: "#fdba74",
    accentDeep: "#e1500f",
    secondary: "#fbbf24",
    tertiary: "#f472b6",
    beacon: "#fde047",
    star: "#fff4ea",
    nodePalette: ["#fb923c", "#fb923c", "#fb923c", "#fdba74", "#fbbf24", "#f472b6", "#e879f9", "#93c5fd", "#fbbf24", "#fff4ea"],
    nebulae: ["#fb923c", "#f472b6", "#fbbf24", "#e879f9", "#93c5fd", "#fb923c", "#f472b6"],
  },
};

interface ThemeContextValue {
  theme: SpaceTheme;
  setTheme: (id: ThemeId) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: THEMES.violet,
  setTheme: () => {},
});

function applyCssVars(theme: SpaceTheme) {
  const root = document.documentElement;
  root.style.setProperty("--color-void", theme.bg);
  root.style.setProperty("--color-accent", theme.accent);
  root.style.setProperty("--color-accent-ink", theme.accentInk);
  root.style.setProperty("--color-accent-deep", theme.accentDeep);
  root.style.setProperty("--color-accent-2", theme.secondary);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [themeId, setThemeId] = useState<ThemeId>("violet");

  useEffect(() => {
    applyCssVars(THEMES[themeId]);
  }, [themeId]);

  const setTheme = useCallback((id: ThemeId) => setThemeId(id), []);

  return (
    <ThemeContext.Provider value={{ theme: THEMES[themeId], setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme() {
  return useContext(ThemeContext);
}
