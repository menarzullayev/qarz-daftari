import { useState, type FormEvent } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { CustomerDetail, Entry, NewLine } from "../api";
import { formatDateTime, formatMoney } from "../format";
import { goodsWindowOpen } from "../goods";
import { useLoad, useSubmit } from "../hooks";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useWorkspace } from "./context";
import { type DraftLine, GoodsEditor, GoodsList, readDrafts } from "./GoodsEditor";
import { errorText, Failure, Loading } from "./parts";

/**
 * Whether the interface offers "add goods" for an entry (REQ-038): a credit sale that is not reversed,
 * has no goods yet, and is still inside the window. The server also requires the caller to be the
 * entry's author, a manager or an owner; the interface cannot tell who the author is (the API does not
 * say which membership is the signed-in person's), so a seller who is not the author is refused there.
 */
export function canAddGoods(entry: Entry, now: Date): boolean {
  return (
    entry.kind === "credit" &&
    !entry.reversed &&
    entry.lines.length === 0 &&
    goodsWindowOpen(new Date(entry.createdAt), now)
  );
}

function AddGoodsForm({ customer, entry }: { customer: CustomerDetail; entry: Entry }) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const [goods, setGoods] = useState<DraftLine[]>([]);
  const [checked, setChecked] = useState(false);
  const { state, submit } = useSubmit((lines: readonly NewLine[], key) => api.addLines(entry.id, lines, key));

  const back = `/customers/${customer.id}`;
  if (state.status === "done") {
    return (
      <div className="notice notice--done" role="status">
        <p>{t("goods.later.done")}</p>
        <GoodsList lines={state.result.lines} />
        <p className="actions">
          <Link to={back} className="button button--primary">
            {t("customer.open")}
          </Link>
        </p>
      </div>
    );
  }

  const reading = readDrafts(goods);
  // Positive: the goods do not reach the entry amount yet. Negative: they exceed it.
  const missing = entry.amount - reading.sum;
  const ready = reading.lines !== null && reading.lines.length > 0 && missing === 0;
  const pending = state.status === "pending";
  const failure = state.status === "error" ? state.error : null;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setChecked(true);
    // The lines must sum to the amount that was recorded; the entry's amount never changes (INV-8).
    if (reading.lines !== null && reading.lines.length > 0 && missing === 0) {
      submit(reading.lines);
    }
  };

  const made = new Date(entry.createdAt);
  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      <p className="balance">
        <span>{t("goods.later.entry")}</span> <strong>{formatMoney(entry.amount, language)}</strong>
      </p>
      <p className="row__meta">{formatDateTime(made, language)}</p>
      <p className="hint">{t("goods.later.hint")}</p>
      {failure ? (
        <div className="notice notice--error" role="alert">
          <p>{errorText(failure, t)}</p>
          {failure.serverMessage === null ? <p>{t("entry.retrySafe")}</p> : null}
        </div>
      ) : null}

      <GoodsEditor
        drafts={goods}
        onChange={(next) => {
          setGoods(next);
          setChecked(false);
        }}
        showAllProblems={checked}
        disabled={pending}
      />

      <p className="difference" role="status">
        {missing === 0
          ? t("goods.later.match")
          : missing > 0
            ? t("goods.later.short", { amount: formatMoney(missing, language) })
            : t("goods.later.over", { amount: formatMoney(-missing, language) })}
      </p>

      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending || !ready}>
          {pending ? t("state.saving") : t("goods.later.submit")}
        </button>
        <Link to={back} className="button">
          {t("action.cancel")}
        </Link>
      </p>
    </form>
  );
}

/** Adds goods, once, to a credit sale that was recorded as an amount only (REQ-038). */
export function AddGoodsScreen({ customerId, entryId }: { customerId: string; entryId: string }) {
  const { api, now } = useWorkspace();
  const { t } = useI18n();
  const { state, reload } = useLoad((signal) => api.readCustomer(customerId, signal), [api, customerId]);

  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.code === "NOT_FOUND" ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  const customer = state.data;
  const entry = customer.entries.find((candidate) => candidate.id.toLowerCase() === entryId.toLowerCase());
  if (!entry) {
    return <NotFoundScreen />;
  }
  return (
    <>
      <h2 className="subject">{customer.displayName}</h2>
      {canAddGoods(entry, now()) ? (
        <AddGoodsForm customer={customer} entry={entry} />
      ) : (
        <>
          <p className="notice">{t("goods.later.closed")}</p>
          {entry.lines.length > 0 ? <GoodsList lines={entry.lines} /> : null}
          <p className="actions">
            <Link to={`/customers/${customer.id}`} className="button">
              {t("customer.open")}
            </Link>
          </p>
        </>
      )}
    </>
  );
}
