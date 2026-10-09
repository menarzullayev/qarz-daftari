// @vitest-environment jsdom
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { I18nProvider } from "../../i18n/I18nProvider";
import {
  darkToken,
  decodePng,
  drawIcon,
  iconColors,
  iconSvg,
  isInk,
  isOnIcon,
  lightToken,
  PNG_ICONS,
  rgb,
  SVG_ICON,
} from "../../../scripts/pwaIcons";
import { quotedManifest, WORKER_FILE, WORKER_MARK, workerManifest } from "../../../vite.config";
import { OfflineNotice, useOnline } from "./OfflineNotice";
import { WORKER_SCOPE, WORKER_URL } from "./register";

const FRONTEND = resolve(import.meta.dirname, "..", "..", "..");
const PUBLIC = resolve(FRONTEND, "public");
const read = (...path: string[]) => readFileSync(resolve(FRONTEND, ...path), "utf8");

afterEach(cleanup);

type Manifest = {
  id: string;
  name: string;
  short_name: string;
  lang: string;
  start_url: string;
  scope: string;
  display: string;
  background_color: string;
  theme_color: string;
  icons: { src: string; sizes: string; type: string; purpose: string }[];
};

describe("the panel's manifest", () => {
  const manifest = JSON.parse(read("public", "panel", "manifest.webmanifest")) as Manifest;

  it("names the panel, opens it standalone, and reaches nothing above it", () => {
    expect(manifest.name).toBe("Qarz Daftari");
    expect(manifest.short_name.length).toBeLessThanOrEqual(12);
    expect(manifest.display).toBe("standalone");
    expect([manifest.id, manifest.start_url, manifest.scope]).toEqual(["/panel/", "/panel/", "/panel/"]);
    expect(manifest.scope).toBe(WORKER_SCOPE);
    expect(WORKER_URL.startsWith(manifest.scope)).toBe(true);
  });

  it("takes its colors from the tokens: the page's ground in the light theme", () => {
    expect(manifest.background_color).toBe(lightToken("--qd-bg"));
    expect(manifest.theme_color).toBe(lightToken("--qd-bg"));
  });

  it("lists icons that are files of the repository, of the size and kind it says", () => {
    expect(manifest.icons.map((icon) => icon.src).sort()).toEqual(
      [...PNG_ICONS.map((icon) => `/panel/icons/${icon.file}`), `/panel/icons/${SVG_ICON}`].sort(),
    );
    for (const icon of manifest.icons) {
      expect(icon.src.startsWith(manifest.scope), icon.src).toBe(true);
      expect(existsSync(resolve(PUBLIC, icon.src.slice(1))), icon.src).toBe(true);
    }
    for (const icon of PNG_ICONS) {
      const listed = manifest.icons.find((candidate) => candidate.src.endsWith(`/${icon.file}`));
      expect(listed).toMatchObject({
        sizes: `${icon.size}x${icon.size}`,
        type: "image/png",
        purpose: icon.maskable ? "maskable" : "any",
      });
    }
    // What a browser needs before it offers to install: a 192 and a 512 pixel icon.
    expect(manifest.icons.map((icon) => icon.sizes)).toEqual(expect.arrayContaining(["192x192", "512x512"]));
  });

  it("is the only thing under public/ but its icons: nothing else is served that was not built", () => {
    expect(readdirSync(PUBLIC)).toEqual(["panel"]);
    expect(readdirSync(resolve(PUBLIC, "panel")).sort()).toEqual(["icons", "manifest.webmanifest"]);
    expect(readdirSync(resolve(PUBLIC, "panel", "icons")).sort()).toEqual(
      [...PNG_ICONS.map((icon) => icon.file), SVG_ICON].sort(),
    );
  });
});

