import { createThreadMetadataClient } from "./threadMetadataClient";
import React from "react";
import ReactDOM from "react-dom/client";
import DesktopApp from "./DesktopApp";
import { FileReferenceProvider } from "../file-references/FileReferenceContext";
import { acquireVsCodeApiSingleton } from "../sharedVscodeApi";
import { installThemeSync } from "../theme";
import { createLayoutClient } from "./layoutClient";
import { createBaselineClient } from "./baselineClient";
import { createChangesClient } from "./changesClient";
import { createWorkspaceClient } from "./workspaceClient";
import { createDraftClient } from "./draftClient";
import "../index.css";
const api = acquireVsCodeApiSingleton();
const baselines = createBaselineClient(message => api.postMessage(message));
const changes = createChangesClient(message => api.postMessage(message));
window.addEventListener("pagehide", () => { changes.dispose(); baselines.dispose(); }, { once: true });
installThemeSync(document, window, message => api.postMessage(message));
const threadMetadata = createThreadMetadataClient(message => api.postMessage(message));
const layout = createLayoutClient(message => api.postMessage(message));
const files = createWorkspaceClient(message => api.postMessage(message));
const drafts = createDraftClient(message => api.postMessage(message), window, () => layout.flush());
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><FileReferenceProvider><DesktopApp threadMetadata={threadMetadata} draftStore={drafts} layoutStore={layout} fileStore={files} changesStore={changes} baselineStore={baselines} /></FileReferenceProvider></React.StrictMode>,
);
