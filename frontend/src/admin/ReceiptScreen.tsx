import { type FormEvent, useState } from "react";

import { useI18n, type Translate } from "../i18n/I18nProvider";
import { type Column, DataTable } from "../panel/DataTable";
import { type ApiError, toApiError } from "../shared/api";
import { formatMoney } from "../shared/format";
import { useLoad, useSubmit } from "../shared/hooks";
import { dayText } from "../shared/promiseParts";
import { Link } from "../shared/router";
import { NotFoundScreen } from "../shared/screens";
import { Confirm, Failure, FieldError, formatInstant, Loading } from "../shared/workspace/parts";
import type { AdminApi, AdminReceiptDetail, DecidedReceipt, ReceiptCopy } from "./adminApi";
import "./messages";
import { actorName, cleanNote, cleanReason, parseReceiptMonths, REASON_MAX, REASON_MIN, RECEIPT_MONTHS_MAX } from "./rules";
import { known, NONE } from "./ShopsScreen";

type Decision = { kind: "approve"; months: number; note: string | null } | { kind: "reject"; reason: string };

/** "Decided already" in the administrator's words, by what the server says was decided. */
export function decidedText(error: ApiError, t: Translate): string | null {
  if (error.code !== "RECEIPT_ALREADY_DECIDED") {
    return null;
  }
  const status = error.fields["status"];
  return status === "approved" ? t("admin.rc.decided.approved") : status === "rejected" ? t("admin.rc.decided.rejected") : t("admin.rc.decided.other");
}

/**
 * The decision on a waiting receipt. Either way a form first, then a question that repeats what will be
 * sent; nothing is sent until "yes", and what is sent carries an Idempotency-Key.
 */
