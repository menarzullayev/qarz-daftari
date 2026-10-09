/**
 * The installed web panel's icons and manifest, written by this script from the brand's definition
 * (`src/shared/brand.ts`): the brand's mark in the color of text on the accent, on the accent color,
 * and the product's name in the manifest. The mark is a prototype, not a registered logo; a new one
 * replaces the geometry in the definition and this script is run again. The colors are read from
 * tokens.css, so the icons follow the tokens.
 *
 * The files are committed, not made by the build: `public/` is served as it is by the development
 * server and copied as it is by the build, a picture in a pull request can be looked at, and
 * `src/panel/pwa/pwaFiles.test.tsx` fails when a committed file is not what this script writes today.
 *
 *   node scripts/pwaIcons.ts            writes public/panel/icons/* and public/panel/manifest.webmanifest
 *   node scripts/pwaIcons.ts --check    fails when the committed files are not what this script writes
 *
 * No dependency: the PNG files are written here (8-bit RGBA, one unfiltered scan line after another,
 * compressed by Node's zlib).
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { deflateSync, inflateSync } from "node:zlib";

import { BRAND_MARK, BRAND_NAME, BRAND_SHORT_NAME, BRAND_TAGLINE, type MarkBlock } from "../src/shared/brand.ts";

const FRONTEND = resolve(import.meta.dirname, "..");
export const ICON_DIR = resolve(FRONTEND, "public", "panel", "icons");

export type Rgb = readonly [number, number, number];

/** A token's color in the light theme, the first `:root` block of tokens.css. */
export function lightToken(name: string, css = readFileSync(resolve(FRONTEND, "src", "shared", "tokens.css"), "utf8")): string {
  const light = /:root\s*\{([^}]*)\}/.exec(css)?.[1] ?? "";
  const value = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(light)?.[1];
  if (!value) {
    throw new Error(`tokens.css does not define ${name} as a six-digit color in its light theme`);
  }
  return value.toLowerCase();
}

/** The same token in the dark theme, the `:root[data-theme="dark"]` block. */
export function darkToken(name: string, css = readFileSync(resolve(FRONTEND, "src", "shared", "tokens.css"), "utf8")): string {
  const dark = /:root\[data-theme="dark"\]\s*\{([^}]*)\}/.exec(css)?.[1] ?? "";
  const value = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(dark)?.[1];
  if (!value) {
    throw new Error(`tokens.css does not define ${name} as a six-digit color in its dark theme`);
  }
  return value.toLowerCase();
}

export function rgb(hex: string): Rgb {
  return [Number.parseInt(hex.slice(1, 3), 16), Number.parseInt(hex.slice(3, 5), 16), Number.parseInt(hex.slice(5, 7), 16)];
}

// --- the mark, in a square of side 1 ------------------------------------------------------------------
// The mark is the brand's (`BRAND_MARK`, from the one definition): blocks with round corners on a square
// ground. Its blocks stay inside the middle 80% of the square (the definition is refused otherwise), so
// a launcher that cuts a "maskable" icon to a circle or a rounded square never cuts the mark.
const UNIT = 1 / BRAND_MARK.canvas;
/** How round the corners of the non-maskable icon are, as a part of its side. */
const CORNER = BRAND_MARK.corner * UNIT;

/** Whether the point is inside a rectangle with round corners. */
function inRounded(x: number, y: number, left: number, top: number, width: number, height: number, radius: number): boolean {
  if (x < left || x > left + width || y < top || y > top + height) {
    return false;
  }
  const [nx, ny] = [Math.min(x - left, left + width - x), Math.min(y - top, top + height - y)];
  return nx >= radius || ny >= radius || Math.hypot(radius - nx, radius - ny) <= radius;
}

/** Whether the point is ink: inside one of the mark's blocks. */
export function isInk(x: number, y: number, blocks: readonly MarkBlock[] = BRAND_MARK.blocks): boolean {
  return blocks.some((block) => inRounded(x, y, block.x * UNIT, block.y * UNIT, block.w * UNIT, block.h * UNIT, block.r * UNIT));
}

/** Whether the point is on the icon at all: everywhere for a maskable icon, a rounded square otherwise. */
export function isOnIcon(x: number, y: number, maskable: boolean): boolean {
  return maskable || inRounded(x, y, 0, 0, 1, 1, CORNER);
}

const SAMPLES = 4;

