import { type FormEvent, type ReactNode, useMemo, useState } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import { type ApiError, RECEIPT_MAX_BYTES, RECEIPT_TYPES } from "../api";
import { receiptProblem, type ReceiptProblem } from "../customer/PaymentNoticeSection";
import { formatMoney } from "../format";
import { useLoad, useSubmit } from "../hooks";
import { type Column, useDesktop } from "../layout";
import { parseWholeUzs } from "../money";
import { useWorkspace } from "../workspace/context";
import { Empty, errorText, Failure, FieldError, formatInstant, Loading } from "../workspace/parts";
import "./messages";
import { MAX_AMOUNT, MAX_MONTHS, MAX_WAITING, MIN_AMOUNT, type OwnReceipt, parseMonths, type ReceiptsApi, receiptsOf } from "./receiptsApi";

const MAX_MB = RECEIPT_MAX_BYTES / (1024 * 1024);
const NONE = "—";
const FILE_WORDS: readonly string[] = ["empty", "too_large", "type", "malformed"];

function fileText(problem: ReceiptProblem, t: Translate): string {
  switch (problem) {
    case "empty":
      return t("my.notice.receipt.empty");
    case "too_large":
      return t("my.notice.receipt.tooLarge", { size: MAX_MB });
    case "type":
      return t("my.notice.receipt.type");
    case "malformed":
      return t("my.notice.receipt.malformed");
  }
}

type Fields = { months: string | null; amount: string | null; file: string | null };
const NOTHING: Fields = { months: null, amount: null, file: null };

/** What the price makes of the months, as the amount field shows it; empty while the months do not fit. */
export function computedAmount(priceUzs: number, monthsText: string): string {
  const months = parseMonths(monthsText);
  return months === null ? "" : String(priceUzs * months);
}

function Form({ client, priceUzs, onSent }: { client: ReceiptsApi; priceUzs: number; onSent: () => void }) {
  const { t, language } = useI18n();
  const [months, setMonths] = useState("1");
  // Null: the amount follows the months and the price. Once the person types one, it is theirs.
  const [typedAmount, setTypedAmount] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [problems, setProblems] = useState<Fields>(NOTHING);
  const amount = typedAmount ?? computedAmount(priceUzs, months);
  // The file is not part of what `useSubmit` compares, so a changed file is told to it by name and size.
  const { state, submit } = useSubmit((payload: { amount: number; months: number; file: string }, key) => {
    if (file === null) {
      throw new RangeError("no file");
    }
    return client.submit({ amount: payload.amount, months: payload.months, receipt: file }, key).then(onSent);
  });
  const monthsMessage = t("receipts.months.invalid", { max: MAX_MONTHS });
  const amountMessage = t("receipts.amount.invalid", { min: formatMoney(MIN_AMOUNT, language), max: formatMoney(MAX_AMOUNT, language) });

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const wantedMonths = parseMonths(months);
    const wantedAmount = parseWholeUzs(amount, MIN_AMOUNT, MAX_AMOUNT);
    const fileProblem = file === null ? null : receiptProblem(file);
    const found: Fields = {
      months: wantedMonths === null ? monthsMessage : null,
      amount: wantedAmount.ok ? null : amountMessage,
      file: file === null ? t("receipts.file.required") : fileProblem === null ? null : fileText(fileProblem, t),
    };
    setProblems(found);
    if (wantedMonths !== null && wantedAmount.ok && file !== null && fileProblem === null) {
      submit({ amount: wantedAmount.amount, months: wantedMonths, file: `${file.name}/${file.size}/${file.lastModified}` });
    }
  };

  // A refusal of the server goes next to the field it is about; one about none of them is said above.
  const failure: ApiError | null = state.status === "error" ? state.error : null;
  let refused: Fields = NOTHING;
  let detail: string | null = null;
  if (failure?.code === "VALIDATION") {
    const word = failure.fields["receipt"];
    refused = {
      months: "months" in failure.fields ? monthsMessage : null,
      amount: "amount" in failure.fields ? amountMessage : null,
      file:
        word === undefined
          ? null
          : word === "required"
            ? t("receipts.file.required")
            : FILE_WORDS.includes(word)
              ? fileText(word as ReceiptProblem, t)
              : errorText(failure, t),
    };
  } else if (failure?.code === "BODY_TOO_LARGE") {
    refused = { ...NOTHING, file: fileText("too_large", t) };
  } else if (failure?.code === "SUBSCRIPTION_RECEIPT_NOT_ALLOWED" && failure.fields["reason"] === "too_many_waiting") {
    detail = t("receipts.tooManyWaiting", { max: MAX_WAITING });
  } else if (failure?.code === "FILE_STORE_UNAVAILABLE") {
    detail = t("receipts.storeDown");
  }
  const shown: Fields = {
    months: problems.months ?? refused.months,
    amount: problems.amount ?? refused.amount,
    file: problems.file ?? refused.file,
  };
  const general = failure !== null && refused.months === null && refused.amount === null && refused.file === null;
  const pending = state.status === "pending";
  const computed = computedAmount(priceUzs, months);

  return (
    <form className="form" onSubmit={onSubmit} noValidate aria-label={t("receipts.title")}>
      {general ? (
        <p className="notice notice--error" role="alert">
          {detail ?? errorText(failure, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="receipt-months">{t("receipts.months")}</label>
        <input
          id="receipt-months"
          className="input"
          inputMode="numeric"
          autoComplete="off"
          maxLength={4}
          value={months}
          aria-invalid={shown.months !== null}
          aria-describedby="receipt-months-hint receipt-months-error"
          onChange={(event) => {
            setMonths(event.target.value);
            setProblems((current) => ({ ...current, months: null }));
          }}
        />
        <p className="field__hint" id="receipt-months-hint">
          {t("receipts.months.hint", { max: MAX_MONTHS })}
        </p>
        <FieldError id="receipt-months-error" message={shown.months} />
      </div>
      <div className="field">
        <label htmlFor="receipt-amount">{t("receipts.amount")}</label>
        <input
          id="receipt-amount"
          className="input input--amount"
          inputMode="numeric"
          autoComplete="off"
          value={amount}
          aria-invalid={shown.amount !== null}
          aria-describedby="receipt-amount-hint receipt-amount-error"
          onChange={(event) => {
            setTypedAmount(event.target.value);
            setProblems((current) => ({ ...current, amount: null }));
          }}
        />
        {computed === "" ? null : (
          <p className="field__hint" id="receipt-amount-hint">
            {t("receipts.amount.hint", { months: months.trim(), amount: formatMoney(Number(computed), language) })}
          </p>
        )}
        <FieldError id="receipt-amount-error" message={shown.amount} />
      </div>
      <div className="field">
        <label htmlFor="receipt-file">{t("receipts.file")}</label>
        <input
          id="receipt-file"
          type="file"
          className="input"
          accept={RECEIPT_TYPES.join(",")}
          aria-invalid={shown.file !== null}
          aria-describedby="receipt-file-hint receipt-file-error"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setProblems((current) => ({ ...current, file: null }));
          }}
        />
        <p className="field__hint" id="receipt-file-hint">
          {t("my.notice.receipt.hint", { size: MAX_MB })}
        </p>
        <FieldError id="receipt-file-error" message={shown.file} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("receipts.send")}
        </button>
      </p>
    </form>
  );
}

