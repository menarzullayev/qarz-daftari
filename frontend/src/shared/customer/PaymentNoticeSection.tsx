import { useState, type FormEvent } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import { type AccountApi, type ApiError, type PaymentNotice, RECEIPT_MAX_BYTES, RECEIPT_TYPES } from "../api";
import { formatMoney } from "../format";
import { useSubmit } from "../hooks";
import { parseAmount } from "../money";
import { amountMessage } from "../workspace/EntryScreen";
import { errorText, FieldError, formatInstant } from "../workspace/parts";

/** How many of a customer's notices may wait for the shop at once (backend/src/qarz/domain/payment_notices.py). */
export const MAX_OPEN_NOTICES = 3;

const MEGABYTE = 1024 * 1024;
const RECEIPT_MAX_MB = RECEIPT_MAX_BYTES / MEGABYTE;

/** What is wrong with a receipt: the server's four words (`fields.receipt`), which this client also finds first. */
export type ReceiptProblem = "empty" | "too_large" | "type" | "malformed";

const RECEIPT_TEXTS: Readonly<Record<ReceiptProblem, MessageKey>> = {
  empty: "my.notice.receipt.empty",
  too_large: "my.notice.receipt.tooLarge",
  type: "my.notice.receipt.type",
  malformed: "my.notice.receipt.malformed",
};

function receiptText(problem: ReceiptProblem, t: Translate): string {
  return t(RECEIPT_TEXTS[problem], problem === "too_large" ? { size: RECEIPT_MAX_MB } : {});
}

/**
 * What can be told about a chosen file before it is sent. Whether it is a whole, well-formed image or
 * PDF only the server can say: it decides from the bytes, not from the name or the type given here.
 */
export function receiptProblem(file: { size: number; type: string }): ReceiptProblem | null {
  if (file.size === 0) {
    return "empty";
  }
  if (file.size > RECEIPT_MAX_BYTES) {
    return "too_large";
  }
  return RECEIPT_TYPES.includes(file.type) ? null : "type";
}

type Refused = { amount: string | null; receipt: string | null; detail: string | null };

/** Places a server refusal next to the field it is about, in the customer's words. */
function refusedFields(error: ApiError | null, t: Translate): Refused {
  const nothing: Refused = { amount: null, receipt: null, detail: null };
  if (error === null) {
    return nothing;
  }
  switch (error.code) {
    case "EXCEEDS_BALANCE":
      return { ...nothing, amount: t("entry.amount.exceedsBalance") };
    // A body the server would not even read: only a receipt can make it that large.
    case "BODY_TOO_LARGE":
      return { ...nothing, receipt: receiptText("too_large", t) };
    case "PAYMENT_NOTICE_NOT_ALLOWED":
      return error.fields["reason"] === "too_many_open"
        ? { ...nothing, detail: t("my.notice.tooManyOpen", { max: MAX_OPEN_NOTICES }) }
        : nothing;
    case "FILE_STORE_UNAVAILABLE":
      return { ...nothing, detail: t("my.notice.storeDown") };
    case "VALIDATION": {
      const receipt = error.fields["receipt"];
      const known = receipt !== undefined && receipt in RECEIPT_TEXTS ? receiptText(receipt as ReceiptProblem, t) : null;
      return {
        amount: "amount" in error.fields ? amountMessage("invalid", t) : null,
        // A word about the receipt this client does not know is still about the receipt.
        receipt: known ?? (receipt !== undefined ? errorText(error, t) : null),
        detail: null,
      };
    }
    default:
      return nothing;
  }
}

