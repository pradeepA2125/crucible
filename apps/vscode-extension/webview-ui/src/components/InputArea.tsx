import { useRef, useEffect, useState } from "react";
import { Icon } from "./Icon";
import { ModelMenu } from "./ModelMenu";
import { EffortMenu } from "./EffortMenu";
import { UsageChip } from "./UsageChip";
import { ContextMeter } from "./ContextMeter";
import { vscode } from "../vscodeApi";
import { parseSlashCommand, resolveSkillCommand, buildSlashDropdownItems } from "../slash";
import { detectTrigger } from "../composerTrigger";
import { TriggerDropdown } from "./TriggerDropdown";
import type { InputAvailability } from "../inputAvailability";
import type { ContextUsage, ThreadUsageView } from "../types";

type ImageAttachment = { name: string; mediaType: string; dataUrl: string; bytes: number };
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/tiff"]);

interface Props {
  availability: InputAvailability;
  draft: string;
  threadId?: string;
  onDraftChange: (text: string) => void;
  // Opens the in-chat settings popup. When absent (standalone/test call sites),
  // the gear falls back to the legacy openSettings host message.
  onOpenSettings?: () => void;
  planMode?: boolean;  // hydrated from the extension's globalState via planModeState
  stepReview?: boolean;  // hydrated from globalState via reviewPrefState
  threadUsage?: ThreadUsageView | null;  // the thread's running token total
  teamNames?: Record<string, string>;
  contextUsage?: ContextUsage | null;
  contextUsageStale?: boolean;
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
export function InputArea({ availability, draft, threadId = "", onDraftChange, onOpenSettings, planMode = false, stepReview = true, threadUsage, teamNames, contextUsage = null, contextUsageStale = false }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const [imageAttachments, setImageAttachments] = useState<ImageAttachment[]>([]);
  const [pendingImages, setPendingImages] = useState(0);
  const [imageError, setImageError] = useState("");
  const readers = useRef(new Set<FileReader>());
  const alive = useRef(true);
  const recoveryId = useRef<string | null>(null);
  const scope = useRef(threadId);
  const readerGeneration = useRef(0);
  useEffect(() => {
    if (scope.current === threadId) return;
    const initialSendAdoption = !scope.current && !!threadId && !!recoveryId.current;
    scope.current = threadId;
    if (initialSendAdoption) return;
    ++readerGeneration.current;
    for (const reader of readers.current) reader.abort();
    readers.current.clear();
    setPendingImages(0);
    setImageAttachments([]);
    setImageError("");
    recoveryId.current = null;
    pendingSlashRef.current = null;
    onDraftChange("");
  }, [threadId, onDraftChange]);
  const currentDraft = useRef(draft);
  currentDraft.current = draft;
  useEffect(() => { alive.current = true; return () => { alive.current = false; for (const reader of readers.current) reader.abort(); }; }, []);
  useEffect(() => {
    if (draft) recoveryId.current = null;
  }, [draft]);
  useEffect(() => {
    const recover = (event: MessageEvent) => {
      const m = event.data;
      if (m?.type === "composerPrefill" && m.threadId === scope.current) {
        ++readerGeneration.current;
        for (const reader of readers.current) reader.abort();
        readers.current.clear();
        setPendingImages(0);
        recoveryId.current = null;
        pendingSlashRef.current = null;
        setImageAttachments(m.imageAttachments ?? []);
        trackedMentionsRef.current = [];
        return;
      }
      if (m?.type !== "draftRecovery" || m.draftId !== recoveryId.current || currentDraft.current || readers.current.size) return;
      recoveryId.current = null;
      setImageAttachments(m.imageAttachments ?? []);
      trackedMentionsRef.current = m.mentionedPaths ?? [];
      onDraftChange(m.text ?? "");
    };
    window.addEventListener("message", recover);
    return () => window.removeEventListener("message", recover);
  }, [onDraftChange]);

  function sendDraft(text: string, forcedSkills?: string[]) {
    if (availability.disabled || readers.current.size) return;
    const draftId = `${Date.now()}-${Math.random()}`;
    recoveryId.current = draftId;
    vscode.postMessage({ type: "sendMessage", text, originalDraftText: currentDraft.current, stepReview, planMode, draftId,
      ...(forcedSkills ? { forcedSkills } : {}),
      ...(trackedMentionsRef.current.length ? { mentionedPaths: trackedMentionsRef.current } : {}),
      ...(imageAttachments.length ? { imageAttachments } : {}),
    });
    trackedMentionsRef.current = [];
    setImageAttachments([]);
    onDraftChange("");
  }
  const [stopping, setStopping] = useState(false);
  // One-shot guard for the Tier B task-abort buttons (keep / revert).
  const [aborting, setAborting] = useState(false);
  // "Review each step" — a CONTROLLED prop sourced from the extension's globalState,
  // not local state. As local useState(true) it silently returned to checked on every
  // remount (thread switch, panel reload), so an unchecked box came back on and the
  // next message gated its edits again. Mirrors planMode exactly.
  //
  // It governs BOTH paths: the controller applies it per message to its edit gate
  // (controller.py `is_review = step_review is True` -> auto_accept_edits), and the
  // task path uses it as the creation default. The older comment here claimed it was
  // task-only; that has not been true since the controller gained per-edit review.

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

  // Reset abort guard when the task leaves an abortable phase.
  useEffect(() => {
    if (!availability.taskStop) {
      setAborting(false);
    }
  }, [availability.taskStop]);

  // The text we sent for expansion, held across the async host round-trip so a
  // "no such prompt" reply can fall back to sending it as a normal message.
  const pendingSlashRef = useRef<{ text: string; requestId: string } | null>(null);
  // Known skill names for deterministic /skill forced-load (the catalog from the host).
  // A prompt file of the same name wins (the host resolves prompts first).
  const [skillNames, setSkillNames] = useState<string[]>([]);
  // Fetch the catalog lazily on the first "/" keystroke (not on mount) so a composer
  // that never types a slash command makes no extra round-trip.
  const skillsRequestedRef = useRef(false);

  // Unified "/" + "@" dropdown state.
  const [trigger, setTrigger] = useState<{ kind: "slash" | "file"; query: string; start: number; end: number } | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [promptNames, setPromptNames] = useState<string[]>([]);
  const [skillCatalog, setSkillCatalog] = useState<{ name: string; description: string }[]>([]);
  const promptsRequestedRef = useRef(false);
  const [workspaceFiles, setWorkspaceFiles] = useState<string[]>([]);
  const filesRequestedRef = useRef(false);
  // Ordered set of paths inserted via the @ dropdown in the CURRENT draft — only
  // these resolve to a real mention on send (not any stray "@text" the user typed).
  const trackedMentionsRef = useRef<string[]>([]);

  useEffect(() => {
    function onMessage(e: MessageEvent) {
      const m = e.data as Record<string, unknown>;
      if (m?.["type"] === "skillList") {
        const skills = (m["skills"] as { name: string; description: string }[] | undefined) ?? [];
        setSkillNames(skills.map((s) => s.name));
        setSkillCatalog(skills);
      } else if (m?.["type"] === "promptList") {
        setPromptNames((m["names"] as string[] | undefined) ?? []);
      } else if (m?.["type"] === "workspaceFileList") {
        setWorkspaceFiles((m["paths"] as string[] | undefined) ?? []);
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  function maybeRequestCatalogs(value: string) {
    if (value.startsWith("/")) {
      if (!skillsRequestedRef.current) {
        skillsRequestedRef.current = true;
        vscode.postMessage({ type: "listSkills" });
      }
      if (!promptsRequestedRef.current) {
        promptsRequestedRef.current = true;
        vscode.postMessage({ type: "listPrompts" });
      }
    }
    if (value.includes("@") && !filesRequestedRef.current) {
      filesRequestedRef.current = true;
      vscode.postMessage({ type: "listWorkspaceFiles" });
    }
  }

  // Prompt-file expansion: the host replies to an expandPrompt request with the
  // substituted body (found=true → fill the draft for review). When there is no
  // matching prompt (found=false), the typed text was NOT a command — send it as
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      const m = e.data as Record<string, unknown>;
      if (m?.["type"] !== "promptExpanded" || !pendingSlashRef.current || m["requestId"] !== pendingSlashRef.current.requestId || currentDraft.current.trim() !== pendingSlashRef.current.text) return;
      if (availability.disabled || readers.current.size) return;
      const original = pendingSlashRef.current.text;
      pendingSlashRef.current = null;
      if (m["found"] === true) {
        onDraftChange(m["text"] as string);
        return;
      }
      // No prompt file matched — was it a /skill? Deterministic forced-load: send the
      // args as the message tagged with the skill. Otherwise send the typed text as-is.
      const slash = parseSlashCommand(original);
      const skill = slash ? resolveSkillCommand(slash.name, slash.args, skillNames) : null;
      if (skill) {
        sendDraft(skill.message, skill.forcedSkills);
      } else {
        sendDraft(original);
      }
      onDraftChange("");
      const el = textareaRef.current;
      if (el) el.style.height = "auto";
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [onDraftChange, stepReview, skillNames, planMode, imageAttachments, availability.disabled]);

  function addImageFiles(files: FileList | File[]) {
    recoveryId.current = null;
    pendingSlashRef.current = null;
    const batch = Array.from(files);
    if (readers.current.size || batch.length + imageAttachments.length > 8 ||
        batch.reduce((n, f) => n + f.size, 0) + imageAttachments.reduce((n, f) => n + f.bytes, 0) > 24 * 1024 * 1024 ||
        batch.some(f => !IMAGE_TYPES.has(f.type) || !f.size || f.size > MAX_IMAGE_BYTES)) {
      setImageError("Use up to 8 supported images, at most 8 MiB each and 24 MiB total. Wait for pending images.");
      return;
    }
    setImageError("");
    for (const file of batch) {
      const generation = readerGeneration.current;
      const reader = new FileReader();
      readers.current.add(reader);
      setPendingImages(readers.current.size);
      reader.onload = () => {
        if (!alive.current || generation !== readerGeneration.current || typeof reader.result !== "string") return;
        const dataUrl = reader.result;
        setImageAttachments(current => current.some(a => a.dataUrl === dataUrl) ? current :
          [...current, { name: file.name || "pasted-image", mediaType: file.type, dataUrl, bytes: file.size }]);
      };
      reader.onerror = () => { if (alive.current) setImageError(`Could not read ${file.name}. Please attach it again.`); };
      reader.onloadend = () => { readers.current.delete(reader); if (alive.current) setPendingImages(readers.current.size); };
      reader.readAsDataURL(file);
    }
  }

  function handlePaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(e.clipboardData.files).filter((file) => file.type.startsWith("image/"));
    if (files.length) {
      e.preventDefault();
      addImageFiles(files);
    }
  }

  function autoGrow() {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`;
  }

  function handleInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const value = e.target.value;
    recoveryId.current = null;
    pendingSlashRef.current = null;
    onDraftChange(value);
    maybeRequestCatalogs(value);
    const cursor = e.target.selectionStart ?? value.length;
    const t = detectTrigger(value, cursor);
    setTrigger(t);
    setActiveIndex(0);
    autoGrow();
  }

  const dropdownItems =
    trigger?.kind === "slash"
      ? buildSlashDropdownItems(trigger.query, promptNames, skillCatalog).map((i) => ({
          id: i.id, label: i.label, sublabel: i.sublabel, badge: i.badge,
        }))
      : trigger?.kind === "file"
      ? workspaceFiles
          .filter((p) => p.toLowerCase().includes(trigger.query.toLowerCase()))
          .slice(0, 20)
          .map((p) => ({ id: p, label: p }))
      : [];

  function applyTriggerSelection(id: string) {
    if (!trigger) return;
    const el = textareaRef.current;
    const before = draft.slice(0, trigger.start);
    const after = draft.slice(trigger.end);
    const insertion = trigger.kind === "slash" ? `/${id} ` : `@${id} `;
    const next = `${before}${insertion}${after}`;
    if (trigger.kind === "file" && !trackedMentionsRef.current.includes(id)) {
      trackedMentionsRef.current = [...trackedMentionsRef.current, id];
    }
    onDraftChange(next);
    setTrigger(null);
    requestAnimationFrame(() => {
      if (!el) return;
      const pos = before.length + insertion.length;
      el.focus();
      el.setSelectionRange(pos, pos);
    });
  }

  function handleTriggerKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>): boolean {
    if (!trigger || dropdownItems.length === 0) return false;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % dropdownItems.length);
      return true;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + dropdownItems.length) % dropdownItems.length);
      return true;
    }
    if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      applyTriggerSelection(dropdownItems[activeIndex].id);
      return true;
    }
    if (e.key === "Escape") {
      setTrigger(null);
      return true;
    }
    return false;
  }

  function doSend() {
    if (availability.disabled || readers.current.size) return;
    const trimmed = draft.trim();
    if (!trimmed && imageAttachments.length === 0) return;
    const slash = parseSlashCommand(trimmed);
    if (slash) {
      // Expand first; the host replies with promptExpanded. found=true fills the
      // draft (user reviews, then sends again → real send); found=false falls back
      // to sending `original` as a normal message (see the listener above).
      const requestId = `${Date.now()}-${Math.random()}`;
      pendingSlashRef.current = { text: trimmed, requestId };
      vscode.postMessage({ type: "expandPrompt", name: slash.name, args: slash.args, requestId });
      return;
    }
    trackedMentionsRef.current = trackedMentionsRef.current.filter(p => draft.includes(`@${p}`));
    sendDraft(trimmed);
    trackedMentionsRef.current = [];
    setImageAttachments([]);
    onDraftChange("");
    // Reset height after clearing.
    const el = textareaRef.current;
    if (el) el.style.height = "auto";
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (handleTriggerKeyDown(e)) return;
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

  function handleAbort(revert: boolean) {
    if (aborting) return; // one-shot guard
    setAborting(true);
    vscode.postMessage({ type: "abortTask", revert });
  }

  const canSend = !availability.disabled && pendingImages === 0 && (draft.trim().length > 0 || imageAttachments.length > 0);

  return (
    <div
      className={[
        "relative rounded-[10px] border px-3 pt-2 pb-1.5",
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
      {trigger && dropdownItems.length > 0 && (
        <TriggerDropdown
          items={dropdownItems}
          activeIndex={activeIndex}
          onHover={setActiveIndex}
          onSelect={applyTriggerSelection}
        />
      )}
      {(imageAttachments.length > 0 || pendingImages > 0 || imageError) && (
        <div data-testid="composer-images" className="flex flex-wrap gap-2 pb-2 pt-1">
          <span className="sr-only">
            {imageAttachments.length} image{imageAttachments.length === 1 ? "" : "s"}
          </span>
          {imageAttachments.map((image, i) => (
            <div key={`${image.name}-${i}`} title={image.name}
              className="group relative h-14 w-14 shrink-0 overflow-hidden rounded-[8px]"
              style={{ border: "1px solid var(--color-border-strong)", background: "var(--color-surface-2)" }}>
              <img src={image.dataUrl} alt={image.name} className="h-full w-full object-cover" />
              <button type="button" aria-label={`Remove ${image.name}`}
                onClick={() => { recoveryId.current = null; pendingSlashRef.current = null; setImageAttachments(current => current.filter((_, index) => index !== i)); }}
                className="absolute right-0.5 top-0.5 flex h-[18px] w-[18px] items-center justify-center rounded-full text-text opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
                style={{ background: "rgba(10,10,14,.72)", border: "1px solid var(--color-border-strong)" }}>
                <Icon name="x" size={9} />
              </button>
            </div>
          ))}
          {Array.from({ length: pendingImages }, (_, i) => (
            <div key={`pending-${i}`} aria-hidden="true" className="h-14 w-14 shrink-0 animate-pulse rounded-[8px]"
              style={{ border: "1px dashed var(--color-border-strong)", background: "var(--color-surface-2)" }} />
          ))}
          {pendingImages > 0 && <span role="status" className="sr-only">Reading images…</span>}
          {imageError && (
            <span role="alert" className="basis-full text-[10.5px]" style={{ color: "var(--color-red)" }}>{imageError}</span>
          )}
        </div>
      )}
      {/* Textarea */}
      <textarea
        ref={textareaRef}
        rows={1}
        value={draft}
        onChange={handleInput}
        onPaste={handlePaste}
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
      <div data-testid="composer-footer" className="relative flex flex-wrap items-center gap-1.5 pt-1 [&>*]:shrink-0">
        <input
          ref={imageInputRef}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp,image/bmp,image/tiff"
          multiple
          className="hidden"
          aria-label="Attach images"
          onChange={(e) => {
            if (e.target.files) addImageFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          aria-label="Attach images"
          title="Attach images"
          onClick={() => imageInputRef.current?.click()}
          className="flex h-6 w-6 items-center justify-center rounded-[7px] text-text-3 hover:bg-surface-2 hover:text-text"
        >
          <Icon name="plus" size={12} />
        </button>
        {/* Reasoning-effort chip + model hot-swap chip + settings shortcut. */}
        <EffortMenu />
        <ModelMenu />
        <button
          type="button"
          onClick={() =>
            onOpenSettings ? onOpenSettings() : vscode.postMessage({ type: "openSettings" })
          }
          aria-label="Open settings"
          title="Crucible settings"
          className="flex h-6 w-6 items-center justify-center rounded-[7px] cursor-pointer text-text-3 transition-colors duration-150 hover:bg-surface-2 hover:text-text"
        >
          <Icon name="gear" size={12} />
        </button>
        <UsageChip usage={threadUsage ?? null} teamNames={teamNames ?? {}} />
        <ContextMeter usage={contextUsage} stale={contextUsageStale} />

        {/* Tier B: task-abort buttons — shown while a task is in an abortable phase.
            "Stop & keep" leaves applied changes; "Stop & revert" rolls the workspace back. */}
        {availability.taskStop && (
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => handleAbort(false)}
              disabled={aborting}
              aria-label="Stop and keep changes"
              title="Stop the task; keep the changes applied so far"
              className="flex items-center gap-1 h-6 px-2 rounded-[6px] border text-[10px] disabled:opacity-50 disabled:cursor-default"
              style={{
                background: "var(--color-surface-2)",
                borderColor: "var(--color-border-strong)",
                color: "var(--color-text-2)",
              }}
            >
              <Icon name="stop" size={9} />
              Stop &amp; keep
            </button>
            <button
              type="button"
              onClick={() => handleAbort(true)}
              disabled={aborting}
              aria-label="Stop and revert changes"
              title="Stop the task and roll the workspace back to its pre-task state"
              className="flex items-center gap-1 h-6 px-2 rounded-[6px] border text-[10px] disabled:opacity-50 disabled:cursor-default"
              style={{
                background: "var(--color-surface-2)",
                borderColor: "var(--red-brd)",
                color: "var(--color-red)",
              }}
            >
              <Icon name="stop" size={9} />
              Stop &amp; revert
            </button>
          </div>
        )}

        {/* Spacer */}
        <span className="flex-1" />

        {/* Review-each-step toggle */}
        <label className="flex items-center gap-1.5 text-[10px] text-text-3 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={stepReview}
            onChange={(e) => {
              const checked = e.target.checked;
              // No local setState: the host persists and echoes reviewPrefState back,
              // which updates this controlled prop. The round-trip is required, not
              // decorative — same contract as setPlanMode.
              // Live-mutable in BOTH surfaces: a running task re-reads it before each step
              // gate, and an in-flight chat turn re-reads it before each edit (flipping it
              // on also accepts the edit gate currently on screen). Checked = "review each
              // step" = auto_accept false. A 409 (nothing running) is benign on the
              // extension side — the value still governs the next task/message.
              vscode.postMessage({ type: "setReviewPref", autoAccept: !checked });
            }}
            className="accent-[var(--color-accent)] w-3 h-3"
          />
          Review each step
        </label>

        {/* Plan Mode toggle — sticky across threads (extension globalState), unlike
            "Review each step" which is per-message local state. */}
        <label className="flex items-center gap-1.5 text-[10px] text-text-3 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={planMode}
            aria-label="Plan Mode"
            onChange={(e) => {
              vscode.postMessage({ type: "setPlanMode", enabled: e.target.checked });
            }}
            className="accent-[var(--color-accent)] w-3 h-3"
          />
          Plan Mode
        </label>

        {/* ⌘↵ hint */}
        <span
          className="font-mono text-text-4 select-none"
          style={{ fontSize: "9.5px" }}
        >
          ⌘↵
        </span>

        {/* Right-hand action: Stop while a chat turn streams, otherwise Send. */}
        {availability.showStop ? (
          <button
            type="button"
            onClick={handleStop}
            disabled={stopping}
            aria-label="Stop"
            title="Stop"
            className={[
              "flex items-center justify-center w-6 h-6 rounded-[7px]",
              "transition-all duration-150",
              "disabled:opacity-50 disabled:cursor-default",
            ].join(" ")}
            style={{
              background: "var(--color-surface-2)",
              border: "1px solid var(--red-brd)",
              color: stopping ? "var(--color-text-4)" : "var(--color-red)",
            }}
          >
            <Icon name="stop" size={11} />
          </button>
        ) : (
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
        )}
      </div>
    </div>
  );
}