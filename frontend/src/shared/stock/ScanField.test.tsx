// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../i18n/I18nProvider";
import type { ScanHost } from "./barcode";
import { ScanField } from "./ScanField";
import { EAN } from "./testing";

afterEach(cleanup);

const LABEL = "Shtrix-kodni skanerlang yoki nom yozing";

/** A browser without BarcodeDetector, as Firefox and Safari are. */
const NO_DETECTOR: ScanHost = { navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } } };

function withCamera(found: string[], formats = ["ean_13", "ean_8", "upc_a", "code_128", "qr_code"]) {
  const stopped = vi.fn();
  const asked: unknown[] = [];
  let detects = 0;
  class BarcodeDetector {
    static getSupportedFormats = async () => formats;
    async detect() {
      detects += 1;
      const rawValue = found.shift();
      return rawValue === undefined ? [] : [{ rawValue }];
    }
  }
  const host: ScanHost = {
    BarcodeDetector,
    navigator: {
      mediaDevices: {
        getUserMedia: async (constraints: unknown) => {
          asked.push(constraints);
          return { getTracks: () => [{ stop: stopped }] };
        },
      },
    },
  };
  return { host, stopped, asked, detects: () => detects };
}

function setup(options: { host?: ScanHost; words?: boolean } = {}) {
  const onCode = vi.fn();
  const onWords = vi.fn();
  let clock = 0;
  const tick = (ms: number) => {
    clock += ms;
  };
  render(
    <I18nProvider initialLanguage="uz">
      <ScanField
        id="scan"
        label={LABEL}
        host={options.host ?? NO_DETECTOR}
        now={() => clock}
        onCode={onCode}
        onWords={options.words === false ? undefined : onWords}
      />
    </I18nProvider>,
  );
  const input = screen.getByLabelText(LABEL) as HTMLInputElement;
  /** Types into the field key by key, `gap` ms apart, and presses Enter, as a scanner or a person does. */
  const typeAndEnter = (text: string, gap: number) => {
    let value = input.value;
    for (const key of text) {
      tick(gap);
      fireEvent.keyDown(input, { key });
      value += key;
      fireEvent.change(input, { target: { value } });
    }
    tick(gap);
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.submit(input.closest("form") as HTMLFormElement);
  };
  return { onCode, onWords, input, typeAndEnter, tick };
}

describe("a scanner that acts as a keyboard", () => {
  it("gives its code at Enter, whatever the code looks like, and leaves the field empty for the next", () => {
    const { onCode, onWords, typeAndEnter, input } = setup();
    typeAndEnter("ABC-17", 8);
    expect(onCode).toHaveBeenCalledWith("ABC-17");
    expect(onWords).not.toHaveBeenCalled();
    expect(input.value).toBe("");
    typeAndEnter(EAN, 8);
    expect(onCode).toHaveBeenLastCalledWith(EAN);
  });

  it("is taken anywhere on the page while no field has the focus", () => {
    const { onCode, tick } = setup();
    for (const key of EAN) {
      tick(6);
      fireEvent.keyDown(document.body, { key });
    }
    tick(6);
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(onCode).toHaveBeenCalledWith(EAN);
  });

  it("is not taken from keys typed into another field of the page", () => {
    const { onCode, tick } = setup();
    const other = document.createElement("input");
    document.body.append(other);
    for (const key of [...EAN, "Enter"]) {
      tick(6);
      fireEvent.keyDown(other, { key });
    }
    expect(onCode).not.toHaveBeenCalled();
    other.remove();
  });

  it("is not a person typing slowly outside a field: nothing is taken", () => {
    const { onCode, tick } = setup();
    for (const key of [...EAN, "Enter"]) {
      tick(300);
      fireEvent.keyDown(document.body, { key });
    }
    expect(onCode).not.toHaveBeenCalled();
  });
});

