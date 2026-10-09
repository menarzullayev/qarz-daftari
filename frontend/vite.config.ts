import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

import { collectInitialAssets } from "./scripts/size.ts";

/** Where the web panel's service worker is built to: a name that never changes, beside the panel's page. */
export const WORKER_FILE = "panel/sw.js";
/** What the build replaces in the worker's script with this build's list of shell files. */
export const WORKER_MARK = "__QD_PANEL_WORKER_MANIFEST__";
const WORKER_ENTRY = "panel-sw";

/**
 * The worker's list for a build: the panel's page and the scripts and style sheets that page names on
 * first load, and a mark that changes when any of them does (their names carry the hash of their
 * content, and the page names them). Screens loaded on demand are not listed: the worker keeps one
 * when it is first opened.
 */
export function workerManifest(panelHtml: string): { build: string; shell: string[] } {
  const files = collectInitialAssets(panelHtml).local.filter((path) => path.startsWith("/assets/"));
  if (files.length === 0) {
    throw new Error("the panel's page names no built file: is the build output intact?");
  }
  const shell = ["/panel/", ...files.sort()];
  const build = createHash("sha256").update(panelHtml).update("\n").update(shell.join("\n")).digest("hex").slice(0, 16);
  return { build, shell };
}

/** The list as text that is safe between the quotes of a script's string, whichever quotes the minifier chose. */
export function quotedManifest(manifest: { build: string; shell: string[] }): string {
  return JSON.stringify(manifest).replace(/[\\"'`$]/g, "\\$&");
}

/** Writes this build's list of shell files into the built service worker, after everything is on disk. */
function panelWorker(): Plugin {
  let outDir = "dist";
  return {
    name: "qd-panel-worker",
    apply: "build",
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    generateBundle(_options, bundle) {
      const worker = bundle[WORKER_FILE];
      if (worker?.type !== "chunk") {
        this.error(`the panel's service worker was not built to ${WORKER_FILE}`);
      }
      // A service worker is one classic script: a part shared with the pages would be an import it
      // cannot make.
      if (worker.imports.length > 0 || worker.dynamicImports.length > 0) {
        this.error(`the panel's service worker must be one file, but it imports ${[...worker.imports, ...worker.dynamicImports].join(", ")}`);
      }
    },
    closeBundle() {
      const path = resolve(outDir, WORKER_FILE);
      const script = readFileSync(path, "utf8");
      if (script.split(WORKER_MARK).length !== 2) {
        this.error(`${WORKER_FILE} must hold the mark ${WORKER_MARK} exactly once`);
      }
      const manifest = workerManifest(readFileSync(resolve(outDir, "panel", "index.html"), "utf8"));
      writeFileSync(path, script.replace(WORKER_MARK, () => quotedManifest(manifest)));
    },
  };
}

// One application, four pages and a worker (ADR-011): the staff workspace served to the Telegram Mini
// App, the web panel for desktop, the administration panel, and the page behind a customer's read-only
// link, which shares nothing with the others but the design tokens. The web panel alone can be
// installed; its service worker is built to /panel/sw.js.
export default defineConfig({
  plugins: [react(), panelWorker()],
  build: {
    rollupOptions: {
      input: {
        app: resolve(import.meta.dirname, "app/index.html"),
        panel: resolve(import.meta.dirname, "panel/index.html"),
        admin: resolve(import.meta.dirname, "admin/index.html"),
        k: resolve(import.meta.dirname, "k/index.html"),
        [WORKER_ENTRY]: resolve(import.meta.dirname, "src/panel/pwa/sw.ts"),
      },
      output: {
        entryFileNames: (chunk) => (chunk.name === WORKER_ENTRY ? WORKER_FILE : "assets/[name]-[hash].js"),
      },
    },
  },
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx", "scripts/**/*.test.ts"],
    // Russian text is at hand in every test file; see the file for why.
    setupFiles: ["scripts/testLanguages.ts"],
  },
});
