import type { IconName } from "../Icon";

/** Map tool names to icons. */
export function toolIcon(tool: string): IconName {
  const map: Record<string, IconName> = {
    search_code: "search",
    read_file: "file",
    run_command: "term",
    query_graph: "diff",
    list_directory: "list",
    search_semantic: "search",
  };
  return map[tool] ?? "bolt";
}
