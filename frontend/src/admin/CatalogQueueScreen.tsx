import { type FormEvent, type ReactNode, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import { usePagedList, useSubmit } from "../shared/hooks";
import { Empty, errorText, Failure, FieldError, formatInstant, Loading, LoadMore } from "../shared/workspace/parts";
import { categoryKey, SHARED_CATEGORIES } from "../shared/sharedCatalog";
import type { AdminApi, CatalogSuggestion, SuggestionNames } from "./adminApi";
import "./messages";
import { SHARED_NAME_MAX, SUGGESTION_STATUSES } from "./rules";
import { known } from "./ShopsScreen";

type Decision = { approve: true; names: SuggestionNames | null } | { approve: false };

const clean = (raw: string): string | null => raw.split(/\s+/u).filter(Boolean).join(" ") || null;

/**
 * One suggestion and, while it waits, its decision. An item is approved under the names and the category
 * the administrator gives: the shop's own spelling is offered as the Uzbek name and can be corrected.
 * A barcode is approved as it is.
 */
function Suggestion({ api, row, onDecided }: { api: AdminApi; row: CatalogSuggestion; onDecided: (status: string) => void }) {
  const { t, language } = useI18n();
  const isItem = row.kind === "item";
  const [nameUz, setNameUz] = useState(row.name ?? "");
  const [nameRu, setNameRu] = useState("");
  const [category, setCategory] = useState("other");
  const [problem, setProblem] = useState<string | null>(null);
  const { state, submit } = useSubmit((decision: Decision, key) =>
    (decision.approve ? api.approveSuggestion(row.id, decision.names, key) : api.rejectSuggestion(row.id, key)).then((done) =>
      onDecided(done.status),
    ),
  );
  const pending = state.status === "pending";
  const failure = state.status === "error" ? state.error : null;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!isItem) {
      submit({ approve: true, names: null });
      return;
    }
    const names = { nameRu: clean(nameRu), nameUz: clean(nameUz), category };
    if (names.nameRu === null && names.nameUz === null) {
      setProblem(t("admin.catalog.nameNeeded"));
      return;
    }
    submit({ approve: true, names });
  };

  const item = row.sharedItem;
  const itemName = item === null ? "" : [item.nameUz ?? item.nameRu ?? "", item.amount].filter(Boolean).join(", ");
  const what = isItem
    ? t("admin.catalog.item", { name: row.name ?? "", unit: row.unit ?? "" })
    : t("admin.catalog.barcode", { code: row.barcode ?? "", name: itemName });
  const id = `suggestion-${row.id}`;

  return (
    <li className="row">
      <p className="row__link">
        <span className="row__name">{what}</span>
        <span className="row__meta">{formatInstant(row.createdAt, language)}</span>
      </p>
      {isItem && row.barcode !== null ? <p className="row__meta">{t("admin.catalog.withCode", { code: row.barcode })}</p> : null}
      {row.same > 0 ? <p className="row__meta">{t("admin.catalog.same", { count: row.same })}</p> : null}
      {row.status === "pending" ? (
        <form className="form" onSubmit={onSubmit} noValidate aria-label={what}>
          {failure ? (
            <p className="notice notice--error" role="alert">
              {errorText(failure, t)}
            </p>
          ) : null}
          {isItem ? (
            <>
              <div className="field">
                <label htmlFor={`${id}-uz`}>{t("admin.catalog.nameUz")}</label>
                <input
                  id={`${id}-uz`}
                  className="input"
                  value={nameUz}
                  maxLength={SHARED_NAME_MAX}
                  autoComplete="off"
                  aria-invalid={problem !== null}
                  aria-describedby={`${id}-error`}
                  onChange={(event) => {
                    setNameUz(event.target.value);
                    setProblem(null);
                  }}
                />
                <FieldError id={`${id}-error`} message={problem} />
              </div>
              <div className="field">
                <label htmlFor={`${id}-ru`}>{t("admin.catalog.nameRu")}</label>
                <input
                  id={`${id}-ru`}
                  className="input"
                  value={nameRu}
                  maxLength={SHARED_NAME_MAX}
                  autoComplete="off"
                  onChange={(event) => {
                    setNameRu(event.target.value);
                    setProblem(null);
                  }}
                />
              </div>
              <div className="field">
                <label htmlFor={`${id}-category`}>{t("admin.catalog.category")}</label>
                <select id={`${id}-category`} className="input" value={category} onChange={(event) => setCategory(event.target.value)}>
                  {SHARED_CATEGORIES.map((key) => (
                    <option key={key} value={key}>
                      {t(categoryKey(key))}
                    </option>
                  ))}
                </select>
              </div>
            </>
          ) : null}
          <p className="actions">
            <button type="submit" className="button button--primary" disabled={pending}>
              {pending ? t("state.saving") : t("admin.catalog.approve")}
            </button>
            <button type="button" className="button" disabled={pending} onClick={() => submit({ approve: false })}>
              {t("admin.catalog.reject")}
            </button>
          </p>
        </form>
      ) : null}
    </li>
  );
}

/**
 * What shops proposed for the shared catalogue: items they added by hand and barcodes they attached to
 * items they had picked, the oldest first. A suggestion holds a name, a unit and a barcode and nothing
 * else of a shop; which shop it came from is not shown here, because the server does not say.
 */
export function CatalogQueueScreen({ api }: { api: AdminApi }) {
  const { t } = useI18n();
  const [status, setStatus] = useState<string>(SUGGESTION_STATUSES[0]);
  const [decided, setDecided] = useState<string | null>(null);
  const list = usePagedList((cursor, signal) => api.listSuggestions({ status, cursor }, signal), [api, status]);

  let body: ReactNode;
  if (list.state.status === "loading") {
    body = <Loading />;
  } else if (list.state.status === "error") {
    body = <Failure error={list.state.error} onRetry={list.reload} />;
  } else if (list.state.items.length === 0) {
    body = <Empty>{t("admin.catalog.none")}</Empty>;
  } else {
    body = (
      <>
        <ul className="rows" aria-label={t("admin.catalog.list")}>
          {list.state.items.map((row) => (
            <Suggestion
              key={row.id}
              api={api}
              row={row}
              onDecided={(now) => {
                setDecided(now);
                list.reload();
              }}
            />
          ))}
        </ul>
        {list.state.nextCursor !== null ? (
          <LoadMore loading={list.state.loadingMore} error={list.state.moreError} onClick={list.loadMore} />
        ) : null}
      </>
    );
  }

  return (
    <>
      <form className="filters" onSubmit={(event) => event.preventDefault()} noValidate aria-label={t("admin.queue.filters")}>
        <div className="field field--inline">
          <label htmlFor="suggestions-status">{t("admin.catalog.status")}</label>
          <select
            id="suggestions-status"
            className="input"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setDecided(null);
            }}
          >
            {SUGGESTION_STATUSES.map((option) => (
              <option key={option} value={option}>
                {known("admin.catalog.status", option, t)}
              </option>
            ))}
          </select>
        </div>
      </form>
      <p className="hint">{t("admin.catalog.privacy")}</p>
      <p className="hint">{t("admin.catalog.order")}</p>
      {decided === "approved" || decided === "rejected" ? (
        <p className="notice notice--done" role="status">
          {t(decided === "approved" ? "admin.catalog.done.approved" : "admin.catalog.done.rejected")}
        </p>
      ) : null}
      {body}
    </>
  );
}
