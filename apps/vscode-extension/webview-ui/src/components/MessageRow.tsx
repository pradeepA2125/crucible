import type { ChatMsg, DiffEntry, ToolEventView } from "../types";
import { PlanCard } from "./messages/PlanCard";
import { DiffCard } from "./messages/DiffCard";
import { AgentRow } from "./messages/AgentRow";
import { QAMessage } from "./messages/QAMessage";
import { UserMessage } from "./messages/UserMessage";
import { CardShell } from "./shared/CardShell";
import { Icon } from "./Icon";

interface Props {
  msg: ChatMsg;
  planVersion?: number;
}

// ── TaskCreatedRow ────────────────────────────────────────────────────────────

function TaskCreatedRow({ msg }: { msg: ChatMsg }) {
  const taskId = msg.taskId ?? msg.content;
  return (
    <div
      className="flex items-center gap-2 px-3 py-2 rounded-lg border"
      style={{
        background: "var(--color-surface)",
        borderColor: "var(--color-border)",
        fontSize: "11px",
      }}
    >
      <span style={{ color: "var(--color-accent)", flexShrink: 0 }}>
        <Icon name="bolt" size={11} />
      </span>
      <span className="text-text-2">Task created</span>
      <span
        className="font-mono truncate"
        style={{ color: "var(--color-code)", fontSize: "10px" }}
      >
        {taskId}
      </span>
    </div>
  );
}

// ── LegacyGateSummary ─────────────────────────────────────────────────────────

// Legacy persisted gate messages (pre-Class-A threads) render read-only — interactive gates live ONLY in the /live slot.

type GateType = "scope_card" | "validation_card" | "command_card";

const GATE_CONFIG: Record<
  GateType,
  { icon: "file" | "warn" | "term"; title: string; subtitleKey: string }
> = {
  scope_card: {
    icon: "file",
    title: "Scope extension (resolved)",
    subtitleKey: "files",
  },
  validation_card: {
    icon: "warn",
    title: "Validation review (resolved)",
    subtitleKey: "summary",
  },
  command_card: {
    icon: "term",
    title: "Command approval (resolved)",
    subtitleKey: "command",
  },
};

function LegacyGateSummary({ msg }: { msg: ChatMsg }) {
  const type = msg.type as GateType;
  const cfg = GATE_CONFIG[type];

  let subtitle: string | undefined;
  const meta = msg.metadata ?? {};

  if (type === "scope_card") {
    const files = meta.files as string[] | undefined;
    subtitle = files && files.length > 0 ? files.join(", ") : undefined;
  } else if (type === "validation_card") {
    subtitle = meta.summary as string | undefined;
  } else if (type === "command_card") {
    subtitle = meta.command as string | undefined;
  }

  return (
    <CardShell
      icon={cfg.icon}
      iconColor="var(--color-text-3)"
      title={cfg.title}
      titleColor="var(--color-text-2)"
      subtitle={subtitle}
    />
  );
}

// ── MessageRow ────────────────────────────────────────────────────────────────

/**
 * MessageRow — dispatches a persisted ChatMsg to the correct component.
 *
 * CRITICAL: switch on msg.type FIRST, role second.
 * Dispatching on role first was a real bug (cards rendered as text forever).
 */
export function MessageRow({ msg, planVersion }: Props) {
  switch (msg.type) {
    case "plan_card":
      return (
        <PlanCard
          content={msg.content}
          taskId={(msg.metadata?.taskId as string) ?? msg.taskId ?? ""}
          readOnly
          version={planVersion}
        />
      );

    case "diff_card":
      return (
        <DiffCard
          taskId={msg.taskId ?? (msg.metadata?.taskId as string) ?? ""}
          diffEntries={(msg.metadata?.diff_entries as DiffEntry[]) ?? []}
          resolved={
            (msg.metadata?.resolved as "applied" | "discarded" | undefined) ??
            null
          }
          thinkingLog={msg.metadata?.thinking_log as string[] | undefined}
        />
      );

    case "task_card":
      return <TaskCreatedRow msg={msg} />;

    case "scope_card":
    case "validation_card":
    case "command_card":
      return <LegacyGateSummary msg={msg} />;

    // diff_summary falls through to text/role-based dispatch
    case "diff_summary":
    case "text":
    default: {
      if (msg.role === "user") {
        return <UserMessage content={msg.content} />;
      }

      if (msg.metadata?.breadcrumb === true) {
        return (
          <AgentRow
            content={msg.content}
            breadcrumb
            thinkingLog={msg.metadata?.thinking_log as string[] | undefined}
            toolEvents={
              (msg.metadata?.tool_events as ToolEventView[]) ?? []
            }
          />
        );
      }

      // Non-empty agent text: use AgentRow if tool_events present (QAMessage has no pills).
      if (msg.content !== "") {
        const toolEvents = msg.metadata?.tool_events as
          | ToolEventView[]
          | undefined;
        if (toolEvents && toolEvents.length > 0) {
          return (
            <AgentRow
              content={msg.content}
              thinkingLog={
                msg.metadata?.thinking_log as string[] | undefined
              }
              toolEvents={toolEvents}
            />
          );
        }
        return (
          <QAMessage
            content={msg.content}
            thinkingLog={msg.metadata?.thinking_log as string[] | undefined}
          />
        );
      }

      // Empty content fallback — e.g. a sealed bubble with only tool events.
      return (
        <AgentRow
          content={msg.content}
          thinkingLog={msg.metadata?.thinking_log as string[] | undefined}
          toolEvents={
            (msg.metadata?.tool_events as ToolEventView[]) ?? []
          }
        />
      );
    }
  }
}
