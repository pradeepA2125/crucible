import React from "react";
import ReactDOM from "react-dom/client";
import { installThemeSync } from "../theme";
import { vscode } from "./vscodeApi";
import GraphApp from "./GraphApp";
import "../index.css";
import "./graph.css";

installThemeSync(document, window, message => vscode.postMessage(message as never));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <GraphApp />
  </React.StrictMode>,
);
