// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fakeServer } from "../../testing/fakeServer";
import { PanelRoot } from "../PanelRoot";
import type { LoginWidgetProps } from "../TelegramLogin";

/** Stands in for Telegram's widget: a button in its place. Loads nothing and leads nowhere. */
function StubWidget({ botUsername }: LoginWidgetProps) {
  return (
    <button type="button" data-bot={botUsername}>
      telegram
    </button>
  );
}

function connection(online: boolean) {
  Object.defineProperty(window.navigator, "onLine", { value: online, configurable: true });
  window.dispatchEvent(new Event(online ? "online" : "offline"));
}

beforeEach(() => {
  window.location.hash = "";
});
afterEach(() => {
  cleanup();
  connection(true);
});

describe("the panel opened without a connection", () => {
  it("draws itself, says there is no connection, asks the server nothing, and offers no sign-in that cannot work", () => {
    connection(false);
    const server = fakeServer(() => "offline");
    render(<PanelRoot initialLanguage="uz" fetch={server.fetch} botUsername="qarz_daftari_bot" LoginWidget={StubWidget} />);

    expect(screen.getByRole("heading", { level: 1, name: "Panelga kirish" })).toBeTruthy();
    const notice = screen.getByRole("alert");
    expect(notice.textContent).toContain("Internet aloqasi yo'q");
    expect(notice.textContent).toContain("aloqasiz hech narsa ko'rsatilmaydi");
    expect(screen.queryByRole("button", { name: "telegram" })).toBeNull();
    expect(server.sent).toEqual([]);
  });

  it("offers the sign-in again as soon as the connection is back", () => {
    connection(false);
    const server = fakeServer(() => "offline");
    render(<PanelRoot initialLanguage="uz" fetch={server.fetch} botUsername="qarz_daftari_bot" LoginWidget={StubWidget} />);
    expect(screen.queryByRole("button", { name: "telegram" })).toBeNull();

    act(() => connection(true));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "telegram" })).toBeTruthy();
  });

  it("with a connection the notice is not there at all", () => {
    render(<PanelRoot initialLanguage="uz" fetch={fakeServer(() => "offline").fetch} botUsername="qarz_daftari_bot" LoginWidget={StubWidget} />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "telegram" })).toBeTruthy();
  });
});
