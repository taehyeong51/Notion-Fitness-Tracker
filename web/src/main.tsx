import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
createRoot(document.getElementById("root")!).render(<App />);
if (
  import.meta.env.PROD &&
  window.isSecureContext &&
  "serviceWorker" in navigator
)
  navigator.serviceWorker.register("/sw.js").catch(() => {
    /* Safari LAN HTTP remains usable without PWA. */
  });
