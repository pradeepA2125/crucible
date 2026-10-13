import React from "react";
import ReactDOM from "react-dom/client";
import SettingsApp from "./SettingsApp";
import "../index.css";
import { acquireVsCodeApiSingleton } from "../sharedVscodeApi";
import { installThemeSync } from "../theme";

// The shared singleton handle (untyped postMessage): themeReady belongs to no
// panel's own protocol; the host answers it before panel-specific handling.
const themeApi = acquireVsCodeApiSingleton();
installThemeSync(document, window, (message) => themeApi.postMessage(message));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <SettingsApp />
  </React.StrictMode>,
);
