import type { Accent, Appearance } from "../../theme";
import type { SettingsInMsg } from "../types";
import { SectionHeader } from "../SectionHeader";

const APPEARANCE_OPTIONS: { value: Appearance; label: string; hint: string }[] = [
  { value: "vscode", label: "Match VS Code", hint: "Uses your editor’s colour theme." },
  { value: "dark", label: "Crucible Dark", hint: "Neutral greys, any editor theme." },
  { value: "light", label: "Crucible Light", hint: "White and light greys." },
];

const ACCENT_OPTIONS: { value: Accent; label: string }[] = [
  { value: "blue", label: "Blue" },
  { value: "violet", label: "Violet" },
  { value: "ember", label: "Ember" },
];

interface Props {
  appearance: Appearance;
  accent: Accent;
  busy: boolean;
  send: (msg: SettingsInMsg) => void;
}

/** Spec 2026-10-13 §1. Changes write the VS Code settings through the host, which
 * broadcasts themeChanged, so every panel (this one included) re-themes live. */
export function AppearanceSection({ appearance, accent, busy, send }: Props) {
  const choose = (next: { appearance?: Appearance; accent?: Accent }) =>
    send({ type: "settings/setAppearance", appearance: next.appearance ?? appearance, accent: next.accent ?? accent });
  // Swatches preview each accent in the current tone by scoping data-tone/data-accent
  // on the swatch itself (styles/themes.css accent blocks aren't :root-only).
  const tone = document.documentElement.dataset.tone === "light" ? "light" : "dark";

  const desktop = document.documentElement.dataset.host === "desktop";
  return (
    <div className="grid gap-5">
      <SectionHeader title="Appearance" description={desktop ? "Colours for every Crucible window." : "Colours for Crucible’s panels. High-contrast VS Code themes always win."} />
      <fieldset className="grid gap-2" disabled={busy}>
        <legend className="mb-1 text-text-2" style={{ fontSize: "var(--fs-meta)" }}>Theme</legend>
        {APPEARANCE_OPTIONS.map((o) => (
          <label key={o.value} className="flex cursor-pointer items-start gap-2">
            <input type="radio" name="appearance" value={o.value} checked={appearance === o.value}
              onChange={() => choose({ appearance: o.value })} />
            <span>
              <span className="block text-text">{desktop && o.value === "vscode" ? "Follow system" : o.label}</span>
              <span className="block text-text-3" style={{ fontSize: "var(--fs-meta)" }}>{desktop && o.value === "vscode" ? "Uses your system’s light or dark appearance." : o.hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <fieldset className="grid gap-2" disabled={busy}>
        <legend className="mb-1 text-text-2" style={{ fontSize: "var(--fs-meta)" }}>Accent</legend>
        <div className="flex flex-wrap gap-4">
          {ACCENT_OPTIONS.map((o) => (
            <label key={o.value} className="flex cursor-pointer items-center gap-2">
              <input type="radio" name="accent" value={o.value} checked={accent === o.value}
                onChange={() => choose({ accent: o.value })} />
              <span aria-hidden="true" data-tone={tone} data-accent={o.value}
                style={{ width: 14, height: 14, borderRadius: "var(--r-round)", background: "var(--accent-fill)" }} />
              <span className="text-text">{o.label}</span>
            </label>
          ))}
        </div>
      </fieldset>
    </div>
  );
}
