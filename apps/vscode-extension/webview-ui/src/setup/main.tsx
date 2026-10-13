import React from "react";
import ReactDOM from "react-dom/client";
import SetupApp from "./SetupApp";
import "../index.css";
import { installThemeSync } from "../theme";

installThemeSync();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <SetupApp />
  </React.StrictMode>,
);
