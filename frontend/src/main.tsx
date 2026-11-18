import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./index.css";

async function enableMocking(): Promise<void> {
  if (import.meta.env.VITE_USE_MOCKS === "false") {
    return;
  }

  if (import.meta.env.DEV || import.meta.env.VITE_USE_MOCKS === "true") {
    const { worker } = await import("./mocks/browser");
    await worker.start({
      onUnhandledRequest: "bypass",
    });
  }
}

void enableMocking().then(() => {
  const rootEl = document.getElementById("root");
  if (!rootEl) {
    throw new Error("Root element #root not found");
  }
  createRoot(rootEl).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
});
