import { correction, generate } from "lean-qr";

/** White modules around the code that a scanner needs to find it. */
export const QUIET_ZONE = 4;

export type QrDrawing = {
  /** Side of the square in modules, quiet zone included: the SVG view box. */
  side: number;
  /** One closed sub-path per horizontal run of dark modules. */
  path: string;
};

/**
 * A QR code for `text`, drawn in the browser: nothing is sent anywhere, which matters because the text
 * is a link that connects a customer. This module is loaded on demand, so the encoder is not part of
 * the first load.
 */
export function drawQr(text: string): QrDrawing {
  const code = generate(text, { minCorrectionLevel: correction.M });
  let path = "";
  for (let y = 0; y < code.size; y += 1) {
    let x = 0;
    while (x < code.size) {
      if (!code.get(x, y)) {
        x += 1;
        continue;
      }
      const from = x;
      while (x < code.size && code.get(x, y)) {
        x += 1;
      }
      path += `M${from + QUIET_ZONE} ${y + QUIET_ZONE}h${x - from}v1h-${x - from}z`;
    }
  }
  return { side: code.size + 2 * QUIET_ZONE, path };
}
