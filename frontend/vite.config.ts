import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// One application, three entry points (ADR-011): the staff workspace served to the Telegram Mini App,
// the web panel for desktop, and the administration panel.
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        app: resolve(import.meta.dirname, "app/index.html"),
        panel: resolve(import.meta.dirname, "panel/index.html"),
        admin: resolve(import.meta.dirname, "admin/index.html"),
      },
    },
  },
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
