import { startWorker, type WorkerManifest, type WorkerScope } from "./worker";

// The web panel's service worker: built to /panel/sw.js, a name that never changes, and served from
// there so that its scope is the panel and nothing above it. What it does and does not do is in
// worker.ts. The mark below is replaced, when the front end is built, by this build's list of shell
// files (vite.config.ts): the text between the quotes is JSON.
const manifest = JSON.parse("__QD_PANEL_WORKER_MANIFEST__") as WorkerManifest;

startWorker(self as unknown as WorkerScope, manifest);
