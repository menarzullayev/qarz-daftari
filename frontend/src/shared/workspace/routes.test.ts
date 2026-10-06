import { describe, expect, it } from "vitest";

import { matchWorkspaceRoute } from "./routes";

const ID = "11111111-1111-4111-8111-111111111111";
const ENTRY = "22222222-2222-4222-8222-222222222222";

describe("matchWorkspaceRoute", () => {
  it.each([
    ["/", { screen: "overview" }, "/"],
    ["/customers", { screen: "customers" }, "/customers"],
    ["/customers/new", { screen: "newCustomer" }, "/customers"],
    ["/new", { screen: "pickCustomer" }, "/new"],
    [`/customers/${ID}`, { screen: "customer", customerId: ID }, "/customers"],
    [`/customers/${ID}/credit`, { screen: "entry", customerId: ID, kind: "credit" }, "/customers"],
    [`/customers/${ID}/payment`, { screen: "entry", customerId: ID, kind: "payment" }, "/customers"],
    [`/customers/${ID}/entries/${ENTRY}/goods`, { screen: "addGoods", customerId: ID, entryId: ENTRY }, "/customers"],
    ["/catalog", { screen: "catalog" }, "/catalog"],
    ["/shop-settings", { screen: "shopSettings" }, "/shop-settings"],
  ])("%s", (path, route, sectionPath) => {
    expect(matchWorkspaceRoute(path)).toMatchObject({ route, sectionPath });
  });

  it.each([
    "/reports",
    "/customers/",
    "/customers/42",
    "/customers/new/credit",
    `/customers/${ID}/refund`,
    `/customers/${ID}/credit/extra`,
    `/customers/${ID.slice(0, -1)}`,
    `/customers/${ID}x`,
    "/customers/..%2F..%2Fme",
    `/entries/${ID}`,
    `/customers/${ID}/entries/${ENTRY}`,
    `/customers/${ID}/entries/42/goods`,
    `/customers/${ID}/entries/${ENTRY}/goods/extra`,
    `/entries/${ENTRY}/goods`,
    "/catalog/new",
    `/catalog/${ID}`,
    "/shop-settings/edit",
  ])("does not match %s", (path) => {
    expect(matchWorkspaceRoute(path)).toBeNull();
  });
});