describe("the panel's icons", () => {
  const { ground, ink } = iconColors();

  it("are the product's initials in the color of text on the accent, on the accent", () => {
    expect([ground, ink]).toEqual([lightToken("--qd-accent"), lightToken("--qd-on-accent")]);
  });

  it.each(PNG_ICONS)("$file is exactly what the script draws", (icon) => {
    const found = decodePng(new Uint8Array(readFileSync(resolve(PUBLIC, "panel", "icons", icon.file))));
    const drawn = drawIcon(icon.size, icon.maskable, rgb(ground), rgb(ink));
    expect(found.size).toBe(icon.size);
    expect(Buffer.from(found.pixels).equals(Buffer.from(drawn))).toBe(true);
    // The ground is the accent, opaque, in the middle of the left edge; a letter is the other color.
    const at = (x: number, y: number) => [...found.pixels.subarray((y * icon.size + x) * 4, (y * icon.size + x) * 4 + 4)];
    expect(at(2, icon.size / 2)).toEqual([...rgb(ground), 255]);
    expect(at(Math.round(0.21 * icon.size), icon.size / 2)).toEqual([...rgb(ink), 255]);
    // A corner: cut away on the rounded icon, filled on the maskable one.
    expect(at(0, 0)[3]).toBe(icon.maskable ? 255 : 0);
  });

  it("the vector icon is what the script writes, and holds no script or outside address", () => {
    const svg = read("public", "panel", "icons", SVG_ICON);
    expect(svg).toBe(iconSvg(ground, ink));
    expect(svg).not.toMatch(/<script|href|https?:\/\/(?!www\.w3\.org\/2000\/svg)/);
  });

  it("keeps both letters inside the part a launcher never cuts away", () => {
    // A maskable icon is safe within a circle of 40% of its side around the middle.
    for (let y = 0; y < 1; y += 0.005) {
      for (let x = 0; x < 1; x += 0.005) {
        if (isInk(x, y)) {
          expect(Math.hypot(x - 0.5, y - 0.5)).toBeLessThanOrEqual(0.4);
          expect(isOnIcon(x, y, false)).toBe(true);
        }
      }
    }
    expect(isInk(0.5, 0.05)).toBe(false);
  });

  it("the check above would notice another picture", () => {
    const other = drawIcon(192, false, rgb(ink), rgb(ground));
    const committed = decodePng(new Uint8Array(readFileSync(resolve(PUBLIC, "panel", "icons", "icon-192.png"))));
    expect(Buffer.from(committed.pixels).equals(Buffer.from(other))).toBe(false);
    expect(() => decodePng(new TextEncoder().encode("not a picture"))).toThrow("not a PNG file");
  });
});

