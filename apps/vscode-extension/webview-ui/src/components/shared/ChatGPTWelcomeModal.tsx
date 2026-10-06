import { createPortal } from "react-dom";
import { BtnGhost, BtnPrimary } from "./buttons";
import { ChatGPTLogo } from "./ChatGPTBrand";

/**
 * Shown once, after the first sign-in with ChatGPT plan usage enabled (UI/UX
 * guidelines: "Confirm ChatGPT plan use after the first sign-in" — never on later
 * sign-ins; the host tracks which accounts have seen it).
 */
export function ChatGPTWelcomeModal({ onDismiss, onManageUsage }: {
  onDismiss: () => void; onManageUsage: () => void;
}) {
  // Portaled to <body>: inside an animated (transformed) section, `fixed` positions
  // against that section instead of the viewport (found live: off-center, nav undimmed).
  return createPortal(
    <div className="scrim fixed inset-0 z-50 flex items-center justify-center p-4"
         role="dialog" aria-modal="true" aria-labelledby="chatgpt-welcome-title">
      <div className="surface-card anim-pop flex w-full max-w-[360px] flex-col gap-3 p-5">
        <ChatGPTLogo size={24} color="var(--color-text)" />
        <h2 id="chatgpt-welcome-title" className="text-sm font-semibold text-text">
          You're using your ChatGPT plan
        </h2>
        <p className="text-xs leading-relaxed text-text-2">
          Eligible usage in this app uses your ChatGPT plan. Manage usage in your ChatGPT
          settings.
        </p>
        <div className="flex items-center justify-end gap-2">
          <BtnGhost onClick={onManageUsage}>Manage usage</BtnGhost>
          <BtnPrimary onClick={onDismiss}>Got it</BtnPrimary>
        </div>
      </div>
    </div>,
    document.body,
  );
}
