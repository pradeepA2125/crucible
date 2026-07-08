import { useEffect, useState } from "react";
import Nav from "./components/Nav";
import Hero from "./components/Hero";
import Marquee from "./components/Marquee";
import ShadowSection from "./components/ShadowSection";
import Lifecycle from "./components/Lifecycle";
import Features from "./components/Features";
import RecentlyForged from "./components/RecentlyForged";
import OpenSource from "./components/OpenSource";
import { Cta, Footer } from "./components/Cta";
import CommandPalette from "./components/CommandPalette";
import ScrollBeam from "./components/ScrollBeam";
import { ThemeProvider } from "./theme";

function Page() {
  const [paletteOpen, setPaletteOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((open) => !open);
      } else if (e.key === "Escape") {
        setPaletteOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="grain">
      <ScrollBeam />
      <Nav onOpenPalette={() => setPaletteOpen(true)} />
      <main>
        <Hero onOpenPalette={() => setPaletteOpen(true)} />
        <Marquee />
        <ShadowSection />
        <Lifecycle />
        <Features />
        <RecentlyForged />
        <OpenSource />
        <Cta />
      </main>
      <Footer />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <Page />
    </ThemeProvider>
  );
}
