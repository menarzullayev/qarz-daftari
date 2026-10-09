import { useState } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { Language } from "../../i18n/types";
import { formatMoney } from "../format";
import { usePagedList, useSubmit } from "../hooks";
import { UsersIcon } from "../icons";
import type { Column } from "../layout";
import { type Currency, parseMoney } from "../money";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useMay } from "../workspace/context";
import { Confirm, CurrencyToggle, Empty, errorText, Failure, FieldError, formatInstant, Loading, LoadMore } from "../workspace/parts";
import { CancelForm, DOCUMENT_KIND_LABELS, Fact, kindText, Listing, MethodChoice, NONE, useStock, useStockSettings } from "./parts";
import { MAX_NOTE, tidy } from "./quantity";
import type { PaymentMethod, Supplier, SupplierBalance, SupplierEntry, SupplierInput } from "./stockApi";

const MAX_NAME = 80;
/** One entry of a supplier's account, in the currency's minor unit (backend/src/qarz/domain/suppliers.py). */
const ENTRY_RANGE = { min: 1, max: 1_000_000_000_000 };

/**
 * What the shop and a supplier owe each other, a line for each currency: the two are never added.
 * Above zero the shop owes; below zero it paid ahead, and the line says so in words.
 */
export function balanceTexts(balances: readonly SupplierBalance[], language: Language, t: Translate): string[] {
  return balances
    .filter((row) => row.balance !== 0)
    .map((row) =>
      row.balance > 0
        ? t("supplier.owed", { amount: formatMoney(row.balance, language, row.currency) })
        : t("supplier.advance", { amount: formatMoney(-row.balance, language, row.currency) }),
    );
}

function Balances({ balances }: { balances: readonly SupplierBalance[] }) {
  const { t, language } = useI18n();
  const lines = balanceTexts(balances, language, t);
  if (lines.length === 0) {
    return <span>{t("supplier.settled")}</span>;
  }
  return (
    <>
      {lines.map((line) => (
        <span key={line} className="money stock-stack">
          {line}
        </span>
      ))}
    </>
  );
}

