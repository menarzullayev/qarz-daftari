// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { lazy, Suspense, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, type MockInstance, vi } from "vitest";

import { setActiveLanguage } from "../i18n/catalog";
import { ErrorBoundary, isChunkLoadError, type Page, RELOAD_EVERY_MS, RELOAD_MARK, reloadOnce } from "./ErrorBoundary";

/** A page whose reloads are counted, with a tab's storage and a clock the test moves. */
function fakePage(options: { online?: boolean; storage?: "none" | "forgetful" } = {}) {
  const kept = new Map<string, string>();
  const page = {
    reloads: 0,
    time: 1_000_000,
    kept,
    reload: () => {
      page.reloads += 1;
    },
    storage:
      options.storage === "none"
        ? null
        : {
            getItem: (name: string) => kept.get(name) ?? null,
            setItem: (name: string, value: string) => {
              if (options.storage !== "forgetful") {
                kept.set(name, value);
              }
            },
          },
    now: () => page.time,
    online: () => options.online ?? true,
  };
  return page satisfies Page;
}

/** What Chromium says when a file named by an old page is gone after a deployment. */
const GONE = new TypeError("Failed to fetch dynamically imported module: https://qarz.test/assets/ReportsScreen-a1b2c3.js");

function Broken({ error }: { error: Error }): never {
  throw error;
}

let logged: MockInstance<typeof console.error>;

beforeEach(() => {
  // React and the boundary both write what happened to the console; the tests read it there.
  logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
  setActiveLanguage("uz");
});

afterEach(() => {
  cleanup();
  logged.mockRestore();
});

describe("isChunkLoadError", () => {
  it.each([
    "Failed to fetch dynamically imported module: https://qarz.test/assets/CashScreen-1f.js",
    "error loading dynamically imported module: https://qarz.test/assets/CashScreen-1f.js",
    "Importing a module script failed.",
    "Unable to preload CSS for /assets/reports-9c.css",
  ])("knows a browser's words for a file that could not be fetched: %s", (message) => {
    expect(isChunkLoadError(new TypeError(message))).toBe(true);
  });

  it("does not take an ordinary error for one", () => {
    expect(isChunkLoadError(new TypeError("Cannot read properties of undefined (reading 'items')"))).toBe(false);
    expect(isChunkLoadError(new Error("Failed to fetch"))).toBe(false);
    expect(isChunkLoadError("Failed to fetch dynamically imported module")).toBe(false);
    expect(isChunkLoadError(null)).toBe(false);
  });
});

