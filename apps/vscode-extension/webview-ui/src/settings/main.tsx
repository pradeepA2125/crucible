import React from "react";
import ReactDOM from "react-dom/client";
import SettingsApp from "./SettingsApp";
import "../index.css";
import { installThemeSync } from "../theme";

installThemeSync();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <SettingsApp />
  </React.StrictMode>,
);
