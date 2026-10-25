import { z } from "zod";
import type { CrucibleController } from "./controller.js";
import type { DraftSession } from "./drafts/draft-session.js";
const id = z.string().min(1).max(1024);
export const gateActionFields = { threadId: id, gateId: id, kind: z.enum(["mode", "clarify", "feedback"]),
  value: z.string().min(1).max(65536), draft: z.object({ revision: z.number().int().nonnegative(), version: z.number().int().nonnegative() }).strict().optional() };
export const GateActionSchema = z.object({ type: z.literal("gateDecision"), ...gateActionFields }).strict();
export type GateAction = z.infer<typeof GateActionSchema>;
export const gateDraftKey = (threadId: string, gateId: string, kind: "mode" | "clarify") => `@gate:${JSON.stringify([threadId, gateId, kind])}`;
/** Both hosts use the same draft acknowledgement and serialized preference writer. */
export function createGateActions(deps: { controller: CrucibleController; drafts: DraftSession;
  setPlanMode(value: boolean, isCurrent?: () => boolean): Promise<void>; post(message: Record<string, unknown>): void }) {
  let generation = 0, queue: Promise<unknown> = Promise.resolve();
  const enqueue = (work: () => Promise<void>) => { const next = queue.catch(() => {}).then(work); queue = next; return next; };
  return {
    setPlanMode: (value: boolean) => { ++generation; return enqueue(() => deps.setPlanMode(value)); },
    submit: async (raw: unknown) => {
      const action = GateActionSchema.parse(raw), captured = generation;
      const key = gateDraftKey(action.threadId, action.gateId, action.kind === "feedback" ? "mode" : action.kind);
      if (deps.drafts.hasSubmission || action.draft && !deps.drafts.matchesFeedback(key, action.draft.revision, action.draft.version,
          action.kind === "feedback" || action.kind === "clarify" ? action.value : undefined)) {
        deps.post({ type: "decisionRetry", threadId: action.threadId, gateId: action.gateId, kind: action.kind === "feedback" ? "mode" : action.kind }); return;
      }
      await deps.controller.handleExactGateDecision(action, async isCurrent => {
        if (action.draft) deps.drafts.acceptFeedback(key, action.draft.revision, action.draft.version);
        if (action.kind === "mode" && action.value === "implement" && isCurrent()) {
          await enqueue(async () => {
            if (captured !== generation || !isCurrent()) return;
            await deps.setPlanMode(false, isCurrent);
            if (captured === generation && isCurrent()) deps.post({ type: "planModeState", enabled: false });
          });
        }
      });
    },
  };
}
