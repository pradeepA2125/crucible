import { createContext } from "react";
export interface ReviewCommentSource { identity: string; label: string }
export interface ReviewDraftSink { threadId: string; ready: boolean; reason?: string; append(text: string): string | undefined }
/** Only the main desktop composer registers; undefined keeps other hosts unchanged. */
export type RegisterReviewDraft = (sink: ReviewDraftSink) => () => void;
export const ReviewDraft = createContext<ReviewDraftSink | undefined>(undefined);
export const REVIEW_APPEND_BYTES = 2 * 1024 * 1024;
export function reviewLineLabel(line: { oldNumber?: number; newNumber?: number }): string {
  return [line.oldNumber === undefined ? "" : `old line ${line.oldNumber}`, line.newNumber === undefined ? "" : `new line ${line.newNumber}`].filter(Boolean).join(" / ");
}
/** Walk only the bounded prefix, preserving complete Unicode code points. */
export function reviewExcerpt(value: string): { text: string; shortened: boolean } {
  let text = "", count = 0;
  for (const point of value) { if (count++ === 1000) return { text, shortened: true }; text += point; }
  return { text, shortened: false };
}
export function reviewCommentText(path: string, source: ReviewCommentSource, line: { text: string; oldNumber?: number; newNumber?: number }, note: string): string {
  const excerpt = reviewExcerpt(line.text), runs = excerpt.text.match(/`+/g) ?? [];
  const fence = "`".repeat(Math.max(3, ...runs.map(run => run.length + 1)));
  return `Review comment (recorded excerpt)\nSource: ${JSON.stringify(source.label)}\nFile: ${JSON.stringify(path)} · ${reviewLineLabel(line)}\n${fence}text\n${excerpt.text}\n${fence}${excerpt.shortened ? "\n[Excerpt shortened to 1,000 characters]" : ""}\nNote: ${note.trim().slice(0, 2000)}`;
}
