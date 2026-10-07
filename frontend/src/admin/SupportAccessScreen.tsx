import { type FormEvent, type ReactNode, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import { type Column, DataTable } from "../panel/DataTable";
import { usePagedList } from "../shared/hooks";
import { Link, navigate } from "../shared/router";
import { Empty, Failure, FieldError, formatInstant, Loading, LoadMore } from "../shared/workspace/parts";
import type { AdminApi, SupportAccess } from "./adminApi";
import "./messages";
import { isOpenAccess, isUuid, shortId } from "./rules";
import type { Who } from "./SupportSection";

/**
 * Every support access administrators opened, across shops, newest first (REQ-059): which shop, who,
 * why, from when until when, and how it ended. It can be narrowed to one shop and to the accesses open
 * now. Opening and closing belong to a shop's page, which each row leads to. No customer is named here.
 */
export function SupportAccessScreen({
  api,
  shopId,
  now,
  who,
}: {
  api: AdminApi;
  /** The shop the address narrows the list to. */
  shopId: string | null;
  now: () => Date;
  who: Who;
}) {
  const { t, language } = useI18n();
  const [openOnly, setOpenOnly] = useState(false);
  const [shopText, setShopText] = useState(shopId ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const list = usePagedList((cursor, signal) => api.listSupport({ shopId, open: openOnly, cursor }, signal), [api, shopId, openOnly]);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const typed = shopText.trim();
    if (typed !== "" && !isUuid(typed)) {
      setProblem(t("admin.audit.shop.invalid"));
      return;
    }
    // The shop is part of the address, so a narrowed list can be linked to and returned to.
    navigate(typed === "" ? "/support-access" : `/support-access/${typed.toLowerCase()}`);
  };

  const stateText = (access: SupportAccess): string => {
    if (isOpenAccess(access, now())) {
      return t("admin.support.state.active");
    }
    if (access.closedAt === null) {
      return t("admin.support.state.expired");
    }
    const date = formatInstant(access.closedAt, language);
    if (access.closedBy === "owner" || access.closedBy === "admin") {
      return t(`admin.support.state.${access.closedBy}`, { date });
    }
    return t("admin.support.state.closed", { date });
  };

  const columns: Column<SupportAccess>[] = [
    {
      id: "shop",
      header: t("admin.support.col.shop"),
      rowHeader: true,
      cell: (access) => <Link to={`/shops/${access.shopId}`}>{access.shopName ?? t("admin.audit.target.shop", { code: shortId(access.shopId) })}</Link>,
    },
    {
      id: "admin",
      header: t("admin.audit.admin"),
      cell: (access) => (access.adminId === who.me ? t("admin.support.you", { code: shortId(access.adminId) }) : shortId(access.adminId)),
    },
    { id: "reason", header: t("admin.reason"), cell: (access) => access.reason },
    { id: "from", header: t("admin.support.col.from"), cell: (access) => formatInstant(access.startsAt, language) },
    { id: "to", header: t("admin.support.col.to"), cell: (access) => formatInstant(access.endsAt, language) },
    { id: "state", header: t("admin.support.col.state"), cell: stateText },
  ];

  let body: ReactNode;
  if (list.state.status === "loading") {
    body = <Loading />;
  } else if (list.state.status === "error") {
    body = <Failure error={list.state.error} onRetry={list.reload} />;
  } else if (list.state.items.length === 0) {
    body = <Empty>{t("admin.support.list.none")}</Empty>;
  } else {
    body = (
      <>
        <DataTable caption={t("admin.nav.supportAccess")} columns={columns} items={list.state.items} rowKey={(access) => access.id} />
        {list.state.nextCursor !== null ? (
          <LoadMore loading={list.state.loadingMore} error={list.state.moreError} onClick={list.loadMore} />
        ) : null}
      </>
    );
  }

  return (
    <>
      <form className="filters" onSubmit={onSubmit} noValidate aria-label={t("admin.support.filters")}>
        <div className="field field--inline">
          <label htmlFor="support-shop">{t("admin.audit.shop")}</label>
          <input
            id="support-shop"
            className="input"
            autoComplete="off"
            value={shopText}
            maxLength={36}
            aria-invalid={problem !== null}
            aria-describedby="support-shop-error"
            onChange={(event) => {
              setShopText(event.target.value);
              setProblem(null);
            }}
          />
          <FieldError id="support-shop-error" message={problem} />
        </div>
        <p className="actions">
          <button type="submit" className="button">
            {t("admin.audit.apply")}
          </button>
        </p>
        <div className="field field--inline">
          <label>
            <input type="checkbox" checked={openOnly} onChange={(event) => setOpenOnly(event.target.checked)} /> {t("admin.support.openOnly")}
          </label>
        </div>
      </form>
      {body}
    </>
  );
}
