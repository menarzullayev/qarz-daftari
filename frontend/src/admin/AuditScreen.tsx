import { type FormEvent, type ReactNode, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import { type Column, DataTable } from "../panel/DataTable";
import { usePagedList } from "../shared/hooks";
import { Link, navigate } from "../shared/router";
import { Empty, Failure, FieldError, formatInstant, Loading, LoadMore } from "../shared/workspace/parts";
import type { AdminApi, AuditRow } from "./adminApi";
import "./messages";
import { actorName, isUuid, shortId } from "./rules";
import { known, NONE } from "./ShopsScreen";

/**
 * The groups the audit can be filtered by. Each is the start of the action names the server records
 * ("subscription" matches "subscription.suspended"), which is how the API filters.
 */
export const AUDIT_GROUPS = ["admin", "shop", "subscription", "setting", "support"] as const;

/** What an action changed, as the server recorded it. It is data, shown as it is, not interface text. */
export function detailText(detail: Readonly<Record<string, unknown>>): string {
  const parts = Object.entries(detail).map(([key, value]) => `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`);
  return parts.length === 0 ? NONE : parts.join("; ");
}

/**
 * Everything administrators did, newest first (REQ-058): who, what, about which shop or setting, why,
 * and what changed. It can be narrowed to one kind of action, to one shop and to one administrator.
 * Nothing here can be changed: the audit is the server's, and insert-only.
 */
export function AuditScreen({ api, shopId }: { api: AdminApi; /** The shop the address narrows the audit to. */ shopId: string | null }) {
  const { t, language } = useI18n();
  const [group, setGroup] = useState("");
  const [shopText, setShopText] = useState(shopId ?? "");
  // The address changed under the screen (a link to another shop's audit): the field follows it.
  const [seenShop, setSeenShop] = useState(shopId);
  if (seenShop !== shopId) {
    setSeenShop(shopId);
    setShopText(shopId ?? "");
  }
  const [problem, setProblem] = useState<string | null>(null);
  // The administrator the audit is narrowed to: a full identifier, which is what the API filters by.
  const [adminId, setAdminId] = useState<string | null>(null);
  const [adminText, setAdminText] = useState("");
  const [adminProblem, setAdminProblem] = useState<string | null>(null);
  const list = usePagedList(
    (cursor, signal) => api.listAudit({ shopId, action: group || null, adminId, cursor }, signal),
    [api, shopId, group, adminId],
  );
  const onlyAdmin = (id: string) => {
    setAdminText(id);
    setAdminProblem(null);
    setAdminId(id);
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const typed = shopText.trim();
    const typedAdmin = adminText.trim();
    const shopWrong = typed !== "" && !isUuid(typed);
    const adminWrong = typedAdmin !== "" && !isUuid(typedAdmin);
    setProblem(shopWrong ? t("admin.audit.shop.invalid") : null);
    setAdminProblem(adminWrong ? t("admin.audit.adminFilter.invalid") : null);
    if (shopWrong || adminWrong) {
      return;
    }
    setAdminId(typedAdmin === "" ? null : typedAdmin.toLowerCase());
    // The shop is part of the address, so a filtered audit can be linked to and returned to.
    navigate(typed === "" ? "/audit" : `/audit/${typed.toLowerCase()}`);
  };

  const columns: Column<AuditRow>[] = [
    { id: "at", header: t("admin.audit.at"), rowHeader: true, cell: (row) => formatInstant(row.at, language) },
    {
      id: "admin",
      header: t("admin.audit.admin"),
      // Nobody types an identifier they have only seen the end of: a row narrows the audit to its administrator.
      cell: (row) => {
        const by = row.adminId;
        return (
          <span className="table__actions">
            {actorName(by, row.actorTgId, (id) => t("admin.actor.groupAdmin", { id }), NONE)}
            {/* Only an administrator can be filtered by: a review-group administrator has no account. */}
            {by === null || adminId === by ? null : (
              <button type="button" className="button button--small" onClick={() => onlyAdmin(by)}>
                {t("admin.audit.adminOnly")}
              </button>
            )}
          </span>
        );
      },
    },
    { id: "action", header: t("admin.audit.action"), cell: (row) => known("admin.action", row.action, t) },
    {
      id: "target",
      header: t("admin.audit.target"),
      cell: (row) =>
        row.shopId !== null ? (
          <Link to={`/shops/${row.shopId}`}>{t("admin.audit.target.shop", { code: shortId(row.shopId) })}</Link>
        ) : row.targetType === "setting" && row.targetId !== null ? (
          known("admin.setting", row.targetId, t)
        ) : (
          known("admin.target", row.targetType, t)
        ),
    },
    { id: "reason", header: t("admin.reason"), cell: (row) => row.reason ?? NONE },
    { id: "detail", header: t("admin.audit.detail"), cell: (row) => detailText(row.detail) },
  ];

  let body: ReactNode;
  if (list.state.status === "loading") {
    body = <Loading />;
  } else if (list.state.status === "error") {
    body = <Failure error={list.state.error} onRetry={list.reload} />;
  } else if (list.state.items.length === 0) {
    body = <Empty>{t("admin.audit.none")}</Empty>;
  } else {
    body = (
      <>
        <DataTable caption={t("admin.nav.audit")} columns={columns} items={list.state.items} rowKey={(row) => row.id} />
        {list.state.nextCursor !== null ? (
          <LoadMore loading={list.state.loadingMore} error={list.state.moreError} onClick={list.loadMore} />
        ) : null}
      </>
    );
  }

  return (
    <>
      <form className="filters" onSubmit={onSubmit} noValidate aria-label={t("admin.audit.filters")}>
        <div className="field field--inline">
          <label htmlFor="audit-action">{t("admin.audit.action")}</label>
          <select id="audit-action" className="input" value={group} onChange={(event) => setGroup(event.target.value)}>
            <option value="">{t("admin.audit.action.all")}</option>
            {AUDIT_GROUPS.map((option) => (
              <option key={option} value={option}>
                {known("admin.audit.group", option, t)}
              </option>
            ))}
          </select>
        </div>
        <div className="field field--inline">
          <label htmlFor="audit-shop">{t("admin.audit.shop")}</label>
          <input
            id="audit-shop"
            className="input"
            autoComplete="off"
            value={shopText}
            maxLength={36}
            aria-invalid={problem !== null}
            aria-describedby="audit-shop-error"
            onChange={(event) => {
              setShopText(event.target.value);
              setProblem(null);
            }}
          />
          <FieldError id="audit-shop-error" message={problem} />
        </div>
        <div className="field field--inline">
          <label htmlFor="audit-admin">{t("admin.audit.adminFilter")}</label>
          <input
            id="audit-admin"
            className="input"
            autoComplete="off"
            value={adminText}
            maxLength={36}
            aria-invalid={adminProblem !== null}
            aria-describedby="audit-admin-error"
            onChange={(event) => {
              setAdminText(event.target.value);
              setAdminProblem(null);
            }}
          />
          <FieldError id="audit-admin-error" message={adminProblem} />
        </div>
        <p className="actions">
          <button type="submit" className="button">
            {t("admin.audit.apply")}
          </button>
        </p>
      </form>
      {body}
    </>
  );
}
