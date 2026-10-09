// @vitest-environment jsdom
import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Role } from "../shared/navigation";
import { MIN_ROLE } from "../shared/permissions";
import { CustomersScreen } from "../shared/workspace/CustomersScreen";
import { CUSTOMER_ID, customerBody, fakeServer, ok } from "../testing/fakeServer";
import { renderScreen } from "../testing/renderScreen";
import { DesktopLayout } from "./tables";
import { setWidth } from "./testing";

/**
 * The panel's table of customers offers the two entries of a new record by the permissions the server
 * named for the member, as the phone's rows do; while it keeps to roles, by the role as before.
 */

afterEach(cleanup);

const SELLER_HOLDS = (Object.keys(MIN_ROLE) as (keyof typeof MIN_ROLE)[]).filter((key) => MIN_ROLE[key] === "seller");
const without = (...denied: string[]) => SELLER_HOLDS.filter((key) => !denied.includes(key));

async function pick(role: Role, permissions?: readonly string[]) {
  setWidth(1280);
  const server = fakeServer(() => ok({ items: [customerBody()], next_cursor: null }));
  renderScreen(
    <DesktopLayout>
      <CustomersScreen pick />
    </DesktopLayout>,
    { fetch: server.fetch, role, ...(permissions ? { permissions } : {}) },
  );
  const table = await screen.findByRole("table", { name: "Mijozlar ro'yxati" });
  return within(table)
    .queryAllByRole("link")
    .map((link) => [link.textContent, link.getAttribute("href")]);
}

describe("the customers' table, picking a customer for a new entry", () => {
  it("offers a seller the sale and the payment while the server keeps to roles", async () => {
    expect(await pick("seller")).toEqual([
      ["Nasiya", `#/customers/${CUSTOMER_ID}/credit`],
      ["To'lov", `#/customers/${CUSTOMER_ID}/payment`],
    ]);
  });

  it("offers the same to a seller whose permissions are the role's, named by the server", async () => {
    expect((await pick("seller", SELLER_HOLDS)).map(([name]) => name)).toEqual(["Nasiya", "To'lov"]);
  });

  it("offers a seller denied `payments.record` the sale alone", async () => {
    expect((await pick("seller", without("payments.record"))).map(([name]) => name)).toEqual(["Nasiya"]);
  });

  it("offers a manager denied `credits.record` the payment alone: the role does not bring the sale back", async () => {
    expect((await pick("manager", without("credits.record"))).map(([name]) => name)).toEqual(["To'lov"]);
  });

  it("offers neither to a member who may record nothing", async () => {
    expect(await pick("owner", ["ledger.view"])).toEqual([]);
  });
});
