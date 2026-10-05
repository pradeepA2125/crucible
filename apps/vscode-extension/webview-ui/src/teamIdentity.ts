// Member hues, assigned by roster order (spec 2026-10-05 §6). Kept apart from the
// semantic green/red/amber so a member's colour never reads as a status.
export const MEMBER_PALETTE = ["#7dd3fc", "#f0abfc", "#5eead4", "#fdba74", "#bef264", "#fda4af"];
export const MAIN_COLOR = "#a78bfa";
export const SYSTEM_COLOR = "#62626e";

export interface MemberIdentity {
  initial: string;
  color: string;
  kind: "main" | "system" | "member";
}

/** A stable avatar for a label: one letter, or two when members share an initial — the
 * first such member (roster order) takes its second letter, each later one the first letter
 * after its initial that no earlier one used (review / reader → Re / Ra). */
export function identityFor(label: string, roster: string[]): MemberIdentity {
  if (label === "main") return { initial: "✦", color: MAIN_COLOR, kind: "main" };
  if (label === "system" || label === "team") {
    return { initial: "·", color: SYSTEM_COLOR, kind: "system" };
  }
  const index = roster.indexOf(label);
  const first = label.charAt(0).toUpperCase();
  const sameInitial = roster.filter((other) => other.charAt(0).toUpperCase() === first);
  let initial = first;
  if (index >= 0 && sameInitial.length > 1) {
    const taken = new Set<string>();
    for (const other of sameInitial) {
      const pick = [...other.slice(1)].find((c) => !taken.has(c.toLowerCase())) ?? "";
      taken.add(pick.toLowerCase());
      if (other === label) {
        initial = first + pick.toLowerCase();
        break;
      }
    }
  }
  return {
    initial,
    color: index >= 0 ? MEMBER_PALETTE[index % MEMBER_PALETTE.length] : SYSTEM_COLOR,
    kind: "member",
  };
}
