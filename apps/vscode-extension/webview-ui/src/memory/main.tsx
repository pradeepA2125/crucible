import React from "react";
import ReactDOM from "react-dom/client";
import MemoryApp from "./MemoryApp";
import "../index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MemoryApp />
  </React.StrictMode>,
);