describe("a screen that draws", () => {
  it("is shown as it is: no message, no reload, nothing in the console", () => {
    const page = fakePage();
    render(
      <ErrorBoundary scope="page" page={page}>
        <p>Mijozlar</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("Mijozlar")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(page.reloads).toBe(0);
    expect(logged).not.toHaveBeenCalled();
  });
});

describe("a screen that cannot be drawn", () => {
  it("is replaced by a message with a way out, never by an empty page", () => {
    const page = fakePage();
    const { container } = render(
      <ErrorBoundary scope="page" page={page}>
        <Broken error={new TypeError("Cannot read properties of undefined (reading 'items')")} />
      </ErrorBoundary>,
    );
    expect(container.textContent).not.toBe("");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Sahifani ko'rsatib bo'lmadi");
    expect(screen.getByRole("alert").textContent).toContain("Kutilmagan xatolik yuz berdi.");
    // An error in drawing is not cured by loading the page again unasked: the person decides.
    expect(page.reloads).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Sahifani yangilash" }));
    expect(page.reloads).toBe(1);
  });

  it("speaks the language the interface is in", () => {
    setActiveLanguage("ru");
    render(
      <ErrorBoundary scope="page" page={fakePage()}>
        <Broken error={new Error("boom")} />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Не удалось показать страницу");
    expect(screen.getByRole("button").textContent).toBe("Обновить страницу");
  });

  it("inside the shell is the message alone: the page's own heading and navigation stay", () => {
    render(
      <main>
        <h1>Hisobotlar</h1>
        <ErrorBoundary scope="screen" page={fakePage()}>
          <Broken error={new Error("boom")} />
        </ErrorBoundary>
      </main>,
    );
    expect(screen.getAllByRole("heading").map((heading) => heading.textContent)).toEqual(["Hisobotlar"]);
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("writes the error's name and message to the console, and nothing the screen was showing", () => {
    function Customer({ name, phone }: { name: string; phone: string }): never {
      // What a screen holds: it must not reach the console through the boundary.
      void [name, phone];
      throw new RangeError("amount must be a whole number");
    }
    render(
      <ErrorBoundary scope="page" page={fakePage()}>
        <Customer name="Dilnoza Karimova" phone="+998901234567" />
      </ErrorBoundary>,
    );
    const own = logged.mock.calls.filter((call) => String(call[0]).startsWith("[qd]"));
    expect(own).toHaveLength(1);
    expect(own[0]?.[0]).toBe("[qd] a screen could not be drawn");
    expect(own[0]?.[1]).toBe("RangeError: amount must be a whole number");
    const written = own.flat().join("\n");
    expect(written).not.toContain("Dilnoza");
    expect(written).not.toContain("+998901234567");
  });

  it("starts clean for the next screen when its key changes", () => {
    function Routes() {
      const [path, setPath] = useState("/reports");
      return (
        <>
          <button type="button" onClick={() => setPath("/customers")}>
            Mijozlar
          </button>
          <ErrorBoundary scope="screen" key={path} page={fakePage()}>
            {path === "/reports" ? <Broken error={new Error("boom")} /> : <p>Ali Valiyev</p>}
          </ErrorBoundary>
        </>
      );
    }
    render(<Routes />);
    expect(screen.getByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Mijozlar" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("Ali Valiyev")).toBeTruthy();
  });
});

describe("a part of the application that cannot be fetched", () => {
  it("loads the page again once, by itself, and says what is happening meanwhile", async () => {
    const page = fakePage();
    const Reports = lazy(() => Promise.reject(GONE));
    render(
      <ErrorBoundary scope="screen" page={page}>
        <Suspense fallback={<p>Yuklanmoqda</p>}>
          <Reports />
        </Suspense>
      </ErrorBoundary>,
    );
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Ilova yangilandi."));
    expect(page.reloads).toBe(1);
    expect(page.kept.get(RELOAD_MARK)).toBe(String(page.time));
    // The reload is on its way: the button does not start a second one.
    await waitFor(() => expect(screen.getByRole("button", { name: "Sahifani yangilash" })).toHaveProperty("disabled", true));
  });

  it("does not load the page again a second time when the file is still missing after the first", async () => {
    const page = fakePage();
    // The page that loaded again a minute ago, and failed the same way.
    page.kept.set(RELOAD_MARK, String(page.time - 60_000));
    render(
      <ErrorBoundary scope="screen" page={page}>
        <Broken error={GONE} />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert").textContent).toContain("Ilova yangilandi.");
    expect(page.reloads).toBe(0);
    // The person can still try, as often as they choose.
    fireEvent.click(screen.getByRole("button", { name: "Sahifani yangilash" }));
    expect(page.reloads).toBe(1);
  });

  it("writes one line to the console, with the file's address and nothing else", () => {
    render(
      <ErrorBoundary scope="screen" page={fakePage()}>
        <Broken error={GONE} />
      </ErrorBoundary>,
    );
    const own = logged.mock.calls.filter((call) => String(call[0]).startsWith("[qd]"));
    expect(own).toHaveLength(1);
    expect(own[0]?.[0]).toBe("[qd] a part of the application could not be fetched");
    expect(own[0]?.[1]).toBe(`TypeError: ${GONE.message}`);
  });
});

describe("reloadOnce", () => {
  it("reloads, and remembers when", () => {
    const page = fakePage();
    expect(reloadOnce(page)).toBe(true);
    expect(page.reloads).toBe(1);
    expect(page.kept.get(RELOAD_MARK)).toBe("1000000");
  });

  it("cannot loop: a second call inside ten minutes does nothing, whatever calls it", () => {
    const page = fakePage();
    reloadOnce(page);
    for (let again = 0; again < 50; again += 1) {
      page.time += 1_000;
      expect(reloadOnce(page)).toBe(false);
    }
    expect(page.reloads).toBe(1);
    page.time = 1_000_000 + RELOAD_EVERY_MS - 1;
    expect(reloadOnce(page)).toBe(false);
  });

  it("reloads again for the next deployment, ten minutes later or more", () => {
    const page = fakePage();
    reloadOnce(page);
    page.time += RELOAD_EVERY_MS;
    expect(reloadOnce(page)).toBe(true);
    expect(page.reloads).toBe(2);
  });

  it("does nothing without a connection: a reload would lose the page that is still there", () => {
    const page = fakePage({ online: false });
    expect(reloadOnce(page)).toBe(false);
    expect(page.reloads).toBe(0);
    expect(page.kept.size).toBe(0);
  });

  it("does nothing where the mark cannot be kept, because nothing would stop the next one", () => {
    const none = fakePage({ storage: "none" });
    expect(reloadOnce(none)).toBe(false);
    expect(none.reloads).toBe(0);
    const forgetful = fakePage({ storage: "forgetful" });
    expect(reloadOnce(forgetful)).toBe(false);
    expect(forgetful.reloads).toBe(0);
  });

  it("does nothing when storage throws", () => {
    const page: Page = {
      reload: vi.fn(),
      storage: {
        getItem: () => {
          throw new DOMException("denied", "SecurityError");
        },
        setItem: () => undefined,
      },
      now: () => 1,
      online: () => true,
    };
    expect(reloadOnce(page)).toBe(false);
    expect(page.reload).not.toHaveBeenCalled();
  });

  it("takes a mark that is not a time for no mark", () => {
    const page = fakePage();
    page.kept.set(RELOAD_MARK, "yesterday");
    expect(reloadOnce(page)).toBe(true);
  });
});
