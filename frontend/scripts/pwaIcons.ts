/**
 * The installed web panel's icons, drawn by this script: the product's initials, "QD", in the color of
 * text on the accent, on the accent color. Simple marks made of rectangles and ellipses, until the
 * product has a logo of its own. The colors are read from tokens.css, so the icons follow the tokens.
 *
 *   node scripts/pwaIcons.ts            writes public/panel/icons/*
 *   node scripts/pwaIcons.ts --check    fails when the committed files are not what this script draws
 *
 * No dependency: the PNG files are written here (8-bit RGBA, one unfiltered scan line after another,
 * compressed by Node's zlib).
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { deflateSync, inflateSync } from "node:zlib";

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
// The letters sit inside the middle 60% of the square, so a launcher that cuts the icon to a circle or
// a rounded square (a "maskable" icon keeps only the middle 80%) never cuts a letter.
const TOP = 0.33;
const BOTTOM = 0.67;
const MIDDLE = (TOP + BOTTOM) / 2;
const STROKE = 0.07;
const Q = { cx: 0.335, rx: 0.145, ry: (BOTTOM - TOP) / 2 };
const Q_TAIL = { x1: 0.375, y1: 0.585, x2: 0.49, y2: 0.69 };
const D = { left: 0.56, bend: 0.66, rx: 0.16, ry: (BOTTOM - TOP) / 2 };
/** How round the corners of the non-maskable icon are, as a part of its side. */
const CORNER = 0.22;

function inEllipse(x: number, y: number, cx: number, cy: number, rx: number, ry: number): boolean {
  return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1;
}

function nearSegment(x: number, y: number, s: typeof Q_TAIL, halfWidth: number): boolean {
  const [dx, dy] = [s.x2 - s.x1, s.y2 - s.y1];
  const along = Math.max(0, Math.min(1, ((x - s.x1) * dx + (y - s.y1) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(x - (s.x1 + along * dx), y - (s.y1 + along * dy)) <= halfWidth;
}

/** Whether the point is ink: part of the "Q" or of the "D". */
export function isInk(x: number, y: number): boolean {
  const ring =
    inEllipse(x, y, Q.cx, MIDDLE, Q.rx, Q.ry) && !inEllipse(x, y, Q.cx, MIDDLE, Q.rx - STROKE, Q.ry - STROKE);
  if (ring || nearSegment(x, y, Q_TAIL, STROKE / 2)) {
    return true;
  }
  const outer =
    (x >= D.left && x <= D.bend && y >= TOP && y <= BOTTOM) || (x >= D.bend && inEllipse(x, y, D.bend, MIDDLE, D.rx, D.ry));
  const inner =
    (x >= D.left + STROKE && x <= D.bend && y >= TOP + STROKE && y <= BOTTOM - STROKE) ||
    (x >= D.bend && inEllipse(x, y, D.bend, MIDDLE, D.rx - STROKE, D.ry - STROKE));
  return outer && !inner;
}

/** Whether the point is on the icon at all: everywhere for a maskable icon, a rounded square otherwise. */
export function isOnIcon(x: number, y: number, maskable: boolean): boolean {
  if (maskable) {
    return true;
  }
  const [nx, ny] = [Math.min(x, 1 - x), Math.min(y, 1 - y)];
  return nx >= CORNER || ny >= CORNER || Math.hypot(CORNER - nx, CORNER - ny) <= CORNER;
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

/** The same mark as a vector image, for a browser tab: the shapes of `isInk`, written as SVG. */
export function iconSvg(ground: string, ink: string): string {
  const n = (value: number) => String(Math.round(value * 1000) / 10);
  const ellipse = (cx: number, rx: number, ry: number) =>
    `M${n(cx - rx)} ${n(MIDDLE)}a${n(rx)} ${n(ry)} 0 1 0 ${n(2 * rx)} 0a${n(rx)} ${n(ry)} 0 1 0 ${n(-2 * rx)} 0z`;
  const half = (inset: number) =>
    `M${n(D.left + inset)} ${n(TOP + inset)}H${n(D.bend)}a${n(D.rx - inset)} ${n(D.ry - inset)} 0 0 1 0 ${n(2 * (D.ry - inset))}H${n(D.left + inset)}z`;
  return [
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">',
    `<rect width="100" height="100" rx="${n(CORNER)}" fill="${ground}"/>`,
    `<path fill="${ink}" fill-rule="evenodd" d="${ellipse(Q.cx, Q.rx, Q.ry)}${ellipse(Q.cx, Q.rx - STROKE, Q.ry - STROKE)}${half(0)}${half(STROKE)}"/>`,
    `<path stroke="${ink}" stroke-width="${n(STROKE)}" stroke-linecap="round" d="M${n(Q_TAIL.x1)} ${n(Q_TAIL.y1)}L${n(Q_TAIL.x2)} ${n(Q_TAIL.y2)}"/>`,
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

export function iconColors(): { ground: string; ink: string } {
  return { ground: lightToken("--qd-accent"), ink: lightToken("--qd-on-accent") };
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
  const svg = iconSvg(ground, ink);
  if (check) {
    if (readFileSync(resolve(ICON_DIR, SVG_ICON), "utf8") !== svg) {
      stale.push(SVG_ICON);
    }
  } else {
    writeFileSync(resolve(ICON_DIR, SVG_ICON), svg);
  }
  if (stale.length > 0) {
    console.error(`not what scripts/pwaIcons.ts draws: ${stale.join(", ")}. Run: node scripts/pwaIcons.ts`);
    process.exit(1);
  }
  console.log(check ? "the panel's icons are current" : `wrote ${PNG_ICONS.length + 1} icons to ${ICON_DIR}`);
}

if (import.meta.main) {
  main();
}