function NoticeForm({
  api,
  balance,
  onSent,
  onCancel,
}: {
  api: AccountApi;
  balance: number;
  onSent: (notice: PaymentNotice) => void;
  onCancel: () => void;
}) {
  const { t, language } = useI18n();
  const [amount, setAmount] = useState("");
  const [receipt, setReceipt] = useState<File | null>(null);
  const [problems, setProblems] = useState<{ amount: string | null; receipt: string | null }>({ amount: null, receipt: null });
  // The file is not part of what `useSubmit` compares, so a changed file is told to it by name and size.
  const { state, submit } = useSubmit((payload: { amount: number; file: string | null }, key) =>
    api.sendPaymentNotice(payload.amount, receipt, key).then(onSent),
  );

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const parsed = parseAmount(amount);
    const tooMuch = parsed.ok && parsed.amount > balance;
    const fileProblem = receipt === null ? null : receiptProblem(receipt);
    const found = {
      amount: !parsed.ok ? amountMessage(parsed.problem, t) : tooMuch ? t("entry.amount.exceedsBalance") : null,
      receipt: fileProblem === null ? null : receiptText(fileProblem, t),
    };
    setProblems(found);
    if (parsed.ok && !tooMuch && fileProblem === null) {
      submit({ amount: parsed.amount, file: receipt === null ? null : `${receipt.name}/${receipt.size}/${receipt.lastModified}` });
    }
  };

  const failure = state.status === "error" ? state.error : null;
  const refused = refusedFields(failure, t);
  const shown = { amount: problems.amount ?? refused.amount, receipt: problems.receipt ?? refused.receipt };
  const general = failure !== null && refused.amount === null && refused.receipt === null;
  const pending = state.status === "pending";

  return (
    <form className="form notice" onSubmit={onSubmit} noValidate>
      {general ? (
        <div role="alert">
          <p className="field__error">{errorText(failure, t)}</p>
          {refused.detail ? <p className="field__error">{refused.detail}</p> : null}
        </div>
      ) : null}
      <p>{t("my.notice.hint")}</p>
      <div className="field">
        <label htmlFor="notice-amount">{t("my.notice.amount")}</label>
        <input
          id="notice-amount"
          className="input input--amount"
          inputMode="numeric"
          autoComplete="off"
          value={amount}
          aria-invalid={shown.amount !== null}
          aria-describedby="notice-amount-hint notice-amount-error"
          onChange={(event) => {
            setAmount(event.target.value);
            setProblems((current) => ({ ...current, amount: null }));
          }}
        />
        <p className="field__hint" id="notice-amount-hint">
          {t("my.notice.amount.max", { amount: formatMoney(balance, language) })}
        </p>
        <FieldError id="notice-amount-error" message={shown.amount} />
      </div>
      <div className="field">
        <label htmlFor="notice-receipt">{t("my.notice.receipt")}</label>
        <input
          id="notice-receipt"
          type="file"
          className="input"
          accept={RECEIPT_TYPES.join(",")}
          aria-invalid={shown.receipt !== null}
          aria-describedby="notice-receipt-hint notice-receipt-error"
          onChange={(event) => {
            setReceipt(event.target.files?.[0] ?? null);
            setProblems((current) => ({ ...current, receipt: null }));
          }}
        />
        <p className="field__hint" id="notice-receipt-hint">
          {t("my.notice.receipt.hint", { size: RECEIPT_MAX_MB })}
        </p>
        <FieldError id="notice-receipt-error" message={shown.receipt} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("my.notice.submit")}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </form>
  );
}

/** What became of one notice, in the customer's words. */
function NoticeRow({ notice }: { notice: PaymentNotice }) {
  const { t, language } = useI18n();
  let state: string;
  switch (notice.status) {
    case "sent":
      state = t("my.notice.state.sent", { date: formatInstant(notice.expiresAt, language) });
      break;
    case "accepted":
      // The shop may have recorded another amount than the one stated: then the customer is told which.
      state =
        notice.recordedAmount !== null && notice.recordedAmount !== notice.amount
          ? t("my.notice.state.corrected", { recorded: formatMoney(notice.recordedAmount, language) })
          : t("my.notice.state.accepted");
      break;
    case "declined":
      state = t("my.notice.state.declined");
      break;
    case "expired":
      state = t("my.notice.state.expired");
      break;
    default:
      state = t("my.notice.state.other");
  }
  return (
    <li className="row">
      <p className="row__link">
        <span className="row__name">{formatInstant(notice.createdAt, language)}</span>
        <span className="row__amount">{formatMoney(notice.amount, language)}</span>
      </p>
      <p className="row__note">{state}</p>
      {notice.status === "declined" && notice.declineReason ? (
        <p className="row__note">{t("my.notice.declineReason", { reason: notice.declineReason })}</p>
      ) : null}
      {notice.hasReceipt ? <p className="row__meta">{t("my.notice.withReceipt")}</p> : null}
    </li>
  );
}

/**
 * "I have paid" on the customer's own page (REQ-060): an amount no larger than what is owed, a receipt
 * if there is one, and the customer's recent notices with what became of each. A notice never changes
 * the balance: only the shop's answer does.
 */
export function PaymentNoticeSection({
  api,
  balance,
  notices,
  onSent,
}: {
  api: AccountApi;
  balance: number;
  notices: readonly PaymentNotice[];
  /** Called once a notice is sent, to read the account again. */
  onSent: () => void;
}) {
  const { t, language } = useI18n();
  const [open, setOpen] = useState(false);
  const [sent, setSent] = useState<PaymentNotice | null>(null);
  // Nothing is owed, so there is nothing to have paid; and the shop takes three waiting notices at most.
  const waiting = notices.filter((notice) => notice.status === "sent").length;
  const offered = balance > 0 && waiting < MAX_OPEN_NOTICES;

  if (!offered && notices.length === 0 && sent === null) {
    return null;
  }
  return (
    <section aria-labelledby="my-notices-title">
      <h2 id="my-notices-title">{t("my.notices")}</h2>
      {sent ? (
        <p className="notice notice--done" role="status">
          {t("my.notice.sent", { amount: formatMoney(sent.amount, language) })}
        </p>
      ) : null}
      {balance > 0 && waiting >= MAX_OPEN_NOTICES ? (
        <p className="notice">{t("my.notice.tooManyOpen", { max: MAX_OPEN_NOTICES })}</p>
      ) : null}
      {!offered ? null : open ? (
        <NoticeForm
          api={api}
          balance={balance}
          onSent={(notice) => {
            setOpen(false);
            setSent(notice);
            onSent();
          }}
          onCancel={() => setOpen(false)}
        />
      ) : (
        <p className="actions">
          <button
            type="button"
            className="button button--primary"
            onClick={() => {
              setOpen(true);
              setSent(null);
            }}
          >
            {t("my.notice.ask")}
          </button>
        </p>
      )}
      {notices.length > 0 ? (
        <ul className="rows">
          {notices.map((notice) => (
            <NoticeRow key={notice.id} notice={notice} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}