/** A supplier's name, phone and note: for a new one, or to change one. */
function SupplierForm({
  initial,
  pending,
  error,
  onSubmit,
  onClose,
}: {
  initial?: Supplier | undefined;
  pending: boolean;
  error: Parameters<typeof errorText>[0] | null;
  onSubmit: (input: SupplierInput) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [name, setName] = useState(initial?.name ?? "");
  const [phone, setPhone] = useState(initial?.phone ?? "");
  const [note, setNote] = useState(initial?.note ?? "");
  const [problem, setProblem] = useState<"name" | "note" | null>(null);
  const send = () => {
    const cleanName = tidy(name);
    const cleanNote = tidy(note);
    if (cleanName === null || [...cleanName].length > MAX_NAME) {
      setProblem("name");
      return;
    }
    if (cleanNote !== null && [...cleanNote].length > MAX_NOTE) {
      setProblem("note");
      return;
    }
    onSubmit({ name: cleanName, phone: tidy(phone), note: cleanNote });
  };
  return (
    <div className="form notice" role="group" aria-label={t(initial ? "supplier.edit" : "supplier.add")}>
      {error ? (
        <p className="field__error" role="alert">
          {errorText(error, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="supplier-name">{t("supplier.name")}</label>
        <input
          id="supplier-name"
          className="input"
          value={name}
          maxLength={200}
          autoComplete="off"
          disabled={pending}
          aria-invalid={problem === "name"}
          aria-describedby="supplier-name-error"
          onChange={(event) => {
            setName(event.target.value);
            setProblem(null);
          }}
        />
        <FieldError id="supplier-name-error" message={problem === "name" ? t("supplier.name.invalid", { max: MAX_NAME }) : null} />
      </div>
      <div className="field">
        <label htmlFor="supplier-phone">{t("supplier.phone")}</label>
        <input
          id="supplier-phone"
          className="input"
          type="tel"
          inputMode="tel"
          value={phone}
          maxLength={40}
          autoComplete="off"
          disabled={pending}
          onChange={(event) => setPhone(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="supplier-note">{t("supplier.note")}</label>
        <input
          id="supplier-note"
          className="input"
          value={note}
          maxLength={400}
          autoComplete="off"
          disabled={pending}
          aria-invalid={problem === "note"}
          aria-describedby="supplier-note-error"
          onChange={(event) => {
            setNote(event.target.value);
            setProblem(null);
          }}
        />
        <FieldError id="supplier-note-error" message={problem === "note" ? t("stock.doc.problem.note", { max: MAX_NOTE }) : null} />
      </div>
      <p className="actions">
        <button type="button" className="button button--primary" onClick={send} disabled={pending}>
          {pending ? t("state.saving") : t("action.save")}
        </button>
        <button type="button" className="button" onClick={onClose} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </div>
  );
}

/** The shop's suppliers with what is owed to each, and to all of them together. */
export function SuppliersScreen() {
  const { t, language } = useI18n();
  const can = useMay();
  const stock = useStock();
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"active" | "archived">("active");
  const [totals, setTotals] = useState<{ currency: Currency; owed: number }[]>([]);
  const [adding, setAdding] = useState(false);
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) =>
      stock.suppliers({ q: query, status, cursor }, signal).then((page) => {
        setTotals(page.totals);
        return { items: page.suppliers, nextCursor: page.nextCursor };
      }),
    [stock, query, status],
  );
  const create = useSubmit((input: SupplierInput, key) =>
    stock.createSupplier(input, key).then(() => {
      setAdding(false);
      reload();
    }),
  );

  const columns: Column<Supplier>[] = [
    {
      id: "supplier",
      header: t("supplier.name"),
      rowHeader: true,
      cell: (row) => <Link to={`/suppliers/${row.id}`}>{row.name}</Link>,
    },
    { id: "phone", header: t("supplier.phone"), cell: (row) => row.phone ?? NONE },
    { id: "balance", header: t("supplier.balance"), numeric: true, cell: (row) => <Balances balances={row.balances} /> },
  ];

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = <Empty icon={<UsersIcon />}>{query !== "" ? t("supplier.none.match") : t("supplier.none")}</Empty>;
  } else {
    body = (
      <>
        <Listing caption={t("nav.suppliers")} columns={columns} items={state.items} rowKey={(row) => row.id} />
        {state.nextCursor !== null ? <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} /> : null}
      </>
    );
  }

  return (
    <>
      <form
        className="search"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(text.trim());
        }}
      >
        <input
          type="search"
          className="input"
          value={text}
          maxLength={80}
          aria-label={t("supplier.search")}
          placeholder={t("supplier.search")}
          onChange={(event) => setText(event.target.value)}
        />
        <button type="submit" className="button">
          {t("action.search")}
        </button>
      </form>
      <div className="bar">
        <div className="toggle" role="group" aria-label={t("supplier.status")}>
          {(["active", "archived"] as const).map((option) => (
            <button
              key={option}
              type="button"
              className="toggle__option"
              aria-pressed={status === option}
              onClick={() => setStatus(option)}
            >
              {t(option === "active" ? "supplier.status.active" : "supplier.status.archived")}
            </button>
          ))}
        </div>
        {can("suppliers.manage") && !adding ? (
          <button type="button" className="button" onClick={() => setAdding(true)}>
            {t("supplier.add")}
          </button>
        ) : null}
      </div>
      {can("suppliers.manage") && adding ? (
        <SupplierForm
          pending={create.state.status === "pending"}
          error={create.state.status === "error" ? create.state.error : null}
          onSubmit={create.submit}
          onClose={() => {
            setAdding(false);
            create.reset();
          }}
        />
      ) : null}
      {/* One figure for each currency, side by side: so'm and dollars are never one sum. */}
      {totals.filter((total) => total.owed !== 0).length > 0 ? (
        <dl className="figures">
          {totals
            .filter((total) => total.owed !== 0)
            .map((total) => (
              <div key={total.currency} className="figure">
                <dt>{t("supplier.total")}</dt>
                <dd className="money">{formatMoney(total.owed, language, total.currency)}</dd>
              </div>
            ))}
        </dl>
      ) : null}
      {body}
    </>
  );
}

type EntryJob = { kind: "payment" | "opening"; amount: number; currency: Currency; note: string | null; method?: PaymentMethod };

/** A payment to the supplier, or what the shop already owed them before it kept this account. */
function EntryForm({
  supplierId,
  kind,
  currencies,
  cashBook,
  onDone,
  onClose,
}: {
  supplierId: string;
  kind: "payment" | "opening";
  currencies: readonly Currency[];
  /** The shop keeps a cash book: a payment is then asked how it was paid. */
  cashBook: boolean;
  onDone: () => void;
  onClose: () => void;
}) {
  const { t, language } = useI18n();
  const stock = useStock();
  const [currency, setCurrency] = useState<Currency>("UZS");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState(false);
  const [method, setMethod] = useState<PaymentMethod>("cash");
  // An old debt stated is no money paid: only a payment has a method.
  const asksMethod = cashBook && kind === "payment";
  const save = useSubmit((job: EntryJob, key) => stock.addEntry(supplierId, job, key).then(onDone));
  const pending = save.state.status === "pending";
  const parsed = parseMoney(amount, currency, ENTRY_RANGE);
  const send = () => {
    if (!parsed.ok) {
      setProblem(true);
      return;
    }
    const cleanNote = tidy(note);
    const job: EntryJob = { kind, amount: parsed.amount, currency, note: cleanNote === null ? null : cleanNote.slice(0, MAX_NOTE) };
    save.submit(asksMethod ? { ...job, method } : job);
  };
  const id = `supplier-${kind}`;
  return (
    <div className="form notice" role="group" aria-label={t(kind === "payment" ? "supplier.pay" : "supplier.opening")}>
      {save.state.status === "error" ? (
        <p className="field__error" role="alert">
          {errorText(save.state.error, t)}
        </p>
      ) : null}
      {kind === "opening" ? <p className="hint">{t("supplier.opening.hint")}</p> : null}
      {currencies.includes("USD") ? (
        <CurrencyToggle
          value={currency}
          disabled={pending}
          onChange={(next) => {
            setCurrency(next);
            setAmount("");
            setProblem(false);
          }}
        />
      ) : null}
      <div className="field">
        <label htmlFor={`${id}-amount`}>{t(kind === "payment" ? "supplier.pay.amount" : "supplier.opening.amount")}</label>
        <input
          id={`${id}-amount`}
          className="input input--amount"
          inputMode={currency === "USD" ? "decimal" : "numeric"}
          autoComplete="off"
          value={amount}
          disabled={pending}
          aria-invalid={problem}
          aria-describedby={`${id}-amount-error ${id}-amount-hint`}
          onChange={(event) => {
            setAmount(event.target.value);
            setProblem(false);
          }}
        />
        <p className="field__hint" id={`${id}-amount-hint`}>
          {parsed.ok ? formatMoney(parsed.amount, language, currency) : ""}
        </p>
        <FieldError id={`${id}-amount-error`} message={problem ? t("supplier.amount.invalid") : null} />
      </div>
      {asksMethod ? <MethodChoice id={`${id}-method`} value={method} disabled={pending} onChange={setMethod} /> : null}
      <div className="field">
        <label htmlFor={`${id}-note`}>{t("supplier.note")}</label>
        <input
          id={`${id}-note`}
          className="input"
          value={note}
          maxLength={MAX_NOTE}
          autoComplete="off"
          disabled={pending}
          onChange={(event) => setNote(event.target.value)}
        />
      </div>
      <p className="actions">
        <button type="button" className="button button--primary" onClick={send} disabled={pending}>
          {pending ? t("state.saving") : t(kind === "payment" ? "supplier.pay.submit" : "supplier.opening.submit")}
        </button>
        <button type="button" className="button" onClick={onClose} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </div>
  );
}

type Mode = "view" | "payment" | "opening" | "edit" | "archive";

/** One supplier: what is owed, the account entry by entry, and what may be recorded or taken back. */
export function SupplierScreen({ supplierId, office }: { supplierId: string; office: boolean }) {
  const { t, language } = useI18n();
  const can = useMay();
  const stock = useStock();
  const settings = useStockSettings();
  const [supplier, setSupplier] = useState<Supplier | null>(null);
  const [mode, setMode] = useState<Mode>("view");
  const [cancelling, setCancelling] = useState<string | null>(null);
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) =>
      stock.supplier(supplierId, cursor, signal).then((account) => {
        setSupplier(account.supplier);
        return { items: account.entries, nextCursor: account.nextCursor };
      }),
    [stock, supplierId],
  );
  const done = () => {
    setMode("view");
    setCancelling(null);
    reload();
  };
  const update = useSubmit((input: SupplierInput, key) => stock.updateSupplier(supplierId, input, key).then(done));
  const archive = useSubmit((archived: boolean, key) => stock.setArchived(supplierId, archived, key).then(done));
  const cancel = useSubmit((job: { entryId: string; reason: string }, key) =>
    stock.cancelEntry(supplierId, job.entryId, job.reason, key).then(done),
  );

  if (state.status === "loading" && supplier === null) {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.status === 404 ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  if (supplier === null) {
    return <Loading />;
  }
  const archived = supplier.status === "archived";
  const currencies = settings.state.status === "ready" ? settings.state.data.currencies : (["UZS"] as const);
  const cashBook = settings.state.status === "ready" && settings.state.data.cashBook;
  const entries = state.status === "ready" ? state.items : [];

  /** Whether "cancel" is offered for an entry: one a person recorded, still standing, by one who may. */
  const mayCancel = (entry: SupplierEntry) =>
    !entry.reversed &&
    entry.document === null &&
    ((entry.kind === "payment" && can("suppliers.pay")) || (entry.kind === "opening" && can("suppliers.manage")));

  const columns: Column<SupplierEntry>[] = [
    { id: "when", header: t("stock.col.when"), rowHeader: true, cell: (entry) => formatInstant(entry.createdAt, language) },
    {
      id: "what",
      header: t("stock.col.what"),
      cell: (entry) => (
        <>
          {kindText(entry.kind, "entry", t)}
          {entry.reversed ? ` · ${t("supplier.entry.cancelled")}` : ""}
        </>
      ),
    },
    {
      id: "amount",
      header: t("supplier.amount"),
      numeric: true,
      cell: (entry) => formatMoney(entry.amount, language, entry.currency),
    },
    {
      id: "document",
      header: t("stock.col.document"),
      cell: (entry) => {
        if (entry.document === null) {
          return null;
        }
        const kind = DOCUMENT_KIND_LABELS[entry.document.kind as keyof typeof DOCUMENT_KIND_LABELS];
        const name = t("stock.doc.ref", { kind: kind ? t(kind) : entry.document.kind, number: entry.document.number });
        // The documents are the web panel's screens; elsewhere the entry names its document without a link.
        return office ? <Link to={`/stock-documents/${entry.document.id}`}>{name}</Link> : name;
      },
    },
    { id: "note", header: t("supplier.note"), cell: (entry) => entry.note },
    {
      id: "actions",
      header: t("stock.col.actions"),
      cell: (entry) =>
        mayCancel(entry) && cancelling !== entry.id ? (
          <button type="button" className="button button--small" onClick={() => setCancelling(entry.id)}>
            {t("supplier.entry.cancel")}
          </button>
        ) : null,
    },
  ];

  return (
    <>
      <h2 className="subject">{supplier.name}</h2>
      <dl className="facts">
        <Fact name={t("supplier.balance")}>
          <Balances balances={supplier.balances} />
        </Fact>
        <Fact name={t("supplier.phone")}>{supplier.phone ?? NONE}</Fact>
        {supplier.note !== null ? <Fact name={t("supplier.note")}>{supplier.note}</Fact> : null}
        {archived ? <Fact name={t("supplier.status")}>{t("supplier.status.archived")}</Fact> : null}
      </dl>

      {mode === "view" ? (
        <p className="actions">
          {/* Paying is a permission of its own, and so is keeping the list: each button for those who hold it. */}
          {can("suppliers.pay") && !archived ? (
            <button type="button" className="button button--primary" onClick={() => setMode("payment")}>
              {t("supplier.pay")}
            </button>
          ) : null}
          {can("suppliers.manage") && !archived ? (
            <button type="button" className="button" onClick={() => setMode("opening")}>
              {t("supplier.opening")}
            </button>
          ) : null}
          {can("suppliers.manage") ? (
            <>
              <button type="button" className="button" onClick={() => setMode("edit")}>
                {t("supplier.edit")}
              </button>
              <button type="button" className="button" onClick={() => setMode("archive")}>
                {t(archived ? "supplier.unarchive" : "supplier.archive")}
              </button>
            </>
          ) : null}
        </p>
      ) : null}
      {mode === "payment" || mode === "opening" ? (
        <EntryForm key={mode} supplierId={supplierId} kind={mode} currencies={currencies} cashBook={cashBook} onDone={done} onClose={() => setMode("view")} />
      ) : null}
      {mode === "edit" ? (
        <SupplierForm
          initial={supplier}
          pending={update.state.status === "pending"}
          error={update.state.status === "error" ? update.state.error : null}
          onSubmit={update.submit}
          onClose={() => {
            setMode("view");
            update.reset();
          }}
        />
      ) : null}
      {mode === "archive" ? (
        <Confirm
          question={t(archived ? "supplier.unarchive.question" : "supplier.archive.question", { name: supplier.name })}
          yes={t(archived ? "supplier.unarchive" : "supplier.archive")}
          no={t("action.cancel")}
          pending={archive.state.status === "pending"}
          error={archive.state.status === "error" ? archive.state.error : null}
          onYes={() => archive.submit(!archived)}
          onNo={() => {
            setMode("view");
            archive.reset();
          }}
        />
      ) : null}

      <h3 className="section-label">{t("supplier.entries")}</h3>
      {entries.length === 0 && state.status === "ready" ? <p className="state">{t("supplier.entries.none")}</p> : null}
      {entries.length > 0 ? (
        <Listing
          caption={t("supplier.entries")}
          columns={columns}
          items={entries}
          rowKey={(entry) => entry.id}
          expanded={(entry) =>
            cancelling === entry.id ? (
              <CancelForm
                id={`supplier-cancel-${entry.id}`}
                label={t("supplier.entry.cancel.reason")}
                submitLabel={t("supplier.entry.cancel")}
                pending={cancel.state.status === "pending"}
                error={cancel.state.status === "error" ? cancel.state.error : null}
                onSubmit={(reason) => cancel.submit({ entryId: entry.id, reason })}
                onClose={() => {
                  setCancelling(null);
                  cancel.reset();
                }}
              />
            ) : null
          }
        />
      ) : null}
      {state.status === "ready" && state.nextCursor !== null ? (
        <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} />
      ) : null}
      <p className="actions">
        <Link to="/suppliers" className="button">
          {t("nav.suppliers")}
        </Link>
      </p>
    </>
  );
}
