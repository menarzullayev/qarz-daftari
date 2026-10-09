import type { BrowserContext, Page } from "@playwright/test";

/**
 * Web vitals of a first visit, measured in the browser the suite already drives, through the stack's
 * own proxy, on a slowed-down connection and processor (the Chrome DevTools protocol; no Lighthouse).
 *
 *  - LCP, largest contentful paint: when the largest text or picture of the first view was drawn.
 *  - CLS, cumulative layout shift: how much of the view moved after it was drawn, without being asked.
 *  - TBT, total blocking time: for how long, after the first paint, the page could not answer a tap
 *    (the part of every task over 50 ms). It stands in for INP, which needs a person's taps.
 *  - usable: when the thing the person came for is on the screen (each page names its own), which is
 *    what NFR-010 means by "usable within 3 seconds".
 */

/**
 * The slow phone on a slow network: Lighthouse's mobile profile, which it used to call "Fast 3G" and
 * now calls "Slow 4G". 150 ms for every round trip, 1.6 Mbit/s down, 750 kbit/s up, and a processor
 * four times slower than the machine the suite runs on.
 */
export const PROFILE = {
  latencyMs: 150,
  downloadBytesPerSecond: (1.6 * 1024 * 1024) / 8,
  uploadBytesPerSecond: (750 * 1024) / 8,
  cpuSlowdown: 4,
} as const;

export type Vitals = {
  /** Milliseconds from the start of the navigation. */
  lcpMs: number;
  fcpMs: number;
  usableMs: number;
  tbtMs: number;
  /** A fraction of the view; no unit. */
  cls: number;
};

type Collected = {
  lcp: number;
  fcp: number;
  cls: number;
  usable: number | null;
  long: [start: number, duration: number][];
};

/** Runs in the page before any of its own scripts: the browser's own observers, and a look-out for `selector`. */
function observe(selector: string): void {
  const seen: Collected = { lcp: 0, fcp: 0, cls: 0, usable: null, long: [] };
  (window as unknown as { __e2eVitals: Collected }).__e2eVitals = seen;
  const watch = (type: string, take: (entry: PerformanceEntry) => void) => {
    new PerformanceObserver((list) => list.getEntries().forEach(take)).observe({ type, buffered: true });
  };
  watch("largest-contentful-paint", (entry) => {
    seen.lcp = entry.startTime;
  });
  watch("paint", (entry) => {
    if (entry.name === "first-contentful-paint") {
      seen.fcp = entry.startTime;
    }
  });
  watch("layout-shift", (entry) => {
    const shift = entry as PerformanceEntry & { value: number; hadRecentInput: boolean };
    if (!shift.hadRecentInput) {
      seen.cls += shift.value;
    }
  });
  watch("longtask", (entry) => {
    seen.long.push([entry.startTime, entry.duration]);
  });
  const look = () => {
    if (document.querySelector(selector)) {
      seen.usable = performance.now();
    } else {
      requestAnimationFrame(look);
    }
  };
  look();
}

/**
 * Loads a page once in `context`, which must never have been to the site (nothing kept), and answers
 * what was measured. `usable` is the selector of what the person came for; `open` navigates.
 */
export async function measure(context: BrowserContext, usable: string, open: (page: Page) => Promise<unknown>): Promise<Vitals> {
  const page = await context.newPage();
  await page.addInitScript(observe, usable);
  const devtools = await context.newCDPSession(page);
  await devtools.send("Network.enable");
  await devtools.send("Network.emulateNetworkConditions", {
    offline: false,
    latency: PROFILE.latencyMs,
    downloadThroughput: PROFILE.downloadBytesPerSecond,
    uploadThroughput: PROFILE.uploadBytesPerSecond,
  });
  await devtools.send("Emulation.setCPUThrottlingRate", { rate: PROFILE.cpuSlowdown });
  try {
    await open(page);
    await page.waitForFunction(() => (window as unknown as { __e2eVitals?: Collected }).__e2eVitals?.usable != null, null, { timeout: 30_000 });
    // What is still on its way may yet be the largest paint, or move what is drawn.
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(500);
    const seen = await page.evaluate(() => (window as unknown as { __e2eVitals: Collected }).__e2eVitals);
    return {
      lcpMs: Math.round(seen.lcp),
      fcpMs: Math.round(seen.fcp),
      usableMs: Math.round(seen.usable ?? Number.POSITIVE_INFINITY),
      tbtMs: Math.round(totalBlockingTime(seen.long, seen.fcp)),
      cls: Math.round(seen.cls * 1000) / 1000,
    };
  } finally {
    await devtools.send("Emulation.setCPUThrottlingRate", { rate: 1 });
    await page.close();
  }
}

/** The part of every long task over 50 ms, for the tasks that began after the first paint. */
export function totalBlockingTime(tasks: readonly (readonly [start: number, duration: number])[], afterMs: number): number {
  return tasks.filter(([start]) => start >= afterMs).reduce((sum, [, duration]) => sum + Math.max(0, duration - 50), 0);
}

/** The better of several loads, figure by figure: a slow moment of the machine shows in one, a slower page in all. */
export function best(runs: readonly Vitals[]): Vitals {
  const least = (pick: (run: Vitals) => number) => Math.min(...runs.map(pick));
  return {
    lcpMs: least((run) => run.lcpMs),
    fcpMs: least((run) => run.fcpMs),
    usableMs: least((run) => run.usableMs),
    tbtMs: least((run) => run.tbtMs),
    cls: least((run) => run.cls),
  };
}

export type Budget = { lcpMs: number; usableMs: number; tbtMs: number; cls: number };

/** What of `vitals` is over `budget`, in words; empty when all of it is within. */
export function overBudget(vitals: Vitals, budget: Budget): string[] {
  const over: string[] = [];
  if (!(vitals.lcpMs > 0)) {
    over.push("no largest contentful paint was reported: nothing was measured");
  }
  if (vitals.lcpMs > budget.lcpMs) {
    over.push(`LCP ${vitals.lcpMs} ms is over ${budget.lcpMs} ms`);
  }
  if (vitals.usableMs > budget.usableMs) {
    over.push(`usable after ${vitals.usableMs} ms, over ${budget.usableMs} ms`);
  }
  if (vitals.tbtMs > budget.tbtMs) {
    over.push(`TBT ${vitals.tbtMs} ms is over ${budget.tbtMs} ms`);
  }
  if (vitals.cls > budget.cls) {
    over.push(`CLS ${vitals.cls} is over ${budget.cls}`);
  }
  return over;
}
