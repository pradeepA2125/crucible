import { z } from "zod";

export const ClaudeAccountSchema = z.object({
  registrationId: z.string(), label: z.string(), signedIn: z.boolean(),
});
export type ClaudeAccount = z.infer<typeof ClaudeAccountSchema>;

export const ClaudeSignInSchema = z.object({
  attemptId: z.string(), state: z.enum(["pending", "succeeded", "failed"]),
  registrationId: z.string().nullable(), reason: z.string().nullable(), message: z.string().nullable(),
});
export type ClaudeSignIn = z.infer<typeof ClaudeSignInSchema>;

export const ClaudeModelSchema = z.object({
  slug: z.string(), displayName: z.string(),
  contextWindow: z.number().int().positive().nullable(),
  maxTokens: z.number().int().positive().nullable(),
});
export type ClaudeModel = z.infer<typeof ClaudeModelSchema>;
