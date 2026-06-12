import { useState } from "react";
import { Icon } from "./Icon";
import type { ThreadSummary } from "../types";

interface Props {
  threads: ThreadSummary[];
  activeThreadId: string;
  /** UX Rule 3: true while a local stream is appending; blocks navigation. */
  navLocked: boolean;
  onSelect: (threadId: string) => void;
  onNewChat: () => void;
}

// ── Date grouping ─────────────────────────────────────────────────────────────

type DayGroup = "Today" | "Yesterday" | "This week" | "Older";

function getDayGroup(createdAt: string, now: Date): DayGroup {
  const d = new Date(createdAt);
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterdayStart = new Date(todayStart.getTime() - 86400_000);
  const weekStart = new Date(todayStart.getTime() - 6 * 86400_000);

  if (d >= todayStart) return "Today";
  if (d >= yesterdayStart) return "Yesterday";
  if (d >= weekStart) return "This week";
  return "Older";
}

const GROUP_ORDER: DayGroup[] = ["Today", "Yesterday", "This week", "Older"];

// ── Relative time ─────────────────────────────────────────────────────────────

function relativeTime(createdAt: string, now: Date): string {
  const d = new Date(createdAt);
  const diffMs = now.getTime() - d.getTime();
  const diffSec = Math.floor(diffMs / 1000);

  if (diffSec < 60) return "just now";

  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} min ago`;

  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} hr${diffHr === 1 ? "" : "s"} ago`;

  // "Yesterday" label for the relative time line (24–48h window).
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterdayStart = new Date(todayStart.getTime() - 86400_000);
  if (d >= yesterdayStart) return "Yesterday";

  // Short date (e.g. "Jun 3").
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// ── HistoryView ───────────────────────────────────────────────────────────────

/**
 * HistoryView — left-panel thread list with day groups and search.
 *
 * The panel shows:
 *  - Header: logo, "AI Editor" title, "+ New Chat" button
 *  - Search: client-side title filter
 *  - Thread list: grouped by creation day, with relative time per row
 *
 * navLocked disables "+ New Chat" and suppresses onSelect while a turn streams.
 * No status chips or message counts — those need backend support (deferred).
 */
