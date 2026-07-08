import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { GITHUB_URL, INSTALL_CMD } from "../content";
import { THEMES, useTheme, type ThemeId } from "../theme";

interface Command {
  id: string;
  group: string;
  label: string;
  hint?: string;
  run: () => void;
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

export default function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const { setTheme } = useTheme();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const commands = useMemo<Command[]>(() => {
    const jump = (hash: string) => () => {
      onClose();
      document.querySelector(hash)?.scrollIntoView({ behavior: "smooth" });
    };
    const navigate: Command[] = [
      { id: "top", group: "fly to", label: "The hero space", hint: "↟", run: jump("#top") },
      { id: "shadow", group: "fly to", label: "The shadow workspace", run: jump("#shadow") },
      { id: "lifecycle", group: "fly to", label: "The reactive loop", run: jump("#lifecycle") },
      { id: "features", group: "fly to", label: "Features", run: jump("#features") },
      { id: "fresh", group: "fly to", label: "Recently cast", run: jump("#fresh") },
      { id: "opensource", group: "fly to", label: "Open source", run: jump("#opensource") },
      { id: "install", group: "fly to", label: "Install", run: jump("#install") },
    ];
    const themes: Command[] = (Object.keys(THEMES) as ThemeId[]).map((id) => ({
      id: `theme-${id}`,
      group: "theme",
      label: `Repaint the space — ${THEMES[id].label}`,
      run: () => {
        setTheme(id);
        onClose();
      },
    }));
    const links: Command[] = [
      {
        id: "github",
        group: "links",
        label: "Open the GitHub repo",
        hint: "↗",
        run: () => {
          window.open(GITHUB_URL, "_blank", "noreferrer");
          onClose();
        },
      },
      {
        id: "copy-install",
        group: "links",
        label: "Copy the install command",
        hint: "⧉",
        run: () => {
          void navigator.clipboard.writeText(INSTALL_CMD).catch(() => {});
          onClose();
        },
      },
    ];
    return [...navigate, ...themes, ...links];
  }, [onClose, setTheme]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter(
      (c) => c.label.toLowerCase().includes(q) || c.group.includes(q),
    );
  }, [commands, query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setSelected(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => {
    setSelected(0);
  }, [query]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelected((s) => Math.min(s + 1, filtered.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelected((s) => Math.max(s - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        filtered[selected]?.run();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, filtered, selected]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          className="fixed inset-0 z-[80] scrim flex items-start justify-center pt-[18vh] px-5"
          onClick={onClose}
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.96, y: -12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: -8 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="surface-card w-full max-w-lg overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 border-b hairline px-5 py-3.5">
              <svg viewBox="0 0 16 16" className="w-4 h-4 text-accent shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
                <circle cx="7" cy="7" r="4.4" />
                <path d="m10.5 10.5 3 3" strokeLinecap="round" />
              </svg>
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="fly to anything…"
                className="flex-1 bg-transparent outline-none font-mono text-sm text-ink placeholder:text-ink-4"
              />
              <kbd className="kbd">esc</kbd>
            </div>
            <div className="max-h-[46vh] overflow-y-auto py-2">
              {filtered.length === 0 && (
                <p className="px-5 py-6 font-mono text-xs text-ink-4">
                  nothing in this sector.
                </p>
              )}
              {filtered.map((command, i) => (
                <button
                  key={command.id}
                  onClick={command.run}
                  onMouseEnter={() => setSelected(i)}
                  className={`w-full flex items-center gap-3 px-5 py-2.5 text-left transition-colors cursor-pointer ${
                    i === selected ? "bg-accent/10" : ""
                  }`}
                >
                  <span className="font-mono text-[9px] tracking-[0.22em] uppercase text-ink-4 w-14 shrink-0">
                    {command.group}
                  </span>
                  <span className={`text-[13.5px] ${i === selected ? "text-ink" : "text-ink-2"}`}>
                    {command.label}
                  </span>
                  <span className="ml-auto font-mono text-xs text-ink-4">
                    {command.hint ?? "↵"}
                  </span>
                </button>
              ))}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
