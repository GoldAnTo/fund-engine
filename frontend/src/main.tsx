import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { FundClawApp } from "@/app/FundClawApp";

import "@/styles/global.css";
import "@/styles/app.css";

const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("#root element not found");
}

createRoot(rootElement).render(
  <StrictMode>
    <FundClawApp />
  </StrictMode>,
);
