// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { customerBody, deferred, fakeServer, ok, refusal, SHOP_BASE } from "../../testing/fakeServer";
import { renderScreen } from "../../testing/renderScreen";
import { cleanName, NewCustomerScreen } from "./NewCustomerScreen";

beforeEach(() => {
  window.location.hash = "#/customers/new";
});
afterEach(cleanup);

const name = () => screen.getByLabelText<HTMLInputElement>("Ism");
const phone = () => screen.getByLabelText<HTMLInputElement>("Telefon (ixtiyoriy)");
const save = () => screen.getByRole<HTMLButtonElement>("button", { name: "Yangi mijoz" });
const type = (input: HTMLElement, value: string) => fireEvent.change(input, { target: { value } });

describe("adding a customer", () => {
  it("creates the customer with one keyed request and opens their page", async () => {
    const server = fakeServer(() => ok(customerBody({ id: "55555555-5555-4555-8555-555555555555", balance: 0 }), 201));
    renderScreen(<NewCustomerScreen />, { fetch: server.fetch });
    type(name(), "  Ali   Valiyev ");
    type(phone(), " 90 123 45 67 ");
    fireEvent.click(save());

    await waitFor(() => expect(window.location.hash).toBe("#/customers/55555555-5555-4555-8555-555555555555"));
    expect(server.sent).toHaveLength(1);
    expect(server.sent[0]).toMatchObject({ method: "POST", path: `${SHOP_BASE}/customers` });
    expect(server.sent[0]?.body).toEqual({ display_name: "Ali Valiyev", phone: "90 123 45 67" });
    expect(server.sent[0]?.headers["Idempotency-Key"]).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
  });

  it("leaves the phone out when none was given", async () => {
    const server = fakeServer(() => ok(customerBody(), 201));
    renderScreen(<NewCustomerScreen />, { fetch: server.fetch });
    type(name(), "Vali");
    fireEvent.click(save());
    await waitFor(() => expect(server.sent).toHaveLength(1));
    expect(server.sent[0]?.body).toEqual({ display_name: "Vali" });
  });

  it.each([
    ["", "Mijoz ismini kiriting."],
    ["   ", "Mijoz ismini kiriting."],
    ["A".repeat(81), "Ism 80 belgidan oshmasligi kerak."],
  ])("refuses the name %j without calling the server", (typed, message) => {
    const server = fakeServer(() => ok(customerBody(), 201));
    renderScreen(<NewCustomerScreen />, { fetch: server.fetch });
    type(name(), typed);
    fireEvent.click(save());
    expect(screen.getByRole("alert").textContent).toBe(message);
    expect(name().getAttribute("aria-invalid")).toBe("true");
    expect(server.sent).toHaveLength(0);
  });

  it("accepts a name of exactly 80 characters", async () => {
    const server = fakeServer(() => ok(customerBody(), 201));
    renderScreen(<NewCustomerScreen />, { fetch: server.fetch });
    type(name(), "A".repeat(80));
    fireEvent.click(save());
    await waitFor(() => expect(server.sent).toHaveLength(1));
  });

  it("shows the server's refusal of a phone number next to the field and stays on the form", async () => {
    const server = fakeServer(() =>
      refusal(422, "VALIDATION", "Ma'lumotlar noto'g'ri kiritilgan.", { phone: "not a phone number" }),
    );
    renderScreen(<NewCustomerScreen />, { fetch: server.fetch });
    type(name(), "Ali");
    type(phone(), "12");
    fireEvent.click(save());
    await screen.findAllByRole("alert");
    expect(screen.getAllByRole("alert").map((alert) => alert.textContent)).toEqual([
      "Ma'lumotlar noto'g'ri kiritilgan.",
      "Telefon raqami noto'g'ri.",
    ]);
    expect(phone().getAttribute("aria-invalid")).toBe("true");
    expect(window.location.hash).toBe("#/customers/new");
    expect(save().disabled).toBe(false);
  });

  it("sends one request for a double tap, and the same key when a failed request is retried", async () => {
    const first = deferred<"offline">();
    let attempt = 0;
    const server = fakeServer(() => (attempt++ === 0 ? first.promise : ok(customerBody(), 201)));
    renderScreen(<NewCustomerScreen />, { fetch: server.fetch });
    type(name(), "Ali");
    fireEvent.click(save());
    fireEvent.click(screen.getByRole("button", { name: "Saqlanmoqda…" }));
    expect(server.sent).toHaveLength(1);

    first.resolve("offline");
    await screen.findByRole("alert");
    fireEvent.click(save());
    await waitFor(() => expect(server.sent).toHaveLength(2));
    expect(server.sent[1]?.headers["Idempotency-Key"]).toBe(server.sent[0]?.headers["Idempotency-Key"]);
  });
});

describe("cleanName", () => {
  it("trims and collapses white space as the server does", () => {
    expect(cleanName("  Ali \t Valiyev\u00a0aka ")).toBe("Ali Valiyev aka");
    expect(cleanName(" \n ")).toBe("");
  });
});
