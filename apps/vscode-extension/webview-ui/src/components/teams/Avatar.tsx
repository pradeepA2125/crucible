import { identityFor } from "../../teamIdentity";

const SIZES = { sm: 16, md: 22, lg: 34 } as const;

/** A member's avatar (spec 2026-10-05 §6): initial on its hue; a pulsing ring while working,
 * a grey ring and desaturated fill while idle. */
export function Avatar({ label, roster, size = "md", ring }: {
  label: string; roster: string[]; size?: keyof typeof SIZES; ring?: "working" | "idle";
}) {
  const id = identityFor(label, roster);
  const px = SIZES[size];
  return (
    // The initial is drawn by CSS (data-initial), so it never enters the text around it —
    // "woke review · impl" reads (and tests) as words, not "woke Rreview · Iimpl".
    <span aria-hidden="true" data-initial={id.initial}
      className="relative inline-grid flex-shrink-0 place-items-center rounded-full font-semibold before:content-[attr(data-initial)]"
      style={{ width: px, height: px, fontSize: Math.round(px * 0.45), color: "var(--color-panel)",
               background: id.color, filter: ring === "idle" ? "saturate(.25) brightness(.8)" : undefined }}>
      {ring && (
        <span className={ring === "working" ? "team-ring-pulse" : undefined}
          style={{ position: "absolute", inset: -3, borderRadius: "50%",
                   border: `1.5px solid ${ring === "working" ? id.color : "var(--color-text-4)"}` }} />
      )}
    </span>
  );
}