/** The icon as RGBA pixels, each one the average of a 4 by 4 grid of points inside it. */
export function drawIcon(size: number, maskable: boolean, ground: Rgb, ink: Rgb): Uint8Array {
  const pixels = new Uint8Array(size * size * 4);
  for (let row = 0; row < size; row += 1) {
    for (let column = 0; column < size; column += 1) {
      let [covered, inked] = [0, 0];
      for (let sy = 0; sy < SAMPLES; sy += 1) {
        for (let sx = 0; sx < SAMPLES; sx += 1) {
          const x = (column + (sx + 0.5) / SAMPLES) / size;
          const y = (row + (sy + 0.5) / SAMPLES) / size;
          if (isOnIcon(x, y, maskable)) {
            covered += 1;
            inked += isInk(x, y) ? 1 : 0;
          }
        }
      }
      const at = (row * size + column) * 4;
      const share = covered === 0 ? 0 : inked / covered;
      for (let channel = 0; channel < 3; channel += 1) {
        pixels[at + channel] = Math.round((ground[channel] ?? 0) * (1 - share) + (ink[channel] ?? 0) * share);
      }
      pixels[at + 3] = Math.round((covered / (SAMPLES * SAMPLES)) * 255);
    }
  }
  return pixels;
}

// --- PNG ----------------------------------------------------------------------------------------------

const SIGNATURE = Uint8Array.of(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a);

const CRC_TABLE = Uint32Array.from({ length: 256 }, (_unused, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit += 1) {
    value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  }
  return value >>> 0;
});

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc = (CRC_TABLE[(crc ^ byte) & 0xff] ?? 0) ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type: string, data: Uint8Array): Uint8Array {
  const body = new Uint8Array(4 + data.length);
  body.set(new TextEncoder().encode(type));
  body.set(data, 4);
  const out = new Uint8Array(12 + data.length);
  const view = new DataView(out.buffer);
  view.setUint32(0, data.length);
  out.set(body, 4);
  view.setUint32(8 + data.length, crc32(body));
  return out;
}

export function encodePng(size: number, pixels: Uint8Array): Uint8Array {
  const header = new Uint8Array(13);
  const view = new DataView(header.buffer);
  view.setUint32(0, size);
  view.setUint32(4, size);
  header.set([8, 6, 0, 0, 0], 8); // 8 bits a channel, RGBA, no interlace
  const lines = new Uint8Array(size * (size * 4 + 1));
  for (let row = 0; row < size; row += 1) {
    lines.set(pixels.subarray(row * size * 4, (row + 1) * size * 4), row * (size * 4 + 1) + 1);
  }
  const parts = [SIGNATURE, chunk("IHDR", header), chunk("IDAT", deflateSync(lines, { level: 9 })), chunk("IEND", new Uint8Array(0))];
  const png = new Uint8Array(parts.reduce((total, part) => total + part.length, 0));
  let at = 0;
  for (const part of parts) {
    png.set(part, at);
    at += part.length;
  }
  return png;
}

/** Side and RGBA pixels of a PNG this script wrote. Throws for anything else. */
export function decodePng(png: Uint8Array): { size: number; pixels: Uint8Array } {
  if (!SIGNATURE.every((byte, index) => png[index] === byte)) {
    throw new Error("not a PNG file");
  }
  const view = new DataView(png.buffer, png.byteOffset, png.byteLength);
  let at = SIGNATURE.length;
  let size = 0;
  const data: Uint8Array[] = [];
  while (at < png.length) {
    const length = view.getUint32(at);
    const type = new TextDecoder().decode(png.subarray(at + 4, at + 8));
    const body = png.subarray(at + 8, at + 8 + length);
    if (type === "IHDR") {
      size = view.getUint32(at + 8);
      if (view.getUint32(at + 12) !== size || body[8] !== 8 || body[9] !== 6) {
        throw new Error("not a square 8-bit RGBA image");
      }
    } else if (type === "IDAT") {
      data.push(body);
    }
    at += 12 + length;
  }
  const lines = inflateSync(Buffer.concat(data));
  const pixels = new Uint8Array(size * size * 4);
  for (let row = 0; row < size; row += 1) {
    const start = row * (size * 4 + 1);
    if (lines[start] !== 0) {
      throw new Error("a filtered scan line: not written by this script");
    }
    pixels.set(lines.subarray(start + 1, start + 1 + size * 4), row * size * 4);
  }
  return { size, pixels };
}

// --- the files ------------------------------------------------------------------------------------------