function Decide({
  api,
  receipt,
  onDecided,
  onAlready,
}: {
  api: AdminApi;
  receipt: AdminReceiptDetail;
  onDecided: (decided: DecidedReceipt) => void;
  /** Someone decided meanwhile, here or in the chat: told with the words for it. */
  onAlready: (said: string) => void;
}) {
  const { t, language } = useI18n();
  const [kind, setKind] = useState<Decision["kind"] | null>(null);
  // Prefilled with what the owner stated; the administrator corrects it when the transfer says otherwise.
  const [months, setMonths] = useState(receipt.statedMonths === null ? "" : String(receipt.statedMonths));
  const [text, setText] = useState("");
  const [problems, setProblems] = useState<{ months: string | null; text: string | null }>({ months: null, text: null });
  const [draft, setDraft] = useState<Decision | null>(null);
  const { state, submit, reset } = useSubmit((payload: Decision, key) => {
    const sent =
      payload.kind === "approve"
        ? api.approveReceipt(receipt.id, { months: payload.months, note: payload.note }, key)
        : api.rejectReceipt(receipt.id, payload.reason, key);
    return sent.then(onDecided, (error: unknown) => {
      const said = decidedText(toApiError(error), t);
      if (said !== null) {
        onAlready(said);
      }
      throw error;
    });
  });
  const failure = state.status === "error" ? state.error : null;
  const already = failure === null ? null : decidedText(failure, t);
  const amount = receipt.statedAmount === null ? NONE : formatMoney(receipt.statedAmount, language);

  if (already !== null) {
    // The screen says it above and reads the receipt again; there is nothing left to decide here.
    return null;
  }

  if (draft !== null) {
    return (
      <Confirm
        question={
          <>
            <p>
              {draft.kind === "approve"
                ? t("admin.rc.approve.confirm", { shop: receipt.shopName, amount, months: draft.months })
                : t("admin.rc.reject.confirm", { shop: receipt.shopName, amount })}
            </p>
            {draft.kind === "reject" ? <p>{t("admin.change.confirm.reason", { reason: draft.reason })}</p> : null}
            {draft.kind === "approve" && draft.note !== null ? <p>{t("admin.rc.confirm.note", { note: draft.note })}</p> : null}
            <p>{t("admin.rc.ownerTold")}</p>
          </>
        }
        yes={draft.kind === "approve" ? t("admin.rc.approve.yes") : t("admin.rc.reject.yes")}
        no={t("action.back")}
        pending={state.status === "pending"}
        error={failure}
        onYes={() => submit(draft)}
        onNo={() => {
          setDraft(null);
          reset();
        }}
      />
    );
  }

  if (kind === null) {
    return (
      <p className="actions">
        <button type="button" className="button button--primary" onClick={() => setKind("approve")}>
          {t("admin.rc.approve")}
        </button>
        <button type="button" className="button" onClick={() => setKind("reject")}>
          {t("admin.rc.reject")}
        </button>
      </p>
    );
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (kind === "approve") {
      const wanted = parseReceiptMonths(months);
      const note = cleanNote(text);
      setProblems({
        months: wanted === null ? t("admin.rc.months.invalid", { max: RECEIPT_MONTHS_MAX }) : null,
        text: note === false ? t("admin.rc.note.invalid", { min: REASON_MIN, max: REASON_MAX }) : null,
      });
      if (wanted !== null && note !== false) {
        reset();
        setDraft({ kind, months: wanted, note });
      }
      return;
    }
    const reason = cleanReason(text);
    setProblems({ months: null, text: reason === null ? t("admin.reason.invalid", { min: REASON_MIN, max: REASON_MAX }) : null });
    if (reason !== null) {
      reset();
      setDraft({ kind, reason });
    }
  };

  return (
    <form className="form notice" onSubmit={onSubmit} noValidate aria-label={kind === "approve" ? t("admin.rc.approve") : t("admin.rc.reject")}>
      {kind === "approve" ? (
        <div className="field">
          <label htmlFor="receipt-months">{t("admin.rc.approve.months")}</label>
          <input
            id="receipt-months"
            className="input"
            inputMode="numeric"
            autoComplete="off"
            maxLength={4}
            value={months}
            aria-invalid={problems.months !== null}
            aria-describedby="receipt-months-hint receipt-months-error"
            onChange={(event) => {
              setMonths(event.target.value);
              setProblems((current) => ({ ...current, months: null }));
            }}
          />
          <p className="field__hint" id="receipt-months-hint">
            {t("admin.rc.approve.months.hint", { max: RECEIPT_MONTHS_MAX })}
          </p>
          <FieldError id="receipt-months-error" message={problems.months} />
        </div>
      ) : null}
      <div className="field">
        <label htmlFor="receipt-text">{kind === "approve" ? t("admin.rc.note") : t("admin.reason")}</label>
        <textarea
          id="receipt-text"
          className="input input--text"
          rows={2}
          maxLength={2000}
          value={text}
          aria-invalid={problems.text !== null}
          aria-describedby="receipt-text-hint receipt-text-error"
          onChange={(event) => {
            setText(event.target.value);
            setProblems((current) => ({ ...current, text: null }));
          }}
        />
        <p className="field__hint" id="receipt-text-hint">
          {kind === "approve" ? t("admin.rc.note.hint") : t("admin.rc.reject.hint")}
        </p>
        <FieldError id="receipt-text-error" message={problems.text} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary">
          {t("admin.change.continue")}
        </button>
        <button
          type="button"
          className="button"
          onClick={() => {
            setKind(null);
            setText("");
            setProblems({ months: null, text: null });
          }}
        >
          {t("action.cancel")}
        </button>
      </p>
    </form>
  );
}

