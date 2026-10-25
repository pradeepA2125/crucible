/** Workspace paths are relative; linkTarget is display-only text, never a path authority. */
export const WORKSPACE_FILE_LIMITS = { page: 200, directory: 10000, textBytes: 256 * 1024, pathLength: 4096, depth: 128 } as const;
export type WorkspaceFileFailure = "invalid" | "protected" | "symlink" | "missing" | "unreadable" | "changed" | "special" | "unsupported";
export interface WorkspaceEntry {
  name: string; path: string; kind: "directory" | "file" | "symlink" | "protected" | "unavailable" | "special";
  hidden: boolean; size?: number; linkTarget?: string; targetPath?: string; targetLocation?: "inside" | "outside" | "protected" | "unavailable";
}
export interface WorkspaceDirectory {
  path: string; entries: WorkspaceEntry[]; captured: number; limited: boolean; nextCursor?: string;
}
export type WorkspacePreview = { path: string; size: number; revision?: string } & (
  { kind: "text"; text: string; truncated: boolean; sha256?: string } | { kind: "binary" }
);

export interface WorkspaceImage { path: string; revision?: string; kind: "image"; mediaType: "image/png" | "image/jpeg" | "image/gif" | "image/webp" | "image/bmp"; dataUrl: string; size: number; sha256: string; width: number; height: number; animated: boolean; }
