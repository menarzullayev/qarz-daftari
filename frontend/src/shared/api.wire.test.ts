import { describe, expect, it } from "vitest";

import { customerBody, detailBody, fakeServer, ok, SHOP_ID } from "../testing/fakeServer";
import { createApi, reading, type Wire } from "./api";

/**
 * The readers are checked against the generated shapes of the API (`Wire`). The lines marked
 * `@ts-expect-error` are the negative cases of that check: each is a mistake the type check must refuse,
 * and `npm run typecheck` fails with "unused directive" the day it stops refusing one. They are what a
 * reader looks like after the back end renamed, removed or retyped a field and the types were
 * regenerated.
 */
describe("reading an answer against its generated shape", () => {
  const body = () => reading.fieldsOf<Wire["Customer"]>(customerBody());

  it("reads a field the back end declares with a reader that takes what is sent for it", () => {
    expect(body().get("display_name", reading.text)).toBe("Ali Valiyev");
    expect(body().get("phone", reading.textOrNull)).toBe("+998901234567");
    expect(body().get("credit_limit", reading.wholeOrNull)).toBeNull();
    expect(body().get("balance", reading.whole)).toBe(120000);
    // A reader may take more than the back end sends: a text that is never null, read as text or null.
    expect(body().get("id", reading.textOrNull)).toBe("11111111-1111-4111-8111-111111111111");
  });

  it("refuses, at type check, a field the back end does not declare", () => {
    // @ts-expect-error -- `full_name` is not a field of a customer
    expect(body().raw("full_name")).toBeUndefined();
    // @ts-expect-error -- nor can it be read with a reader
    expect(() => body().get("full_name", reading.text)).toThrow();
  });

  it("refuses, at type check, a reader that does not take what the back end sends", () => {
    // @ts-expect-error -- the balance is a number: a text reader does not take it
    expect(() => body().get("balance", reading.text)).toThrow();
    // @ts-expect-error -- the phone may be null: a reader of text alone does not take that
    expect(body().get("phone", reading.text)).toBe("+998901234567");
    // @ts-expect-error -- the credit limit may be null
    expect(() => body().get("credit_limit", reading.whole)).toThrow();
    // @ts-expect-error -- `reminders_off` is a flag, not a number
    expect(() => body().get("reminders_off", reading.whole)).toThrow();
  });

  it("refuses, at type check, a request body the back end would not take", () => {
    const sent: Wire["NewCustomer"] = { display_name: "Ali" };
    expect(sent).toEqual({ display_name: "Ali" });
    // @ts-expect-error -- the API's name for it is `display_name`
    const renamed: Wire["NewCustomer"] = { displayName: "Ali" };
    // @ts-expect-error -- an amount is a number
    const retyped: Wire["NewEntry"] = { kind: "credit", amount: "45000" };
    expect([renamed, retyped]).toHaveLength(2);
  });

  it("still checks what arrives, whatever the types say", async () => {
    const server = fakeServer(() => ok(detailBody({ balance: "120000" })));
    const shop = createApi({ fetch: server.fetch, auth: { kind: "bearer", token: "t" } }).shop(SHOP_ID);
    await expect(shop.readCustomer("11111111-1111-4111-8111-111111111111")).rejects.toMatchObject({
      code: "BAD_RESPONSE",
    });
  });
});