function Detail({ api, loaded, reload, onAlready }: { api: AdminApi; loaded: AdminReceiptDetail; reload: () => void; onAlready: (said: string) => void }) {
  const { t, language } = useI18n();
  const [decided, setDecided] = useState<DecidedReceipt | null>(null);
  // What the decision answered is newer than what was read; the file and the copies stay as read.
  const receipt = decided === null ? loaded : { ...loaded, ...decided };
  const copies: Column<ReceiptCopy>[] = [
    {
      id: "created",
      header: t("admin.receipts.created"),
      rowHeader: true,
      cell: (copy) => <Link to={`/receipts/${copy.id}`}>{formatInstant(copy.createdAt, language)}</Link>,
    },
    { id: "shop", header: t("admin.shops.name"), cell: (copy) => <Link to={`/shops/${copy.shopId}`}>{copy.shopName}</Link> },
    {
      id: "amount",
      header: t("admin.receipts.amount"),
      numeric: true,
      cell: (copy) => (copy.statedAmount === null ? NONE : formatMoney(copy.statedAmount, language)),
    },
    { id: "status", header: t("admin.receipts.status"), cell: (copy) => known("admin.receipt", copy.status, t) },
  ];

  return (
    <>
      <p>
        <Link to="/receipts">{t("admin.rc.back")}</Link>
      </p>
      {decided === null ? null : (
        <p className="notice notice--done" role="status">
          {decided.status === "rejected"
            ? t("admin.rc.rejected")
            : decided.subscription === null
              ? t("admin.rc.approved.plain")
              : t("admin.rc.approved", { date: dayText(decided.subscription.paidThrough, language) })}
        </p>
      )}
      <dl className="facts">
        <dt>{t("admin.shops.name")}</dt>
        <dd>
          <Link to={`/shops/${receipt.shopId}`}>{receipt.shopName}</Link>
        </dd>
        <dt>{t("admin.receipts.amount")}</dt>
        <dd>{receipt.statedAmount === null ? NONE : formatMoney(receipt.statedAmount, language)}</dd>
        <dt>{t("admin.queue.statedMonths")}</dt>
        <dd>{receipt.statedMonths ?? NONE}</dd>
        <dt>{t("admin.receipts.created")}</dt>
        <dd>{formatInstant(receipt.createdAt, language)}</dd>
        <dt>{t("admin.receipts.status")}</dt>
        <dd>{known("admin.receipt", receipt.status, t)}</dd>
        {receipt.decidedAt === null ? null : (
          <>
            <dt>{t("admin.receipts.decided")}</dt>
            <dd>{formatInstant(receipt.decidedAt, language)}</dd>
            <dt>{t("admin.rc.decidedBy")}</dt>
            <dd>
              {actorName(receipt.decidedBy, receipt.decidedByTgId, (id) => t("admin.actor.groupAdmin", { id }), NONE)}
            </dd>
          </>
        )}
        {receipt.months === null ? null : (
          <>
            <dt>{t("admin.rc.months")}</dt>
            <dd>{receipt.months}</dd>
          </>
        )}
        {receipt.rejectReason === null ? null : (
          <>
            <dt>{t("admin.receipts.rejectReason")}</dt>
            <dd>{receipt.rejectReason}</dd>
          </>
        )}
      </dl>

      <section aria-labelledby="receipt-file">
        <h2 id="receipt-file">{t("admin.rc.file")}</h2>
        {loaded.file === null ? (
          <p className="state">{loaded.hasFile ? t("admin.rc.file.gone") : t("admin.rc.file.none")}</p>
        ) : (
          // Two steps, as every file here: the link came with the session, and the person opens it.
          <div className="receipt">
            <p className="actions">
              <a className="button" href={loaded.file.url} target="_blank" rel="noopener noreferrer">
                {t("admin.rc.file.open")}
              </a>
            </p>
            <p className="row__meta">{t("admin.rc.file.valid", { date: formatInstant(loaded.file.expiresAt, language) })}</p>
          </div>
        )}
        {loaded.hasFile ? (
          <p className="actions">
            <button type="button" className="button button--small" onClick={reload}>
              {t("admin.rc.file.again")}
            </button>
          </p>
        ) : null}
      </section>

      {loaded.copies.length === 0 ? null : (
        <section aria-labelledby="receipt-copies">
          <h2 id="receipt-copies">{t("admin.rc.copies")}</h2>
          <p className="notice notice--error" role="note">
            {t("admin.rc.copies.warning")}
          </p>
          <DataTable caption={t("admin.rc.copies")} columns={copies} items={loaded.copies} rowKey={(copy) => copy.id} />
        </section>
      )}

      {receipt.status === "submitted" ? <Decide api={api} receipt={receipt} onDecided={setDecided} onAlready={onAlready} /> : null}
    </>
  );
}

/**
 * One receipt of a subscription payment (REQ-055): the shop, what its owner stated, the file, the other
 * receipts that carry the same file, and the decision. The server records that it was looked at.
 * Nothing of the shop's customers is here: the API gives none of it.
 */
export function ReceiptScreen({ api, receiptId }: { api: AdminApi; receiptId: string }) {
  const { state, reload } = useLoad((signal) => api.readReceipt(receiptId, signal), [api, receiptId]);
  // "Decided already" outlives the read that follows it: the receipt below then shows what was decided.
  const [already, setAlready] = useState<string | null>(null);
  if (state.status === "error") {
    return state.error.code === "NOT_FOUND" ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  return (
    <>
      {already === null ? null : (
        <p className="notice notice--error" role="alert">
          {already}
        </p>
      )}
      {state.status === "loading" ? (
        <Loading />
      ) : (
        <Detail
          key={state.data.id}
          api={api}
          loaded={state.data}
          reload={reload}
          onAlready={(said) => {
            setAlready(said);
            reload();
          }}
        />
      )}
    </>
  );
}