export function HistoryView({ threads, activeThreadId, navLocked, onSelect, onNewChat }: Props) {
  const [query, setQuery] = useState("");
  const now = new Date();

  const filtered = query
    ? threads.filter((t) =>
        t.title.toLowerCase().includes(query.toLowerCase()),
      )
    : threads;

  // Group threads preserving original order within each group.
  const grouped = new Map<DayGroup, ThreadSummary[]>();
  for (const group of GROUP_ORDER) grouped.set(group, []);
  for (const thread of filtered) {
    const group = getDayGroup(thread.updatedAt ?? thread.createdAt, now);
    grouped.get(group)!.push(thread);
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* ── Header ── */}
      <div
        className="flex items-center gap-2 px-3 py-2.5 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--color-border)" }}
      >
        {/* Logo tile */}
        <span
          className="flex items-center justify-center rounded-md flex-shrink-0"
          style={{
            width: 22,
            height: 22,
            background:
              "linear-gradient(180deg, var(--color-accent-deep), var(--color-accent-hot))",
            boxShadow: "0 0 10px var(--accent-glow)",
            color: "#fff",
          }}
        >
          <Icon name="spark" size={12} />
        </span>

        {/* Title */}
        <span
          className="font-semibold flex-1 min-w-0 truncate"
          style={{ fontSize: 13 }}
        >
          AI Editor
        </span>

        {/* + New Chat */}
        <button
          type="button"
          onClick={() => {
            if (!navLocked) onNewChat();
          }}
          disabled={navLocked}
          title={navLocked ? "A turn is in progress" : undefined}
          className={[
            "inline-flex items-center gap-1 px-2 py-[4px] rounded-md",
            "text-[11px] font-[550] border transition-all duration-150",
            "disabled:opacity-50 disabled:cursor-not-allowed",
          ].join(" ")}
          style={{
            color: "var(--color-accent-ink)",
            background: "var(--accent-bg)",
            borderColor: "var(--accent-brd)",
          }}
          onMouseEnter={(e) => {
            if (!navLocked) {
              (e.currentTarget as HTMLButtonElement).style.boxShadow =
                "0 0 10px var(--accent-glow)";
            }
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.boxShadow = "none";
          }}
        >
          <Icon name="plus" size={10} />
          New Chat
        </button>
      </div>

      {/* ── Search ── */}
      <div className="px-3 py-2 flex-shrink-0">
        <div
          className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg border"
          style={{
            background: "var(--color-surface)",
            borderColor: "var(--color-border-strong)",
          }}
        >
          <span style={{ color: "var(--color-text-4)" }}>
            <Icon name="search" size={11} />
          </span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search chats…"
            aria-label="Search threads"
            className="flex-1 min-w-0 bg-transparent outline-none text-xs text-text placeholder:text-text-4"
          />
        </div>
      </div>

      {/* ── Thread list ── */}
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {filtered.length === 0 ? (
          <p className="text-center text-xs text-text-4 mt-6 px-3">
            {query ? "No matching chats" : "No chats yet"}
          </p>
        ) : (
          GROUP_ORDER.map((group) => {
            const rows = grouped.get(group)!;
            if (rows.length === 0) return null;
            return (
              <div key={group}>
                {/* Day group label */}
                <p
                  className="uppercase tracking-wider text-text-4 px-2.5 pt-3 pb-1 select-none"
                  style={{ fontSize: 10 }}
                >
                  {group}
                </p>

                {/* Thread rows */}
                {rows.map((thread) => (
                  <ThreadRow
                    key={thread.threadId}
                    thread={thread}
                    isActive={thread.threadId === activeThreadId}
                    navLocked={navLocked}
                    now={now}
                    onClick={() => {
                      if (!navLocked) onSelect(thread.threadId);
                    }}
                  />
                ))}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

// ── ThreadRow ─────────────────────────────────────────────────────────────────

interface ThreadRowProps {
  thread: ThreadSummary;
  isActive: boolean;
  navLocked: boolean;
  now: Date;
  onClick: () => void;
}

// Mockup frame 1 `.chip-status` — Running (accent, pulse dot), Review (amber),
// Done (green); Failed (red) is ours, the mockup omits it.
const CHIP_CONFIG = {
  running: { label: "Running", color: "var(--color-accent-ink)", bg: "var(--accent-bg)", brd: "var(--accent-brd)" },
  review:  { label: "Review",  color: "var(--color-amber)", bg: "var(--amber-bg)", brd: "rgba(251,191,36,.25)" },
  done:    { label: "Done",    color: "var(--color-green)", bg: "var(--green-bg)", brd: "var(--green-brd)" },
  failed:  { label: "Failed",  color: "var(--color-red)", bg: "var(--red-bg)", brd: "var(--red-brd)" },
} as const;

function StatusChip({ status }: { status?: ThreadSummary["status"] }) {
  if (!status) return null;
  const cfg = CHIP_CONFIG[status];
  return (
    <span
      className="inline-flex items-center gap-1 font-semibold px-[7px] py-px rounded-full flex-shrink-0"
      style={{ fontSize: "9.5px", color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.brd}` }}
    >
      {status === "running" && (
        <span
          className="w-[5px] h-[5px] rounded-full flex-shrink-0"
          style={{ background: "var(--color-accent)", animation: "pulse 1.5s ease-in-out infinite" }}
          aria-hidden="true"
        />
      )}
      {cfg.label}
    </span>
  );
}

function ThreadRow({ thread, isActive, navLocked, now, onClick }: ThreadRowProps) {
  const relTime = relativeTime(thread.updatedAt ?? thread.createdAt, now);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      className={[
        "group rounded-lg px-2.5 py-2 flex items-center gap-2",
        "transition-colors duration-100",
        navLocked ? "cursor-not-allowed" : "cursor-pointer",
        isActive
          ? "bg-surface border border-border"
          : "hover:bg-surface border border-transparent",
      ].join(" ")}
      style={
        isActive
          ? { boxShadow: "inset 2px 0 0 var(--color-accent)" }
          : undefined
      }
    >
      {/* Thread title + time */}
      <div className="flex flex-col gap-0.5 flex-1 min-w-0">
        <span
          className={[
            "text-xs leading-snug line-clamp-2",
            isActive ? "text-text" : "text-text-2 group-hover:text-text",
          ].join(" ")}
          style={{
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {thread.title}
        </span>
        <span
          className="text-text-4 tabular-nums"
          style={{ fontSize: "10.5px" }}
        >
          {relTime}
          {typeof thread.messageCount === "number" && thread.messageCount > 0 && (
            <> · {thread.messageCount} {thread.messageCount === 1 ? "message" : "messages"}</>
          )}
        </span>
      </div>

      <StatusChip status={thread.status} />

      {/* Chevron */}
      <span
        className={[
          "flex-shrink-0 text-text-4 transition-colors duration-100",
          "group-hover:text-accent",
        ].join(" ")}
      >
        <Icon name="chev-r" size={10} />
      </span>
    </div>
  );
}

// Export helpers for testing.
export { getDayGroup, relativeTime };
