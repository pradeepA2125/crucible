export interface ClaudeAccount {
  registrationId: string;
  label: string;
  signedIn: boolean;
}
export interface ClaudeSignIn {
  attemptId: string;
  state: "pending" | "succeeded" | "failed";
  registrationId: string | null;
  reason: string | null;
  message: string | null;
}
export type ClaudeInMsg =
  | { type: "settings/claudeLoad" }
  | { type: "settings/claudeModels"; registrationId: string; refresh?: boolean }
  | { type: "settings/claudeSignIn" }
  | { type: "settings/claudeCancel" }
  | { type: "settings/claudeSignOut"; registrationId: string }
  | { type: "settings/useClaude"; registrationId: string; model: string };
export type ClaudeOutMsg =
  | { type: "settings/claude"; accounts: ClaudeAccount[]; activeRegistrationId: string | null }
  | { type: "settings/claudeSignIn"; signIn: ClaudeSignIn }
  | { type: "settings/claudeError"; message: string; registrationId?: string }
  | { type: "settings/claudeModels"; registrationId: string; models: ClaudeModel[] }
  | { type: "settings/claudeSignedOut"; registrationId: string };

export interface ClaudeModel {
  slug: string;
  displayName: string;
  contextWindow: number | null;
  maxTokens: number | null;
}
