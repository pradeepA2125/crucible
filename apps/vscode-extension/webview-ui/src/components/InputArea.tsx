import { useRef, useEffect, useState } from "react";
import { Icon } from "./Icon";
import { vscode } from "../vscodeApi";
import type { InputAvailability } from "../inputAvailability";

interface Props {
  availability: InputAvailability;
  draft: string;
  onDraftChange: (text: string) => void;
}

// 5 lines × ~19.2px line-height ≈ 96px. Caps the textarea's auto-grow.
const MAX_TEXTAREA_HEIGHT = 96;

/**
 * InputArea — the chat input bar.
 *
 * Auto-grows with content up to ~5 lines. Enter sends; Shift+Enter inserts a
 * newline. When availability.showStop is true, a Stop button appears on the
 * left side of the footer row and posts { type: "stopTurn" } once.
 */
export function InputArea({ availability, draft, onDraftChange }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [stopping, setStopping] = useState(false);

  // Focus on mount and whenever disabled flips to false.
  useEffect(() => {
    if (!availability.disabled) {
      textareaRef.current?.focus();
    }
  }, [availability.disabled]);

  // Reset stopping state when showStop flips back to false (turn ended).
  useEffect(() => {
    if (!availability.showStop) {
      setStopping(false);
    }
  }, [availability.showStop]);

  function autoGrow() {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`;
  }

  function handleInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    onDraftChange(e.target.value);
    autoGrow();
  }

  function doSend() {
    if (availability.disabled) return;
    const trimmed = draft.trim();
    if (!trimmed) return;
    vscode.postMessage({ type: "sendMessage", text: trimmed });
    onDraftChange("");
    // Reset height after clearing.
    const el = textareaRef.current;
    if (el) el.style.height = "auto";
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      doSend();
    }
    // Shift+Enter falls through to default (newline insertion).
  }

  function handleStop() {
    if (stopping) return; // one-shot guard
    setStopping(true);
    vscode.postMessage({ type: "stopTurn" });
  }

  const canSend = !availability.disabled && draft.trim().length > 0;

  return (
    <div
      className={[
        "rounded-[10px] border px-3 pt-2 pb-1.5",
        "transition-opacity duration-150",
        availability.disabled ? "opacity-55" : "opacity-100",
      ].join(" ")}
      style={{
        background: "var(--color-surface)",
        borderColor: "var(--color-border-strong)",
      }}
      // Focus-within ring is applied via inline style on a wrapper trick using
      // onFocusCapture/onBlurCapture to avoid Tailwind v4 focus-within issues.
    >
      {/* Textarea */}
      <textarea
        ref={textareaRef}
        rows={1}
        value={draft}
        onChange={handleInput}
        onKeyDown={handleKeyDown}
        disabled={availability.disabled}
        placeholder={availability.placeholder}
        aria-label="Chat input"
        className={[
          "w-full bg-transparent outline-none resize-none",
          "text-xs leading-relaxed text-text",
          "placeholder:text-text-4",
          "disabled:cursor-not-allowed",
        ].join(" ")}
        style={{
          fontFamily: "inherit",
          minHeight: "1.5em",
          maxHeight: MAX_TEXTAREA_HEIGHT,
          overflowY: "auto",
        }}
      />

      {/* Footer row */}
      <div className="flex items-center gap-1.5 pt-1">
        {/* Stop button — only shown when a streaming chat turn is active */}
        {availability.showStop && (
          <button
            type="button"
            onClick={handleStop}
            disabled={stopping}
            aria-label="Stop"
            title="Stop"
            className={[
              "flex items-center justify-center w-6 h-6 rounded-[6px]",
              "border transition-colors duration-150",
              "disabled:opacity-50 disabled:cursor-default",
            ].join(" ")}
            style={{
              background: "var(--color-surface-2)",
              borderColor: "var(--color-border-strong)",
              color: stopping ? "var(--color-text-4)" : "var(--color-text-2)",
            }}
            onMouseEnter={(e) => {
              if (!stopping) {
                (e.currentTarget as HTMLButtonElement).style.color = "var(--color-red)";
                (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--red-brd)";
              }
            }}
            onMouseLeave={(e) => {
              if (!stopping) {
                (e.currentTarget as HTMLButtonElement).style.color = "var(--color-text-2)";
                (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--color-border-strong)";
              }
            }}
          >
            <Icon name="stop" size={10} />
          </button>
        )}

        {/* Spacer */}
        <span className="flex-1" />

        {/* ⌘↵ hint */}
        <span
          className="font-mono text-text-4 select-none"
          style={{ fontSize: "9.5px" }}
        >
          ⌘↵
        </span>

        {/* Send button */}
        <button
          type="button"
          onClick={doSend}
          disabled={!canSend}
          aria-label="Send"
          className={[
            "flex items-center justify-center w-6 h-6 rounded-[7px]",
            "transition-all duration-150",
            "disabled:opacity-40 disabled:cursor-default",
          ].join(" ")}
          style={
            canSend
              ? {
                  background:
                    "linear-gradient(180deg, var(--color-accent-deep), var(--color-accent-hot))",
                  boxShadow:
                    "0 1px 4px rgba(0,0,0,.4), 0 0 12px var(--accent-glow)",
                  color: "#fff",
                }
              : {
                  background: "var(--color-surface-2)",
                  borderColor: "var(--color-border)",
                  color: "var(--color-text-4)",
                  border: "1px solid var(--color-border)",
                }
          }
        >
          <Icon name="send" size={12} />
        </button>
      </div>
    </div>
  );
}
