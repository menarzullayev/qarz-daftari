/**
 * Paints a drawing made by `drawQr` (or the `d` of the SVG path it became) into pixels, so that a test
 * can read the code the way a phone's camera would.
 */
export function paintQr(
  drawing: { side: number; path: string },
  scale: number,
): { data: Uint8ClampedArray; width: number; height: number } {
  const size = drawing.side * scale;
  const data = new Uint8ClampedArray(size * size * 4).fill(255);
  for (const run of drawing.path.matchAll(/M(\d+) (\d+)h(\d+)v1h-\d+z/g)) {
    const [x, y, width] = [Number(run[1]), Number(run[2]), Number(run[3])];
    for (let row = y * scale; row < (y + 1) * scale; row += 1) {
      for (let column = x * scale; column < (x + width) * scale; column += 1) {
        data.fill(0, (row * size + column) * 4, (row * size + column) * 4 + 3);
      }
    }
  }
  return { data, width: size, height: size };
}
