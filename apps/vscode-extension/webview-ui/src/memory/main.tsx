import React from "react";
import ReactDOM from "react-dom/client";
import MemoryApp from "./MemoryApp";
import "../index.css";
import { vscode } from "./vscodeApi";
import { installThemeSync } from "../theme";

// MemoryToHost is the memory panel's typed protocol; themeReady is the shared
// theme handshake every panel answers before its own messages.
installThemeSync(document, window, (message) => vscode.postMessage(message as never));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MemoryApp />
  </React.StrictMode>,
);
