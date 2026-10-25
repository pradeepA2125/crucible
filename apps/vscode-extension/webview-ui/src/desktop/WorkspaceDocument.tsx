import { usePreviewFreshness } from "./usePreviewFreshness";
import { useEffect, useMemo, useState } from "react";
import type { Components } from "react-markdown";
import type { WorkspaceImage } from "../../../../desktop/src/workspace-file-types";
import { MarkdownContent } from "../components/shared/MarkdownContent";
import { vscode } from "../vscodeApi";
import type { ProjectFiles } from "./useProjectFiles";
import type { WorkspacePort } from "./workspaceClient";
/** Resolve document links without silently leaving the project or accepting schemes. */
export function documentPath(base: string, reference: string): string | undefined {
  if (!reference || /^(?:[a-z][a-z0-9+.-]*:|[\\/])/i.test(reference) || /[\x00-\x1f\x7f]/.test(reference)) return undefined;
  const raw = reference.split(/[?#]/)[0]; if (!raw) return undefined;
  const result = base.split("/").slice(0, -1);
  try {
    for (const encoded of raw.split("/")) {
      const part = decodeURIComponent(encoded);
      if (/[\\/\x00-\x1f\x7f:]/.test(part)) return undefined;
      if (part === "..") { if (!result.length) return undefined; result.pop(); }
      else if (part && part !== ".") result.push(part);
    }
  } catch { return undefined; }
  if (result.some((part, index) => part.toLowerCase() === ".git" || part.toLowerCase() === ".crucible" && result[index + 1]?.toLowerCase() === "state")) return undefined;
  return result.join("/") || undefined;
}
export function LocalImage({ image }: { image: WorkspaceImage }) {
  const [failed, setFailed] = useState(false), [playing, setPlaying] = useState(false);
  const animated = image.animated;
  useEffect(() => { setFailed(false); setPlaying(false); }, [image.sha256]);
  if (failed) return <span role="status">This image could not be displayed. Reload it or open it in your editor.</span>;
  return <span className="desktop-local-image">{animated && <button type="button" onClick={() => setPlaying(!playing)}>{playing ? "Hide animated image" : "Show image (may animate)"}</button>}
    {(!animated || playing) && <img src={image.dataUrl} alt={image.path} decoding="async" onError={() => setFailed(true)} />}
    <small>{image.width} × {image.height} · {image.size.toLocaleString()} bytes</small>
  </span>;
}
function DocumentImage({ path, port, active, activate }: { path?: string; port?: WorkspacePort; active: boolean; activate(): void }) {
  const [image, setImage] = useState<WorkspaceImage>(), [error, setError] = useState("");
  const [stale, setStale] = useState(false), [reload, setReload] = useState(0);
  usePreviewFreshness(port, image && !stale ? [{ path: image.path, revision: image.revision }] : [], active,
    () => setStale(true), () => setError("Image freshness could not be checked. Reload the image to verify it."));
  useEffect(() => {
    let live = true; setImage(undefined); setError(""); setStale(false);
    if (active && path && port?.image) void port.image(path).then(value => { if (live) setImage(value); }, reason => { if (live) setError(reason instanceof Error ? reason.message : "Image unavailable."); });
    return () => { live = false; };
  }, [active, path, port, reload]);
  if (!path) return <span>External or protected image omitted.</span>;
  return <span className="desktop-document-image"><button type="button" onClick={activate}>{active ? "Hide image" : `Show local image: ${path}`}</button>{active && stale && <span role="status">Changed — reload this image.</span>}{active && <button type="button" onClick={() => setReload(value => value + 1)}>Reload image</button>}{active && (error ? <span role="status">{error}</span> : image ? <LocalImage image={image} /> : <span>Loading image…</span>)}</span>;
}
export function WorkspaceDocument({ content, path, model }: { content: string; path: string; model: ProjectFiles }) {
  const [activeImage, setActiveImage] = useState<string>();
  useEffect(()=>{window.dispatchEvent(new Event("desktop/findTargetChanged"));},[path,content]);
  useEffect(() => { setActiveImage(undefined); }, [path, content]);
  const overrides = useMemo<Components>(() => ({
    a: ({ href, children, node }) => {
      const reference = typeof node?.properties?.["data-file-reference"] === "string" ? node.properties["data-file-reference"] : href ?? "";
      const local = documentPath(path, reference), external = /^https:\/\//i.test(reference);
      return <a href={external ? reference : "#workspace-file"} onClick={event => { event.preventDefault(); if (local) model.open(local); else if (external) vscode.postMessage({ type: "settings/openExternal", url: reference }); }} aria-disabled={!local && !external}>{children}</a>;
    },
    img: ({ src, node }) => {
      const id = String(node?.position?.start.offset ?? src);
      return <DocumentImage path={documentPath(path, src ?? "")} port={model.port} active={activeImage === id} activate={() => setActiveImage(value => value === id ? undefined : id)} />;
    },
  }), [path, model.open, model.port, activeImage]);
  return <div className="desktop-document"><MarkdownContent content={content} overrides={overrides} disableFileReferences /></div>;
}
