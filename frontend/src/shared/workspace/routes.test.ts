import { describe, expect, it } from "vitest";

import { matchWorkspaceRoute } from "./routes";

const ID = "11111111-1111-4111-8111-111111111111";

describe("matchWorkspaceRoute", () => {
  it.each([
    ["/", { screen: "overview" }, "/"],
    ["/customers", { screen: "customers" }, "/customers"],
    ["/customers/new", { screen: "newCustomer" }, "/customers"],
    ["/new", { screen: "pickCustomer" }, "/new"],
    [`/customers/${ID}`, { screen: "customer", customerId: ID }, "/customers"],
    [`/customers/${ID}/credit`, { screen: "entry", customerId: ID, kind: "credit" }, "/customers"],
    [`/customers/${ID}/payment`, { screen: "entry", customerId: ID, kind: "payment" }, "/customers"],
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
  ])("does not match %s", (path) => {
    expect(matchWorkspaceRoute(path)).toBeNull();
  });
});
