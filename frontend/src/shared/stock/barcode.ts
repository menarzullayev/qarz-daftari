/**
 * Barcodes at the counter: what a code is, how a scanner that acts as a keyboard is told from a person
 * typing, and whether this browser can read a code with its camera.
 *
 * Three ways a code arrives, in the order they are reliable:
 *  1. A USB or Bluetooth scanner in keyboard mode types the digits within a few milliseconds of each
 *     other and ends with Enter. It needs nothing of the browser and works wherever a key press does.
 *  2. A person types the digits and presses Enter or the button. Always possible.
 *  3. The camera, through the browser's `BarcodeDetector` (the Shape Detection API). It exists in
 *     Chromium on Android and macOS/ChromeOS and is absent in Firefox and Safari, so it is detected,
 *     never assumed; where it is absent the screen says so and the first two remain.
 *
 * Telegram's own `showScanQrPopup` reads QR codes only (core.telegram.org/bots/webapps: "a native popup
 * for scanning a QR code"); it is not a barcode scanner and is not used here.
 */

/** The longest code the server keeps (backend/src/qarz/domain/stock.py, `MAX_BARCODE_LENGTH`). */
export const MAX_BARCODE_LENGTH = 48;
export const MAX_BARCODES = 10;

/** The check digit of a GS1 code (EAN-8, UPC-A, EAN-13) whose digits without it are `body`. */
export function gs1CheckDigit(body: string): number {
  let total = 0;
  [...body].reverse().forEach((digit, position) => {
    total += Number(digit) * (position % 2 === 0 ? 3 : 1);
  });
  return (10 - (total % 10)) % 10;
}

const DIGITS = /^\d+$/;
const CHECKED_LENGTHS: ReadonlySet<number> = new Set([8, 12, 13]);
/** Printable ASCII with no space at either end: the text of a Code 128 label. */
const CODE128 = /^[\x21-\x7e](?:[\x20-\x7e]*[\x21-\x7e])?$/;

export type BarcodeProblem = "empty" | "too_long" | "check_digit" | "characters";
export type BarcodeResult = { ok: true; code: string } | { ok: false; problem: BarcodeProblem };

/**
 * The code as the server stores it, or why it cannot be one. Eight, twelve or thirteen digits must
 * carry a right check digit, so a mistyped digit is caught here instead of finding nothing.
 */
export function readBarcode(raw: string): BarcodeResult {
  const code = raw.trim();
  if (code === "") {
    return { ok: false, problem: "empty" };
  }
  if (code.length > MAX_BARCODE_LENGTH) {
    return { ok: false, problem: "too_long" };
  }
  if (DIGITS.test(code) && CHECKED_LENGTHS.has(code.length)) {
    return gs1CheckDigit(code.slice(0, -1)) === Number(code.slice(-1))
      ? { ok: true, code }
      : { ok: false, problem: "check_digit" };
  }
  return CODE128.test(code) ? { ok: true, code } : { ok: false, problem: "characters" };
}

/** Whether a text is a retail code with a right check digit: what a search box takes for a barcode. */
export function isRetailCode(text: string): boolean {
  const code = text.trim();
  return DIGITS.test(code) && CHECKED_LENGTHS.has(code.length) && readBarcode(code).ok;
}

// --- a scanner that acts as a keyboard ---------------------------------------------------------------

/** A scanner sends its keys a few milliseconds apart; a fast typist needs well over this between two. */
export const SCAN_MAX_GAP_MS = 50;
/** Shorter than any code a shop uses, so two quick key presses are never taken for a scan. */
export const SCAN_MIN_LENGTH = 4;

/**
 * Collects key presses and says, at Enter, whether they were a scan: at least four characters, each
 * within 50 ms of the one before. A slower key starts the code again, so what a person typed before
 * the scanner fired is not part of it.
 *
 * `feed` takes the `key` of a keydown event and the time it happened. It answers the scanned code at
 * the Enter that ends one, and null for every other key.
 */
export class ScanBuffer {
  private text = "";
  private last = 0;

  feed(key: string, at: number): string | null {
    if (key === "Enter") {
      const code = this.text;
      const scanned = code.length >= SCAN_MIN_LENGTH && at - this.last <= SCAN_MAX_GAP_MS;
      this.reset();
      return scanned ? code : null;
    }
    // Shift, Tab, arrows and the like have names longer than one character and are not part of a code.
    if (key.length !== 1) {
      return null;
    }
    if (this.text !== "" && at - this.last > SCAN_MAX_GAP_MS) {
      this.text = "";
    }
    this.text += key;
    this.last = at;
    return null;
  }

  reset(): void {
    this.text = "";
    this.last = 0;
  }
}

/**
 * What Enter in a search box means: a barcode to look up, or words to search by. A scan is a code
 * whatever it looks like; typed text is one only when it is a retail code with a right check digit, so
 * a name or a part of one is never sent to the lookup.
 */
export function searchIntent(text: string, scanned: boolean): "code" | "words" {
  return scanned || isRetailCode(text) ? "code" : "words";
}

// --- the camera ------------------------------------------------------------------------------------------

/** The formats of goods labels: the three retail codes and Code 128. */
export const LABEL_FORMATS: readonly string[] = ["ean_13", "ean_8", "upc_a", "code_128"];

export type DetectedCode = { rawValue: string; format?: string };
export type Detector = { detect: (source: unknown) => Promise<DetectedCode[]> };
type DetectorClass = {
  new (options?: { formats?: string[] }): Detector;
  getSupportedFormats?: () => Promise<string[]>;
};

/** What the page needs of the browser; a test passes its own. */
export type ScanHost = {
  BarcodeDetector?: unknown;
  navigator?: { mediaDevices?: { getUserMedia?: unknown } | undefined } | undefined;
};

export type CameraSupport =
  | { ok: true; formats: string[]; create: () => Detector }
  /** `detector`: the browser has no BarcodeDetector. `camera`: no camera API (or not a secure page).
   *  `formats`: the detector reads none of the formats of goods labels. */
  | { ok: false; why: "detector" | "camera" | "formats" };

/**
 * Whether this browser can read a goods label with its camera, found out and never assumed: the
 * detector must exist, must say it reads at least one of the label formats, and the camera API must be
 * there. Asking does not open the camera and shows no permission prompt.
 */
export async function cameraSupport(host: ScanHost | undefined = currentHost()): Promise<CameraSupport> {
  if (!host || !("BarcodeDetector" in host) || typeof host.BarcodeDetector !== "function") {
    return { ok: false, why: "detector" };
  }
  if (typeof host.navigator?.mediaDevices?.getUserMedia !== "function") {
    return { ok: false, why: "camera" };
  }
  const Detector = host.BarcodeDetector as DetectorClass;
  let supported: string[];
  try {
    supported = (await Detector.getSupportedFormats?.()) ?? [];
  } catch {
    supported = [];
  }
  const formats = LABEL_FORMATS.filter((format) => supported.includes(format));
  if (formats.length === 0) {
    return { ok: false, why: "formats" };
  }
  return { ok: true, formats, create: () => new Detector({ formats }) };
}

function currentHost(): ScanHost | undefined {
  return typeof window === "undefined" ? undefined : (window as unknown as ScanHost);
}

/** Ends a camera stream: every track is stopped, so the camera's light goes out. */
export function stopStream(stream: { getTracks: () => { stop: () => void }[] } | null): void {
  for (const track of stream?.getTracks() ?? []) {
    track.stop();
  }
}
