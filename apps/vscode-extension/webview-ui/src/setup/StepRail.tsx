import { Fragment } from "react";
import { Icon } from "../components/Icon";

export type Step = "welcome" | "install" | "provider" | "done";

const STEP_ORDER: Step[] = ["welcome", "install", "provider", "done"];
const STEP_LABELS: Record<Step, string> = {
  welcome: "Welcome",
  install: "Install",
  provider: "Provider",
  done: "Ready",
};

/**
 * StepRail — 4 numbered dots joined by connector lines. Completed dots turn
 * green with a pop-in check, the active dot breathes with the accent glow,
 * lines fill with accent as you advance.
 */
export function StepRail({ current }: { current: Step }) {
  const idx = STEP_ORDER.indexOf(current);
  return (
    <div className="mb-6 flex items-center" aria-label={`Step ${idx + 1} of 4`}>
      {STEP_ORDER.map((s, i) => {
        const isDone = i < idx;
        const isActive = i === idx;
        return (
          <Fragment key={s}>
            {i > 0 && (
              <span
                aria-hidden="true"
                className="mx-1.5 h-[2px] flex-1 rounded"
                style={{
                  background: i <= idx ? "var(--color-accent-deep)" : "var(--color-surface-3)",
                  transition: "background var(--dur-slow) var(--ease-out)",
                }}
              />
            )}
            <span className="flex items-center gap-1.5">
              <span
                className={[
                  "flex h-[18px] w-[18px] items-center justify-center rounded-full font-semibold",
                  isDone ? "anim-pop" : "",
                ].join(" ")}
                style={{
                  fontSize: "9.5px",
                  ...(isDone
                    ? { background: "var(--green-bg)", border: "1px solid var(--green-brd)", color: "var(--color-green)" }
                    : isActive
                      ? {
                          background: "linear-gradient(180deg, var(--color-accent-deep), var(--color-accent-hot))",
                          color: "#fff",
                          animation: "breathe 2.4s ease-in-out infinite",
                        }
                      : { background: "var(--color-surface-3)", color: "var(--color-text-3)" }),
                }}
              >
                {isDone ? <Icon name="check" size={9} /> : i + 1}
              </span>
              <span className="text-[10.5px]" style={{ color: isActive ? "var(--color-text)" : "var(--color-text-3)" }}>
                {STEP_LABELS[s]}
              </span>
            </span>
          </Fragment>
        );
      })}
    </div>
  );
}
