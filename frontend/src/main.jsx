import { createRoot } from "react-dom/client";

import App from "@/App";
import { AppProviders } from "@/app/providers";

import "@/index.css";

createRoot(document.getElementById("root")).render(
  <AppProviders>
    <App />
  </AppProviders>,
);