/** What became of one receipt, in the owner's words; a state this client does not know keeps the server's word. */
export function receiptState(receipt: OwnReceipt, t: Translate): string {
  switch (receipt.status) {
    case "submitted":
      return t("receipts.state.submitted");
    case "approved":
      return receipt.months === null ? t("receipts.state.approved.plain") : t("receipts.state.approved", { months: receipt.months });
    case "rejected":
      return receipt.rejectReason === null ? t("receipts.state.rejected.plain") : t("receipts.state.rejected", { reason: receipt.rejectReason });
    default:
      return receipt.status;
  }
}

/**
 * Paying the subscription by card transfer (REQ-054, REQ-055): the owner says for how many months and
 * how much was paid, attaches the receipt, and sees what became of the receipts sent before. The
 * subscription screen shows this to the owner alone; the server answers nobody else.
 */
export default function ReceiptSection({ priceUzs }: { priceUzs: number }) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const desktop = useDesktop();
  const client = useMemo(() => receiptsOf(api), [api]);
  const { state, reload } = useLoad((signal) => client.list(signal), [client]);
  // A sent receipt empties the form: the next one is another action, with another key.
  const [sent, setSent] = useState(0);
  const money = (amount: number | null) => (amount === null ? NONE : formatMoney(amount, language));

  let history: ReactNode;
  if (state.status === "loading") {
    history = <Loading />;
  } else if (state.status === "error") {
    history = <Failure error={state.error} onRetry={reload} />;
  } else if (state.data.length === 0) {
    history = <Empty>{t("receipts.none")}</Empty>;
  } else if (desktop) {
    const columns: Column<OwnReceipt>[] = [
      { id: "sent", header: t("receipts.col.sent"), rowHeader: true, cell: (receipt) => formatInstant(receipt.createdAt, language) },
      { id: "amount", header: t("receipts.col.amount"), numeric: true, cell: (receipt) => money(receipt.statedAmount) },
      { id: "months", header: t("receipts.col.months"), numeric: true, cell: (receipt) => receipt.statedMonths ?? NONE },
      { id: "state", header: t("receipts.col.state"), cell: (receipt) => receiptState(receipt, t) },
    ];
    history = <desktop.Table caption={t("receipts.history")} columns={columns} items={state.data} rowKey={(receipt) => receipt.id} />;
  } else {
    history = (
      <ul className="rows" aria-label={t("receipts.history")}>
        {state.data.map((receipt) => (
          <li key={receipt.id} className="row">
            <p className="row__link">
              <span className="row__name">{formatInstant(receipt.createdAt, language)}</span>
              <span className="row__amount">{money(receipt.statedAmount)}</span>
            </p>
            {receipt.statedMonths === null ? null : <p className="row__meta">{t("receipts.stated", { count: receipt.statedMonths })}</p>}
            <p className="row__meta">{receiptState(receipt, t)}</p>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <>
      <section aria-labelledby="receipts-title">
        <h2 id="receipts-title">{t("receipts.title")}</h2>
        <p>{t("receipts.explain")}</p>
        {sent > 0 ? (
          <p className="notice notice--done" role="status">
            {t("receipts.sent")}
          </p>
        ) : null}
        <Form
          key={sent}
          client={client}
          priceUzs={priceUzs}
          onSent={() => {
            setSent((count) => count + 1);
            reload();
          }}
        />
      </section>
      <section aria-labelledby="receipts-history">
        <h2 id="receipts-history">{t("receipts.history")}</h2>
        {history}
      </section>
    </>
  );
}
