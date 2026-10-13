import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { FileReferenceProvider } from "./file-references/FileReferenceContext";
import "./index.css";
import { installThemeSync } from "./theme";

installThemeSync();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <FileReferenceProvider><App /></FileReferenceProvider>
  </React.StrictMode>,
);