/** The same mark as a vector image, for a browser tab: the ground and the blocks, as they are defined. */
export function iconSvg(ground: string, ink: string): string {
  const { canvas, corner, blocks } = BRAND_MARK;
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${canvas} ${canvas}">`,
    `<rect width="${canvas}" height="${canvas}" rx="${corner}" fill="${ground}"/>`,
    ...blocks.map((block) => `<rect x="${block.x}" y="${block.y}" width="${block.w}" height="${block.h}" rx="${block.r}" fill="${ink}"/>`),
    "</svg>",
    "",
  ].join("\n");
}

export const PNG_ICONS = [
  { file: "icon-192.png", size: 192, maskable: false },
  { file: "icon-512.png", size: 512, maskable: false },
  { file: "icon-maskable-512.png", size: 512, maskable: true },
] as const;
export const SVG_ICON = "icon.svg";
export const MANIFEST = resolve(FRONTEND, "public", "panel", "manifest.webmanifest");

export function iconColors(): { ground: string; ink: string } {
  return { ground: lightToken("--qd-accent"), ink: lightToken("--qd-on-accent") };
}

/**
 * The installed panel's manifest: its name and short name are the brand's, its colors the tokens', its
 * icons the files above. Written by this script like the icons, so that the committed file is never
 * typed and a changed name cannot leave it behind.
 */
export function manifestText(): string {
  const ground = lightToken("--qd-bg");
  const icons = [
    ...PNG_ICONS.map((icon) => ({ src: `/panel/icons/${icon.file}`, sizes: `${icon.size}x${icon.size}`, type: "image/png", purpose: icon.maskable ? "maskable" : "any" })),
    { src: `/panel/icons/${SVG_ICON}`, sizes: "any", type: "image/svg+xml", purpose: "any" },
  ];
  const fields: [string, unknown][] = [
    ["id", "/panel/"],
    ["name", BRAND_NAME],
    ["short_name", BRAND_SHORT_NAME],
    ["description", `${BRAND_NAME} — ${BRAND_TAGLINE}.`],
    ["lang", "uz"],
    ["dir", "ltr"],
    ["start_url", "/panel/"],
    ["scope", "/panel/"],
    ["display", "standalone"],
    ["background_color", ground],
    ["theme_color", ground],
  ];
  const pairs = (entry: object) => Object.entries(entry).map(([key, value]) => `${JSON.stringify(key)}: ${JSON.stringify(value)}`).join(", ");
  return [
    "{",
    ...fields.map(([key, value]) => `  ${JSON.stringify(key)}: ${JSON.stringify(value)},`),
    '  "icons": [',
    icons.map((icon) => `    { ${pairs(icon)} }`).join(",\n"),
    "  ]",
    "}",
    "",
  ].join("\n");
}

function main(): void {
  const check = process.argv.includes("--check");
  const { ground, ink } = iconColors();
  const stale: string[] = [];
  if (!check) {
    mkdirSync(ICON_DIR, { recursive: true });
  }
  for (const icon of PNG_ICONS) {
    const pixels = drawIcon(icon.size, icon.maskable, rgb(ground), rgb(ink));
    const path = resolve(ICON_DIR, icon.file);
    if (check) {
      // Compared by pixels: the compressed bytes may differ from one zlib to the next.
      const found = decodePng(new Uint8Array(readFileSync(path)));
      if (found.size !== icon.size || !found.pixels.every((value, index) => value === pixels[index])) {
        stale.push(icon.file);
      }
    } else {
      writeFileSync(path, encodePng(icon.size, pixels));
    }
  }
  const texts: [string, string, string][] = [
    [resolve(ICON_DIR, SVG_ICON), SVG_ICON, iconSvg(ground, ink)],
    [MANIFEST, "manifest.webmanifest", manifestText()],
  ];
  for (const [path, name, text] of texts) {
    if (!check) {
      writeFileSync(path, text);
    } else if (readFileSync(path, "utf8") !== text) {
      stale.push(name);
    }
  }
  if (stale.length > 0) {
    console.error(`not what scripts/pwaIcons.ts writes: ${stale.join(", ")}. Run: node scripts/pwaIcons.ts`);
    process.exit(1);
  }
  console.log(check ? "the panel's icons and manifest are current" : `wrote ${PNG_ICONS.length + 1} icons and the manifest under ${resolve(FRONTEND, "public", "panel")}`);
}

if (import.meta.main) {
  main();
}
