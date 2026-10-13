import { useRef, useEffect, useState, useCallback } from "react";
import { Icon } from "./Icon";
import { ModelMenu } from "./ModelMenu";
import { ApprovalMenu } from "./ApprovalMenu";
import { usePopover } from "./shared/usePopover";
import { ContextMeter } from "./ContextMeter";
import { vscode } from "../vscodeApi";
import { parseSlashCommand, resolveSkillCommand, buildSlashDropdownItems } from "../slash";
import { detectTrigger } from "../composerTrigger";
import { TriggerDropdown } from "./TriggerDropdown";
import type { InputAvailability } from "../inputAvailability";
import type { ApprovalModeView, ContextUsage, ThreadUsageView } from "../types";

type ImageAttachment = { name: string; mediaType: string; dataUrl: string; bytes: number };
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/tiff"]);

interface Props {
  availability: InputAvailability;
  draft: string;
  threadId?: string;
  onDraftChange: (text: string) => void;
  approval?: ApprovalModeView;
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
export function InputArea({ availability, draft, threadId = "", onDraftChange, approval, planMode = false, stepReview = true, threadUsage, teamNames, contextUsage = null, contextUsageStale = false }: Props) {
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

  const [addOpen, setAddOpen] = useState(false);
  const addRoot = useRef<HTMLDivElement>(null);
  const closeAdd = useCallback(() => setAddOpen(false), []);
  const addPosition = usePopover(addRoot, addOpen, closeAdd, 220);
  const [abortOpen, setAbortOpen] = useState(false);
  const abortRoot = useRef<HTMLDivElement>(null);
  const closeAbort = useCallback(() => setAbortOpen(false), []);
  const abortPosition = usePopover(abortRoot, abortOpen, closeAbort, 220);
  function insertTrigger(kind: "slash" | "file") {
    setAddOpen(false);
    const el = textareaRef.current;
    const cursor = el?.selectionStart ?? draft.length;
    const start = kind === "slash" ? 0 : cursor;
    const prefix = kind === "file" && start > 0 && !/\s/.test(draft[start - 1]) ? " " : "";
    const value = `${draft.slice(0, start)}${prefix}${kind === "slash" ? "/" : "@"}${draft.slice(start)}`;
    const end = start + prefix.length + 1;
    onDraftChange(value); maybeRequestCatalogs(value);
    setTrigger(detectTrigger(value, end)); setActiveIndex(0);
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(end, end); });
  }
  const effectiveApproval: ApprovalModeView = approval ?? {
    tier: "older", snapshot: null, mode: stepReview ? "ask" : "accept_edits",
    confirmed: true, pending: false, warningDismissed: false, error: null, notice: null,
  };
  const canSend = !availability.disabled && pendingImages === 0 && (draft.trim().length > 0 || imageAttachments.length > 0);

  return (
    <div
      className="relative rounded-composer border border-border-strong bg-[var(--input-bg)] px-3 pt-2 pb-2 text-[var(--input-fg)] focus-within:border-[var(--focus-ring)]"
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
              className="group relative h-14 w-14 shrink-0 overflow-hidden rounded-control"
              style={{ border: "1px solid var(--color-border-strong)", background: "var(--color-surface-2)" }}>
              <img width={56} height={56} src={image.dataUrl} alt={image.name} className="h-full w-full object-cover" />
              <button type="button" aria-label={`Remove ${image.name}`}
                onClick={() => { recoveryId.current = null; pendingSlashRef.current = null; setImageAttachments(current => current.filter((_, index) => index !== i)); }}
                className="absolute right-0.5 top-0.5 flex h-[18px] w-[18px] items-center justify-center rounded-full text-text opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
                style={{ background: "var(--color-surface-3)", border: "1px solid var(--color-border-strong)" }}>
                <Icon name="x" size={9} />
              </button>
            </div>
          ))}
          {Array.from({ length: pendingImages }, (_, i) => (
            <div key={`pending-${i}`} aria-hidden="true" className="h-14 w-14 shrink-0 animate-pulse rounded-control"
              style={{ border: "1px dashed var(--color-border-strong)", background: "var(--color-surface-2)" }} />
          ))}
          {pendingImages > 0 && <span role="status" className="sr-only">Reading images…</span>}
          {imageError && (
            <span role="alert" className="basis-full text-meta [overflow-wrap:anywhere]" style={{ color: "var(--color-red)" }}>{imageError}</span>
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
        placeholder={!availability.disabled && planMode ? "Describe a plan…" : availability.placeholder}
        aria-label="Chat input" name="message" autoComplete="off"
        className={[
          "w-full bg-transparent outline-none resize-none",
          "text-body leading-relaxed",
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

      <div data-testid="composer-footer" className="relative flex flex-wrap items-center gap-1 pt-1">
        <input ref={imageInputRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp,image/bmp,image/tiff" multiple
          className="hidden" aria-label="Attach images" onChange={e => { if (e.target.files) addImageFiles(e.target.files); e.target.value = ""; }} />
        <div ref={addRoot} className="relative shrink-0">
          <button type="button" aria-label="Add context" aria-haspopup="menu" aria-expanded={addOpen} onClick={() => setAddOpen(!addOpen)}
            className="flex h-7 w-7 items-center justify-center rounded-control text-text-2 hover:bg-surface-2 hover:text-text"><Icon name="plus" size={14} /></button>
          {addOpen && <div role="menu" aria-label="Add context" style={addPosition} className="absolute bottom-full z-50 mb-2 grid rounded-card border border-border-strong bg-surface-3 p-1 text-body text-text">
            <button type="button" role="menuitem" disabled={availability.disabled} onClick={() => { setAddOpen(false); imageInputRef.current?.click(); }} className="rounded-control px-2 py-2 text-left hover:bg-surface-2 disabled:opacity-50">Attach images</button>
            <button type="button" role="menuitem" disabled={availability.disabled} onClick={() => insertTrigger("file")} className="rounded-control px-2 py-2 text-left hover:bg-surface-2 disabled:opacity-50">Mention a file @</button>
            <button type="button" role="menuitem" disabled={availability.disabled} onClick={() => insertTrigger("slash")} className="rounded-control px-2 py-2 text-left hover:bg-surface-2 disabled:opacity-50">Commands /</button>
          </div>}
        </div>
        <ApprovalMenu approval={effectiveApproval} />
        <button type="button" aria-label="Plan Mode" aria-pressed={planMode} onClick={() => vscode.postMessage({ type: "setPlanMode", enabled: !planMode })}
          className="flex h-7 items-center gap-1 rounded-control px-1.5 text-meta hover:bg-surface-2"
          style={{ color: planMode ? "var(--color-accent-ink)" : "var(--color-text-3)", background: planMode ? "var(--accent-bg)" : undefined }}>
          <Icon name="list" size={13} />Plan
        </button>
        <span className="flex-1" />
        <ModelMenu />
        <ContextMeter usage={contextUsage} stale={contextUsageStale} threadUsage={threadUsage} teamNames={teamNames} />
        {availability.taskStop ? <div ref={abortRoot} className="relative flex shrink-0 items-center gap-0.5">
          <button type="button" onClick={() => handleAbort(false)} disabled={aborting} aria-label="Stop and keep changes" title="Stop the task; keep applied changes"
            className="flex h-8 w-8 items-center justify-center rounded-full border border-border-strong bg-surface-2 text-text disabled:opacity-50"><Icon name="stop" size={13} /></button>
          <button type="button" aria-label="Task stop options" aria-haspopup="menu" aria-expanded={abortOpen} disabled={aborting} onClick={() => setAbortOpen(!abortOpen)}
            className="flex h-7 w-5 items-center justify-center rounded-control text-text-3 hover:bg-surface-2"><Icon name="chev-d" size={10} /></button>
          {abortOpen && <div role="menu" aria-label="Task stop options" style={abortPosition} className="absolute bottom-full z-50 mb-2 rounded-card border border-border-strong bg-surface-3 p-1 text-body">
            <button type="button" role="menuitem" disabled={aborting} onClick={() => { handleAbort(true); setAbortOpen(false); }} aria-label="Stop and revert changes"
              className="w-full rounded-control px-2 py-2 text-left text-red hover:bg-surface-2">Stop and revert changes</button>
          </div>}
        </div> : availability.showStop ? <button type="button" onClick={handleStop} disabled={stopping} aria-label="Stop" title="Stop"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-border-strong bg-surface-2 text-text disabled:opacity-50"><Icon name="stop" size={13} /></button>
        : <button type="button" onClick={doSend} disabled={!canSend} aria-label="Send" title="Send (Enter)"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--accent-fill)] text-[var(--on-accent)] hover:bg-[var(--accent-fill-hover)] disabled:opacity-40 disabled:cursor-default"><Icon name="send" size={14} /></button>}
      </div>
    </div>
  );
}
