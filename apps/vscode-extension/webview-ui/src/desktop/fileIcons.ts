import type { IconName } from "../components/Icon";
/** Static extension hints; never read content or load an icon pack. */
export function fileIcon(path: string): IconName {
  if (/\.(png|jpe?g|gif|webp|bmp|svg|ico)$/i.test(path)) return "image";
  if (/\.(html?|css|scss)$/i.test(path)) return "hash";
  if (/\.(jsonc?|ya?ml|toml|[cm]?[jt]sx?|py|rs|go|java|c|h|cpp|sh)$/i.test(path)) return "braces";
  if (/\.(md|markdown)$/i.test(path)) return "markdown";
  if (/\.(txt|log|csv)$/i.test(path)) return "list";
  return "file";
}
