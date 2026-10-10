// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { LoginWidgetProps } from "../panel/TelegramLogin";
import { fakeServer } from "../testing/fakeServer";
import { AdminRoot } from "./AdminRoot";
import { fromBase64Url, type NewCredential, PasskeyDeclined, type PasskeyDevice, type PasskeyQuestion, type PasskeyStart, readQuestion, toBase64Url } from "../shared/passkey";
import { PasskeysSection } from "./PasskeysSection";
import { ADMIN, adminApi, authBody, CSRF, NOW, ok, refusal, renderAdmin, shopBody } from "./testing";

/**
 * An administrator's passkey on the page: the sign-in button at the door and the list of devices in the
 * settings. The device is a stand-in; what is checked is what the page sends, and that it keeps nothing.
 */

beforeEach(() => {
  window.location.hash = "";
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(cleanup);

const PATH = "/api/v1/auth/admin-passkey";
const WANTED = 'QD-Passkey challenge="Z1V7bR9tU_6a", rp_id="admin.shop.test", timeout=120000';
const ANSWER = { id: "Y3JlZA", clientData: "Y2xpZW50", authenticatorData: "ZGF0YQ", signature: "c2ln" };
const MADE: NewCredential = { clientData: "Y2xpZW50", authenticatorData: "ZGF0YQ", publicKey: "a2V5", algorithm: -7 };

function StubWidget({ botUsername }: LoginWidgetProps) {
  return (
    <button type="button" data-bot={botUsername}>
      telegram
    </button>
  );
}

function fakeDevice(over: Partial<PasskeyDevice> = {}) {
  const asked: { questions: PasskeyQuestion[]; starts: PasskeyStart[] } = { questions: [], starts: [] };
  const device: PasskeyDevice = {
    available: () => true,
    create: async (start) => {
      asked.starts.push(start);
      return MADE;
    },
    answer: async (question) => {
      asked.questions.push(question);
      return ANSWER;
    },
    ...over,
  };
  return { device, asked };
}

describe("the bytes between the server and the device", () => {
  it("go to base64url and back unchanged", () => {
    const bytes = new Uint8Array([0, 1, 250, 251, 252, 253, 254, 255]);
    const text = toBase64Url(bytes.buffer);
    expect(text).toBe("AAH6-_z9_v8");
    expect(Array.from(new Uint8Array(fromBase64Url(text)))).toEqual(Array.from(bytes));
  });

  it("reads the question from the refusal's header, and nothing from another", () => {
    expect(readQuestion(WANTED)).toEqual({ challenge: "Z1V7bR9tU_6a", rpId: "admin.shop.test", timeout: 120000 });
    expect(readQuestion(null)).toBeNull();
    expect(readQuestion('Basic realm="x"')).toBeNull();
    expect(readQuestion('QD-Passkey rp_id="admin.shop.test"')).toBeNull();
  });
});

describe("signing in with a passkey", () => {
  function door(device: PasskeyDevice, accept = true) {
    const server = fakeServer((sent) => {
      if (sent.path === PATH) {
        const body = sent.body as Record<string, unknown>;
        if (Object.keys(body).length === 0) {
          return { ...refusal(401, "UNAUTHENTICATED", "not signed in"), headers: { "WWW-Authenticate": WANTED } };
        }
        return accept ? ok({ csrf_token: CSRF, expires_at: "2026-10-24T07:00:00+00:00" }) : refusal(401, "UNAUTHENTICATED", "not signed in");
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
        passkeys={device}
        loginReturn={{ status: "none" }}
        now={() => NOW}
      />,
    );
    return server;
  }

  it("asks the server, then the device, and goes on with the answer", async () => {
    const { device, asked } = fakeDevice();
    const server = door(device);
    fireEvent.click(screen.getByRole("button", { name: "Passkey bilan kirish" }));
    await waitFor(() => expect(server.sent.some((sent) => sent.path === `${ADMIN}/shops`)).toBe(true));
    expect(asked.questions).toEqual([{ challenge: "Z1V7bR9tU_6a", rpId: "admin.shop.test", timeout: 120000 }]);
    const calls = server.sent.filter((sent) => sent.path === PATH);
    expect(calls.map((sent) => sent.body)).toEqual([
      {},
      { id: ANSWER.id, client_data: ANSWER.clientData, authenticator_data: ANSWER.authenticatorData, signature: ANSWER.signature },
    ]);
  });

  it("says the device declined, and stays at the door", async () => {
    const { device } = fakeDevice({
      answer: async () => {
        throw new PasskeyDeclined("NotAllowedError");
      },
    });
    const server = door(device);
    fireEvent.click(screen.getByRole("button", { name: "Passkey bilan kirish" }));
    expect((await screen.findByRole("status")).textContent).toBe("Qurilma tasdiqlamadi yoki oyna yopildi.");
    expect(server.sent.filter((sent) => sent.path === PATH)).toHaveLength(1);
    expect(screen.getByText("telegram")).toBeTruthy();
  });

  it("says one thing when the server refuses the answer", async () => {
    const { device } = fakeDevice();
    door(device, false);
    fireEvent.click(screen.getByRole("button", { name: "Passkey bilan kirish" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Passkey qabul qilinmadi. Qayta urinib ko'ring yoki boshqa usul bilan kiring.");
  });

  it("shows no button where the browser has no such device", () => {
    const { device } = fakeDevice({ available: () => false });
    door(device);
    expect(screen.queryByRole("button", { name: "Passkey bilan kirish" })).toBeNull();
    expect(screen.getByText("telegram")).toBeTruthy();
  });
});

describe("an administrator's devices", () => {
  const START = {
    challenge: "Y2hhbGxlbmdl",
    rp: { id: "admin.shop.test", name: "Some Brand" },
    user: { id: "dXNlcg", name: "Some Brand admin" },
    algorithms: [-7, -257, -8],
    exclude: ["b2xk"],
    timeout: 120000,
  };
  const ROW = { id: "0b6f0c0e-0000-4000-8000-00000000c0de", label: "noutbuk", created_at: "2026-10-10T07:00:00+00:00", last_used_at: null };

  it("lists them, adds this one under a name, and removes one", async () => {
    const rows = [ROW];
    const { device, asked } = fakeDevice();
    const { server, api } = adminApi((sent) => {
      if (sent.path === `${ADMIN}/passkeys` && sent.method === "GET") {
        return ok({ items: rows });
      }
      if (sent.path === `${ADMIN}/passkeys/challenge`) {
        return ok(START);
      }
      if (sent.path === `${ADMIN}/passkeys` && sent.method === "POST") {
        rows.push({ ...ROW, id: "0b6f0c0e-0000-4000-8000-00000000beef", label: "telefon" });
        return ok(rows[1], 201);
      }
      rows.shift();
      return ok({ removed: true });
    });
    renderAdmin(<PasskeysSection api={api} device={device} />);
    expect(await screen.findByText("noutbuk")).toBeTruthy();
    expect(screen.getByText("hali ishlatilmagan")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Qurilma nomi"), { target: { value: "  telefon " } });
    fireEvent.click(screen.getByRole("button", { name: "Shu qurilmani qo'shish" }));
    expect(await screen.findByText("telefon")).toBeTruthy();
    expect(asked.starts).toEqual([
      { challenge: "Y2hhbGxlbmdl", rpId: "admin.shop.test", rpName: "Some Brand", userId: "dXNlcg", userName: "Some Brand admin", algorithms: [-7, -257, -8], exclude: ["b2xk"], timeout: 120000 },
    ]);
    const added = server.sent.find((sent) => sent.path === `${ADMIN}/passkeys` && sent.method === "POST");
    expect(added?.body).toEqual({ label: "telefon", client_data: MADE.clientData, authenticator_data: MADE.authenticatorData, public_key: MADE.publicKey, algorithm: -7 });
    expect(added?.headers["Idempotency-Key"]).toBeTruthy();

    fireEvent.click(screen.getAllByRole("button", { name: "Olib tashlash" })[0] as HTMLElement);
    await waitFor(() => expect(screen.queryByText("noutbuk")).toBeNull());
    const removed = server.sent.find((sent) => sent.path.endsWith("/remove"));
    expect(removed?.path).toBe(`${ADMIN}/passkeys/${ROW.id}/remove`);
    expect(removed?.headers["Idempotency-Key"]).toBeTruthy();
  });

  it("adds nothing when the device declines", async () => {
    const { device } = fakeDevice({
      create: async () => {
        throw new PasskeyDeclined("NotAllowedError");
      },
    });
    const { server, api } = adminApi((sent) => (sent.path.endsWith("/challenge") ? ok(START) : ok({ items: [] })));
    renderAdmin(<PasskeysSection api={api} device={device} />);
    expect(await screen.findByText("Hali birorta qurilma qo'shilmagan.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Qurilma nomi"), { target: { value: "telefon" } });
    fireEvent.click(screen.getByRole("button", { name: "Shu qurilmani qo'shish" }));
    expect((await screen.findByRole("status")).textContent).toBe("Qurilma tasdiqlamadi yoki oyna yopildi. Hech narsa qo'shilmadi.");
    expect(server.sent.some((sent) => sent.method === "POST")).toBe(false);
  });

  it("says so where the browser has no such device", async () => {
    const { device } = fakeDevice({ available: () => false });
    const { api } = adminApi(() => ok({ items: [] }));
    renderAdmin(<PasskeysSection api={api} device={device} />);
    expect(await screen.findByText("Bu brauzer passkey'ni qo'llamaydi.")).toBeTruthy();
    expect(screen.queryByLabelText("Qurilma nomi")).toBeNull();
  });
});
