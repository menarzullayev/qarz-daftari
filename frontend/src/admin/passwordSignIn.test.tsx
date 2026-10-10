// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { LoginWidgetProps } from "../panel/TelegramLogin";
import { fakeServer, type Sent } from "../testing/fakeServer";
import { AdminRoot } from "./AdminRoot";
import { ADMIN, authBody, CSRF, NOW, ok, refusal, shopBody } from "./testing";

/**
 * The administrator's other way in on the sign-in page: a login and a password
 * (`POST /api/v1/auth/admin-password`). The form sends exactly the two fields, goes on with what the
 * Telegram sign-in goes on with, and says one thing when it is refused.
 */

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(cleanup);

const PATH = "/api/v1/auth/admin-password";
const REFUSED = "Login yoki parol qabul qilinmadi. Besh marta xato kiritilsa, login 15 daqiqaga yopiladi.";

function StubWidget({ botUsername }: LoginWidgetProps) {
  return (
    <button type="button" data-bot={botUsername}>
      telegram
    </button>
  );
}

function door(answer: (sent: Sent) => ReturnType<typeof ok>) {
  const server = fakeServer((sent) => {
    if (sent.path === PATH) {
      return answer(sent);
    }
    if (sent.path === `${ADMIN}/auth`) {
      return ok(authBody({ elevated: true, second_factor: "off" }));
    }
    if (sent.path === `${ADMIN}/shops`) {
      return ok({ items: [shopBody()], next_cursor: null });
    }
    return ok({ items: [], next_cursor: null });
  });
  render(
    <AdminRoot
      initialLanguage="uz"
      fetch={server.fetch}
      botUsername="qarz_daftari_bot"
      LoginWidget={StubWidget}
      loginReturn={{ status: "none" }}
      now={() => NOW}
    />,
  );
  return server;
}

function fill(login: string, password: string) {
  fireEvent.click(screen.getByText("Parol bilan kirish"));
  fireEvent.change(screen.getByLabelText("Login"), { target: { value: login } });
  fireEvent.change(screen.getByLabelText("Parol"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Kirish" }));
}

describe("signing in with a password", () => {
  it("sends the two fields and goes on to the panel", async () => {
    const server = door(() => ok({ csrf_token: CSRF, expires_at: "2026-10-24T07:00:00+00:00" }));
    expect(screen.getByText("telegram")).toBeTruthy();
    fill("  boss ", "a long and private phrase 42");
    await waitFor(() => expect(server.sent.some((sent) => sent.path === `${ADMIN}/shops`)).toBe(true));
    const sent = server.sent.find((one) => one.path === PATH);
    expect(sent?.method).toBe("POST");
    expect(sent?.body).toEqual({ login: "boss", password: "a long and private phrase 42" });
  });

  it("says one thing when it is refused, and forgets the password", async () => {
    door(() => refusal(401, "UNAUTHENTICATED", "not signed in"));
    fill("boss", "not the password at all");
    expect((await screen.findByRole("alert")).textContent).toBe(REFUSED);
    expect((screen.getByLabelText("Parol") as HTMLInputElement).value).toBe("");
    expect(screen.getByText("telegram")).toBeTruthy();
  });

  it("sends nothing while a field is empty", () => {
    const server = door(() => ok({ csrf_token: CSRF, expires_at: "2026-10-24T07:00:00+00:00" }));
    fill("boss", "");
    expect(server.sent.some((sent) => sent.path === PATH)).toBe(false);
  });
});
