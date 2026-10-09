import { describe, expect, it } from "vitest";

import {
  cameraSupport,
  gs1CheckDigit,
  isRetailCode,
  LABEL_FORMATS,
  readBarcode,
  SCAN_MAX_GAP_MS,
  ScanBuffer,
  type ScanHost,
  searchIntent,
  stopStream,
} from "./barcode";

/** Feeds a text key by key, `gap` milliseconds apart, then Enter after `enterGap`; answers what Enter gave. */
function type(buffer: ScanBuffer, text: string, gap: number, start = 1000, enterGap = gap): string | null {
  let at = start;
  for (const key of text) {
    expect(buffer.feed(key, at)).toBeNull();
    at += gap;
  }
  return buffer.feed("Enter", at - gap + enterGap);
}

describe("a barcode as the server stores it", () => {
  it("computes the GS1 check digit", () => {
    expect(gs1CheckDigit("400638133393")).toBe(1); // EAN-13 4006381333931
    expect(gs1CheckDigit("7351353")).toBe(7); // EAN-8 73513537
    expect(gs1CheckDigit("03600029145")).toBe(2); // UPC-A 036000291452
  });

  it("takes an EAN-13, an EAN-8 and a UPC-A with a right check digit, trimmed", () => {
    expect(readBarcode(" 4006381333931 ")).toEqual({ ok: true, code: "4006381333931" });
    expect(readBarcode("73513537")).toEqual({ ok: true, code: "73513537" });
    expect(readBarcode("036000291452")).toEqual({ ok: true, code: "036000291452" });
  });

  it("refuses a retail code with a wrong check digit instead of looking nothing up", () => {
    expect(readBarcode("4006381333932")).toEqual({ ok: false, problem: "check_digit" });
    expect(readBarcode("73513538")).toEqual({ ok: false, problem: "check_digit" });
  });

  it("takes any other printable ASCII as the text of a Code 128 label", () => {
    expect(readBarcode("ABC-123/x")).toEqual({ ok: true, code: "ABC-123/x" });
    expect(readBarcode("12345")).toEqual({ ok: true, code: "12345" });
    expect(readBarcode("A B")).toEqual({ ok: true, code: "A B" });
  });

  it("refuses nothing, a code that is too long, and letters outside ASCII", () => {
    expect(readBarcode("   ")).toEqual({ ok: false, problem: "empty" });
    expect(readBarcode("1".repeat(49))).toEqual({ ok: false, problem: "too_long" });
    expect(readBarcode("шоколад")).toEqual({ ok: false, problem: "characters" });
    expect(readBarcode("o'zbek choy")).toEqual({ ok: false, problem: "characters" });
  });
});

describe("a scanner that acts as a keyboard", () => {
  it("is recognised: characters a few milliseconds apart, ended by Enter", () => {
    expect(type(new ScanBuffer(), "4006381333931", 8)).toBe("4006381333931");
    expect(type(new ScanBuffer(), "ABC-1", SCAN_MAX_GAP_MS)).toBe("ABC-1");
  });

  it("is not a person typing: the same digits a fifth of a second apart are no scan", () => {
    expect(type(new ScanBuffer(), "4006381333931", 200)).toBeNull();
    expect(type(new ScanBuffer(), "4006381333931", SCAN_MAX_GAP_MS + 1)).toBeNull();
  });

  it("is not two or three fast keys: a code is at least four characters", () => {
    expect(type(new ScanBuffer(), "123", 5)).toBeNull();
    expect(type(new ScanBuffer(), "1234", 5)).toBe("1234");
  });

  it("does not take an Enter that comes long after the last character", () => {
    expect(type(new ScanBuffer(), "4006381333931", 8, 1000, 900)).toBeNull();
  });

  it("drops what was typed slowly before the scan, and keys that are not characters", () => {
    const buffer = new ScanBuffer();
    buffer.feed("s", 0);
    buffer.feed("u", 300);
    buffer.feed("t", 600);
    buffer.feed("Shift", 5000);
    expect(type(buffer, "73513537", 6, 5001)).toBe("73513537");
  });

  it("starts again after each Enter", () => {
    const buffer = new ScanBuffer();
    expect(type(buffer, "73513537", 6)).toBe("73513537");
    expect(buffer.feed("Enter", 9000)).toBeNull();
    expect(type(buffer, "036000291452", 6, 20000)).toBe("036000291452");
  });
});