describe("the three pages: only the panel can be installed", () => {
  const panel = read("panel", "index.html");

  it("the panel names its manifest and icons, all under /panel/", () => {
    expect(panel).toContain('<link rel="manifest" href="/panel/manifest.webmanifest" />');
    expect(panel).toContain('<link rel="icon" type="image/svg+xml" href="/panel/icons/icon.svg" />');
    expect(panel).toContain('<link rel="apple-touch-icon" href="/panel/icons/icon-192.png" />');
  });

  it("colors the browser's bars with the page's ground of each theme", () => {
    expect(panel).toContain(`<meta name="theme-color" media="(prefers-color-scheme: light)" content="${lightToken("--qd-bg")}" />`);
    expect(panel).toContain(`<meta name="theme-color" media="(prefers-color-scheme: dark)" content="${darkToken("--qd-bg")}" />`);
    expect(lightToken("--qd-bg")).not.toBe(darkToken("--qd-bg"));
  });

  it.each(["app", "admin", "k"])("/%s/ has no manifest, and no code of it registers a worker", (entry) => {
    const html = read(entry, "index.html");
    expect(html).not.toMatch(/rel="manifest"|theme-color|apple-touch-icon/);
    const main = read("src", entry, entry === "k" ? "main.ts" : "main.tsx");
    expect(main).not.toMatch(/serviceWorker|pwa\//);
  });

  it("the panel is the only entry that registers the worker", () => {
    expect(read("src", "panel", "main.tsx")).toContain("registerPanelWorker({ container: navigator.serviceWorker, production: import.meta.env.PROD })");
    expect(WORKER_URL).toBe(`/${WORKER_FILE}`);
  });
});

describe("the worker's list for a build", () => {
  const html = [
    '<script type="module" crossorigin src="/assets/panel-CZMCdhnq.js"></script>',
    '<link rel="modulepreload" crossorigin href="/assets/workspace-2I24fcgw.js">',
    '<link rel="stylesheet" crossorigin href="/assets/workspace-C8A9U79g.css">',
    '<link rel="manifest" href="/panel/manifest.webmanifest" />',
    '<link rel="icon" href="/panel/icons/icon.svg" />',
    '<script src="https://telegram.org/js/telegram-widget.js"></script>',
  ].join("\n");

  it("is the panel's page and the built files that page names, and nothing else", () => {
    expect(workerManifest(html).shell).toEqual([
      "/panel/",
      "/assets/panel-CZMCdhnq.js",
      "/assets/workspace-2I24fcgw.js",
      "/assets/workspace-C8A9U79g.css",
    ]);
  });

  it("gets another mark when any file of the shell changes, and the same mark when none does", () => {
    const mark = workerManifest(html).build;
    expect(mark).toMatch(/^[0-9a-f]{16}$/);
    expect(workerManifest(html).build).toBe(mark);
    expect(workerManifest(html.replace("panel-CZMCdhnq.js", "panel-AAAAAAAA.js")).build).not.toBe(mark);
    expect(workerManifest(html.replace("workspace-C8A9U79g.css", "workspace-BBBBBBBB.css")).build).not.toBe(mark);
  });

  it("refuses a page that names no built file", () => {
    expect(() => workerManifest("<html></html>")).toThrow("names no built file");
  });

  it.each(['"', "'", "`"])("reads back the same between %s quotes, whichever the minifier chose", (quote) => {
    const manifest = { build: "0123456789abcdef", shell: ["/panel/", "/assets/a-b_c-12345678.js", "/assets/it's `${odd}\\.css"] };
    const script = `return JSON.parse(${quote}${quotedManifest(manifest)}${quote});`;
    expect(new Function(script)()).toEqual(manifest);
  });

  it("the worker's source holds the mark the build replaces, once", () => {
    expect(read("src", "panel", "pwa", "sw.ts").split(WORKER_MARK)).toHaveLength(2);
  });
});

describe("the notice that there is no connection", () => {
  function fakeConnection(initial: boolean) {
    let online = initial;
    const events = new EventTarget();
    return {
      connection: { read: () => online, events: events as unknown as Window },
      set(next: boolean) {
        online = next;
        events.dispatchEvent(new Event(next ? "online" : "offline"));
      },
    };
  }

  function Probe({ connection }: { connection: Parameters<typeof useOnline>[0] }) {
    return <OfflineNotice online={useOnline(connection)} />;
  }

  it("is shown while the device has no connection, and goes when it is back", () => {
    const network = fakeConnection(false);
    render(
      <I18nProvider initialLanguage="uz">
        <Probe connection={network.connection} />
      </I18nProvider>,
    );
    expect(screen.getByRole("alert").textContent).toContain("Internet aloqasi yo'q");
    expect(screen.getByRole("alert").textContent).toContain("Panel do'kon ma'lumotlarini qurilmada saqlamaydi");
    act(() => network.set(true));
    expect(screen.queryByRole("alert")).toBeNull();
    act(() => network.set(false));
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("shows nothing while there is a connection", () => {
    render(
      <I18nProvider initialLanguage="uz">
        <Probe connection={fakeConnection(true).connection} />
      </I18nProvider>,
    );
    expect(document.body.textContent).toBe("");
  });

  it("says it in Russian", () => {
    render(
      <I18nProvider initialLanguage="ru">
        <OfflineNotice online={false} />
      </I18nProvider>,
    );
    expect(screen.getByRole("alert").textContent).toContain("Нет подключения к интернету");
  });
});
