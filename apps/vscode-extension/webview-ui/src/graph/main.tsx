import React from "react";
import ReactDOM from "react-dom/client";
import GraphApp from "./GraphApp";
import "../index.css";
import "./graph.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <GraphApp />
  </React.StrictMode>,
);