describe("what Enter in a search box means", () => {
  it("is a code when it was scanned, whatever it looks like", () => {
    expect(searchIntent("ABC-1", true)).toBe("code");
  });

  it("is a code when a retail code with a right check digit was typed", () => {
    expect(isRetailCode("4006381333931")).toBe(true);
    expect(searchIntent("4006381333931", false)).toBe("code");
  });

  it("is words for a name, a part of one, and digits that are no retail code", () => {
    expect(searchIntent("shakar", false)).toBe("words");
    expect(searchIntent("123", false)).toBe("words");
    expect(searchIntent("4006381333932", false)).toBe("words");
    expect(isRetailCode("abc")).toBe(false);
  });
});

function detector(formats: string[] | Error) {
  return class {
    static async getSupportedFormats() {
      if (formats instanceof Error) {
        throw formats;
      }
      return formats;
    }
    readonly asked: string[];
    constructor(options?: { formats?: string[] }) {
      this.asked = options?.formats ?? [];
    }
    async detect() {
      return [];
    }
  };
}

const camera = { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } };

describe("whether this browser can read a label with its camera", () => {
  it("can, where the detector reads label formats and there is a camera API: only those formats are asked for", async () => {
    const host: ScanHost = { BarcodeDetector: detector(["qr_code", "ean_13", "code_128"]), navigator: camera };
    const found = await cameraSupport(host);
    expect(found.ok).toBe(true);
    if (found.ok) {
      expect(found.formats).toEqual(["ean_13", "code_128"]);
      expect((found.create() as unknown as { asked: string[] }).asked).toEqual(["ean_13", "code_128"]);
    }
  });

  it("cannot without BarcodeDetector (Firefox, Safari): the reason is named", async () => {
    expect(await cameraSupport({ navigator: camera })).toEqual({ ok: false, why: "detector" });
    expect(await cameraSupport({ BarcodeDetector: undefined, navigator: camera })).toEqual({ ok: false, why: "detector" });
    expect(await cameraSupport(undefined)).toEqual({ ok: false, why: "detector" });
  });

  it("cannot when the detector reads QR codes only, or cannot say what it reads", async () => {
    expect(await cameraSupport({ BarcodeDetector: detector(["qr_code"]), navigator: camera })).toEqual({ ok: false, why: "formats" });
    expect(await cameraSupport({ BarcodeDetector: detector(new Error("no")), navigator: camera })).toEqual({ ok: false, why: "formats" });
  });

  it("cannot without the camera API, as on a page that is not secure", async () => {
    expect(await cameraSupport({ BarcodeDetector: detector([...LABEL_FORMATS]) })).toEqual({ ok: false, why: "camera" });
    expect(await cameraSupport({ BarcodeDetector: detector([...LABEL_FORMATS]), navigator: {} })).toEqual({ ok: false, why: "camera" });
  });

  it("asks for the four formats of goods labels and no other", () => {
    expect(LABEL_FORMATS).toEqual(["ean_13", "ean_8", "upc_a", "code_128"]);
  });

  it("stops every track of a stream, and nothing for no stream", () => {
    const stopped: number[] = [];
    stopStream({ getTracks: () => [{ stop: () => stopped.push(1) }, { stop: () => stopped.push(2) }] });
    expect(stopped).toEqual([1, 2]);
    expect(() => stopStream(null)).not.toThrow();
  });
});