describe("typing by hand", () => {
  it("looks a retail code up, and searches by anything else", () => {
    const { onCode, onWords, typeAndEnter } = setup();
    typeAndEnter(EAN, 250);
    expect(onCode).toHaveBeenCalledWith(EAN);
    typeAndEnter("shakar", 250);
    expect(onWords).toHaveBeenCalledWith("shakar");
    expect(onCode).toHaveBeenCalledTimes(1);
  });

  it("refuses a code whose check digit is wrong, in words, and looks nothing up", () => {
    const { onCode, onWords, typeAndEnter } = setup({ words: false });
    typeAndEnter("4006381333932", 250);
    expect(onCode).not.toHaveBeenCalled();
    expect(onWords).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe("Shtrix-kodning nazorat raqami to'g'ri kelmadi. Raqamlarni tekshiring.");
    expect(screen.getByLabelText(LABEL).getAttribute("aria-invalid")).toBe("true");
  });

  it("takes any typed code where the field is for codes only, and says so when it is empty", () => {
    const { onCode, typeAndEnter, input } = setup({ words: false });
    typeAndEnter("12345", 250);
    expect(onCode).toHaveBeenCalledWith("12345");
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    expect(screen.getByRole("alert").textContent).toBe("Shtrix-kodni yozing yoki skanerlang.");
    expect(onCode).toHaveBeenCalledTimes(1);
  });
});

describe("the camera", () => {
  it("is not offered where the browser has no BarcodeDetector, and a line says what remains", async () => {
    setup();
    expect(
      await screen.findByText(
        "Bu brauzer kamera orqali shtrix-kodni o'qiy olmaydi. Kodni qo'lda yozing yoki klaviatura kabi ishlaydigan skanerdan foydalaning.",
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Kamera bilan skanerlash" })).toBeNull();
  });

  it("is not offered where the detector reads QR codes only", async () => {
    setup({ host: withCamera([], ["qr_code"]).host });
    expect(await screen.findByText("Bu brauzer tovar yorliqlaridagi shtrix-kodlarni o'qimaydi. Kodni qo'lda yozing.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Kamera bilan skanerlash" })).toBeNull();
  });

  it("is offered where labels can be read, opens only when asked, gives the code and stops the stream", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const camera = withCamera([EAN]);
    const { onCode } = setup({ host: camera.host });
    const open = await screen.findByRole("button", { name: "Kamera bilan skanerlash" });
    // Nothing of the camera is touched until the person asks for it.
    expect(camera.asked).toEqual([]);
    fireEvent.click(open);
    await waitFor(() => expect(onCode).toHaveBeenCalledWith(EAN), { timeout: 2000 });
    expect(camera.asked).toEqual([{ video: { facingMode: { ideal: "environment" } }, audio: false }]);
    await waitFor(() => expect(camera.stopped).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("group", { name: "Kamera bilan skanerlash" })).toBeNull();
  });

  it("stops the stream when the person closes it without a code", async () => {
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const camera = withCamera([]);
    const { onCode } = setup({ host: camera.host });
    fireEvent.click(await screen.findByRole("button", { name: "Kamera bilan skanerlash" }));
    await waitFor(() => expect(camera.asked).toHaveLength(1));
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole("button", { name: "Kamerani yopish" }));
    await waitFor(() => expect(camera.stopped).toHaveBeenCalledTimes(1));
    expect(onCode).not.toHaveBeenCalled();
  });

  it("says so when the permission is refused, and manual entry stays", async () => {
    const camera = withCamera([]);
    const denied = Object.assign(new Error("denied"), { name: "NotAllowedError" });
    (camera.host.navigator as { mediaDevices: { getUserMedia: unknown } }).mediaDevices.getUserMedia = async () => {
      throw denied;
    };
    const { onCode, typeAndEnter } = setup({ host: camera.host });
    fireEvent.click(await screen.findByRole("button", { name: "Kamera bilan skanerlash" }));
    expect(
      (await screen.findByText("Kameraga ruxsat berilmadi. Brauzer sozlamalarida ruxsat bering yoki kodni qo'lda yozing.")).closest(
        "[role=alert]",
      ),
    ).toBeTruthy();
    typeAndEnter(EAN, 250);
    expect(onCode).toHaveBeenCalledWith(EAN);
  });
});
