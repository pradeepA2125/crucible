import type { CatalogSnapshot, FileReferenceCatalog } from "./catalog.js";
import type { FileReferenceEntry } from "./matching.js";

export interface FileReferenceCatalogMessage {
  type: "fileReferenceCatalog";
  revision: number;
  entries: FileReferenceEntry[];
}

export interface FileReferenceBridge {
  handle(message: unknown): Promise<boolean>;
  dispose(): void;
}

export function createFileReferenceBridge<U>(
  catalog: FileReferenceCatalog<U>,
  open: (reference: unknown) => Promise<void>,
  post: (message: FileReferenceCatalogMessage) => void,
): FileReferenceBridge {
  let disposed = false;
  let publications = 0;
  const publish = (snapshot: CatalogSnapshot<U>) => {
    if (disposed) return;
    publications++;
    post({
      type: "fileReferenceCatalog",
      revision: snapshot.revision,
      entries: snapshot.files.map(({ entry }) => ({
        workspaceId: entry.workspaceId,
        workspaceLabel: entry.workspaceLabel,
        relativePath: entry.relativePath,
      })),
    });
  };
  const subscription = catalog.subscribe(publish);
  return {
    async handle(message) {
      if (disposed || !message || typeof message !== "object" || Array.isArray(message)) return false;
      const envelope = message as Record<string, unknown>;
      if (envelope["type"] === "openFileReference") {
        await open(envelope["reference"]);
        return true;
      }
      if (envelope["type"] === "requestFileReferenceCatalog") {
        const before = publications;
        const snapshot = await catalog.refresh();
        // Normally refresh publishes via the subscription. A cached/disposed
        // catalog may return without publication; send that snapshot once.
        if (publications === before) publish(snapshot);
        return true;
      }
      return false;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      subscription.dispose();
    },
  };
}
