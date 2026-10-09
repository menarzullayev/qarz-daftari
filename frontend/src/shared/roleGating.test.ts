import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * What a member may do is decided by permissions (the server's catalogue; `permissions.ts` here), and
 * the interface asks one helper: `useMay()` in a component, `may(viewer, key)` in a rule. A place that
 * looks at the role instead keeps offering what the owner took away from one member, and hides what the
 * owner gave. This test reads the source and fails on any look at the role that is not listed below
 * with the reason it is by role on purpose (docs/08-technical-spec/OUTPUT.md, "Checks that stay by role
 * on purpose").
 */

const SRC = resolve(import.meta.dirname, "..");

/** The ways the code has of deciding by role. Each is tried against one line of code. */
const ROLE_GATES: readonly RegExp[] = [
  // role === "owner", member.role !== "seller", role == other
  /\brole\s*[!=]==?/i,
  // "owner" === role
  /["'`](owner|manager|seller)["'`]\s*[!=]==?/,
  // isOwner = ..., isManager(...), a flag or a helper named after a role
  /\b(const|let|var|function)\s+is(Owner|Manager|Seller)\b/,
  // canManage(role), atLeast(role, "manager")
  /\b(canManage|atLeast|roleAtLeast|hasRole)\s*\(/,
  // the table of the lowest role per permission, used apart from the helper
  /\bMIN_ROLE\b/,
  // RANK[role] >= RANK.manager, ROLES.indexOf(role)
  /\bRANK\s*[[.]/,
  /\bROLES\.indexOf\(/,
  // ["owner", "manager"].includes(role)
  /\.includes\(\s*(\w+\.)*role\b/i,
  // switch (role)
  /\bswitch\s*\(\s*(\w+\.)*role\s*\)/i,
];

/** The lines of a source text that decide by role, trimmed; comments are not code and are skipped. */
export function roleGates(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== "" && !/^(\/\/|\/\*|\*)/.test(line))
    .filter((line) => ROLE_GATES.some((pattern) => pattern.test(line)));
}

type Allowed = { file: string; code: string; why: string };

/** Fixed permissions follow the role alone and can be neither granted nor denied. */
const FIXED = "a fixed permission: the role alone, by the catalogue";

/**
 * Every place that is by role on purpose: the file, the line as written, and why. A line that changes
 * must be looked at again; one that is gone must leave this list (the last test below).
 */
const BY_ROLE_ON_PURPOSE: readonly Allowed[] = [
  // --- the helper itself: the role is the preset while the server keeps no permissions per member
  { file: "shared/permissions.ts", code: "export const MIN_ROLE = {", why: "the role presets, used by may() alone" },
  { file: "shared/permissions.ts", code: "export type PermissionKey = keyof typeof MIN_ROLE;", why: "the keys of the presets" },
  {
    file: "shared/permissions.ts",
    code: "return RANK[viewer.role] >= RANK[MIN_ROLE[permission]];",
    why: "may(): the role decides only when the server named no permissions",
  },
  { file: "shared/navigation.ts", code: "return ROLES.some((role) => role === value);", why: "isRole(): reads a value, gates nothing" },
  { file: "shared/navigation.ts", code: "export function canManage(role: Role): boolean {", why: `ownership.receive, ${FIXED}` },
  { file: "shared/navigation.ts", code: "return RANK[role] >= RANK.manager;", why: `ownership.receive, ${FIXED}` },

  // --- fixed permissions
  { file: "panel/DeletionSection.tsx", code: 'return role === "owner" ? <DeleteShop /> : null;', why: `shop.delete, ${FIXED}` },
  {
    file: "panel/office.tsx",
    code: '(signal) => (role === "owner" ? office.readDeletion(signal) : Promise.resolve(null)),',
    why: `shop.delete, ${FIXED}`,
  },
  {
    file: "panel/office.tsx",
    code: "(signal) => (canManage(role) ? office.readTransfer(signal) : Promise.resolve(null)),",
    why: `ownership.receive, ${FIXED}; only the named manager answers`,
  },
  {
    file: "panel/office.tsx",
    code: '(signal) => (role === "owner" ? supportOf(api).list(null, signal).then((page) => page.items) : Promise.resolve([])),',
    why: `support.manage, ${FIXED}`,
  },
  { file: "panel/OfficeBanner.tsx", code: 'if (role !== "owner" || support.status !== "ready") {', why: `support.manage, ${FIXED}` },
  {
    file: "panel/OfficeBanner.tsx",
    code: 'if (role === "owner" && deletion.status === "ready" && deletion.data?.status === "deletion_pending") {',
    why: `shop.delete, ${FIXED}`,
  },
  { file: "shared/StaffApp.tsx", code: 'return role === "owner" ? (', why: `support.manage, ${FIXED}` },
  {
    file: "shared/support/SupportAccessSection.tsx",
    code: 'return role === "owner" ? <Section onChanged={onChanged} /> : null;',
    why: `support.manage, ${FIXED}`,
  },
  { file: "shared/workspace/SubscriptionScreen.tsx", code: 'const isOwner = role === "owner";', why: `subscription.manage, ${FIXED}` },
  {
    file: "panel/StaffScreen.tsx",
    code: 'const isOwner = role === "owner";',
    why: `permissions.manage and ownership.transfer, ${FIXED}; a role is the owner's alone to give, and someone given staff.manage acts on sellers only (the server's escalation rule)`,
  },

  // --- the owner as a person, and who may be given a role
  {
    file: "panel/OwnerTotals.tsx",
    code: 'const owned = (shops ?? []).filter((shop) => shop.role === "owner").length;',
    why: "the owner as a person: the shops a person owns",
  },
  {
    file: "panel/StaffScreen.tsx",
    code: 'return member.role === "manager" ? "seller" : "manager";',
    why: "who may be given a role: manager or seller, never owner",
  },
  { file: "panel/StaffScreen.tsx", code: 'if (member.role === "owner") {', why: "the owner as a person: nothing stored changes the owner" },
  {
    file: "panel/StaffScreen.tsx",
    code: 'if (!isOwner && (member.role !== "seller" || member.id === membershipId)) {',
    why: "the server's escalation rule: someone given staff.manage acts on sellers only, never on themselves",
  },
  {
    file: "panel/StaffScreen.tsx",
    code: '{isOwner && member.role === "manager" && member.status === "active" && noOffer',
    why: "ownership transfer: the target must be an active manager",
  },

  // --- BR-30
  {
    file: "shared/exports/ExportsScreen.tsx",
    code: '{shopMode === "suspended" && role !== "owner" ? (',
    why: "BR-30: in a suspended shop only the owner still looks at the data",
  },
];

function sources(): { name: string; text: string }[] {
  return readdirSync(SRC, { recursive: true, encoding: "utf8" })
    .map((name) => name.replaceAll("\\", "/"))
    .filter((name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name))
    // Test helpers render what a test asks for, and the generated description of the API is data. The
    // administrator's console is not a shop's workspace: it has no members of a shop to gate.
    .filter((name) => !name.startsWith("testing/") && !name.startsWith("admin/") && !name.endsWith(".generated.ts"))
    .map((name) => ({ name, text: readFileSync(resolve(SRC, name), "utf8") }));
}

const found = sources().flatMap((file) => roleGates(file.text).map((code) => ({ file: file.name, code })));
const isAllowed = (gate: { file: string; code: string }) =>
  BY_ROLE_ON_PURPOSE.some((allowed) => allowed.file === gate.file && allowed.code === gate.code);

describe("the scanner for decisions by role", () => {
  it.each([
    "return role === 'owner' ? <Delete /> : null;",
    'if (role !== "seller") {',
    'const isOwner = session.role == "owner";',
    '{member.role === "manager" ? <Offer /> : null}',
    'if ("owner" === viewer.kind) {',
    "const isManager = rank > 0;",
    "function isSeller(viewer: Viewer) {",
    "{canManage(role) ? <Reverse /> : null}",
    'if (atLeast(role, "manager")) {',
    'const allowed = MIN_ROLE["customers.create"] === "seller";',
    "return RANK[role] >= RANK.manager;",
    "const high = ROLES.indexOf(role) > 0;",
    '{["owner", "manager"].includes(role) ? <AddCustomer /> : null}',
    "if (MANAGERS.includes(session.role)) {",
    "switch (role) {",
    "switch (viewer.role) {",
  ])("catches %s", (line) => {
    expect(roleGates(line)).toEqual([line]);
  });

  it.each([
    '{can("customers.create") ? <AddCustomer /> : null}',
    'return may(viewer, "entries.cancel");',
    'const { role, permissions } = useWorkspace();',
    'role: options.role ?? "seller",',
    '<span className="row__meta">{t(`role.${shop.role}`)}</span>',
    '<div className="notice" role="alert">',
    '// by role === "owner" in the old days',
    '* role === "owner" is how the server names the owner',
  ])("lets %s pass", (line) => {
    expect(roleGates(line)).toEqual([]);
  });

  it("reads every line of a text and answers the ones that decide by role, trimmed", () => {
    const text = ['  const can = useMay();', '  return role === "owner" ? <A /> : null;', "", "  // role === 1"].join("\r\n");
    expect(roleGates(text)).toEqual(['return role === "owner" ? <A /> : null;']);
  });
});

describe("what a member is offered is decided by permission, not by role", () => {
  it("looks at the code of the Mini App, the panel and what they share", () => {
    const names = sources().map((file) => file.name);
    expect(names).toContain("app/main.tsx");
    expect(names).toContain("panel/tables.tsx");
    expect(names).toContain("panel/StaffScreen.tsx");
    expect(names).toContain("shared/StaffRoot.tsx");
    expect(names).toContain("shared/workspace/CustomersScreen.tsx");
    expect(names).toContain("shared/stock/StockScreen.tsx");
    expect(names).toContain("shared/cash/CashScreen.tsx");
    expect(names.some((name) => name.includes(".test."))).toBe(false);
    expect(names.length).toBeGreaterThan(100);
  });

  it("decides by role nowhere but in the places listed, each with its reason", () => {
    expect(found.filter((gate) => !isAllowed(gate))).toEqual([]);
  });

  it("would fail on the same line in a file that is not listed for it", () => {
    // The list is by file and by line: a look at the role that is allowed in one place is not allowed
    // in another, and a changed line is a new line.
    expect(isAllowed({ file: "panel/DeletionSection.tsx", code: 'return role === "owner" ? <DeleteShop /> : null;' })).toBe(true);
    expect(isAllowed({ file: "shared/workspace/CustomersScreen.tsx", code: 'return role === "owner" ? <DeleteShop /> : null;' })).toBe(false);
    expect(isAllowed({ file: "panel/DeletionSection.tsx", code: 'return role !== "seller" ? <DeleteShop /> : null;' })).toBe(false);
  });

  it("lists nothing that is no longer in the code, and gives each a reason", () => {
    const stale = BY_ROLE_ON_PURPOSE.filter((allowed) => !found.some((gate) => gate.file === allowed.file && gate.code === allowed.code));
    expect(stale).toEqual([]);
    expect(BY_ROLE_ON_PURPOSE.filter((allowed) => allowed.why.trim().length < 10)).toEqual([]);
  });
});
