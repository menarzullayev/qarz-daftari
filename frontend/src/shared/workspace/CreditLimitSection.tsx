import { useState, type FormEvent } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { AmountProblem, Currency } from "../money";
import { amountInput, formatDollars, formatUzs } from "../money";
import type { CreditSettings, Customer, CustomerPatch } from "../api";
import { formatMoney } from "../format";
import { useLoad, useSubmit } from "../hooks";
import { canManage } from "../navigation";
import { useWorkspace } from "./context";
import { limitIn, mayExceed, parseLimit } from "./creditRules";
import { errorText, Failure, FieldError, Loading } from "./parts";

type Bounds = CreditSettings["bounds"];

/** The heading of the dollar limit, which also names its group and its form. */
const USD_TITLE = "credit-usd-title";

/** The range of a limit in the words of its currency: "1 000 so'mdan ...", or "1.00 $ dan 1 000 000.00 $ gacha". */
export function limitRange(bounds: Bounds, t: Translate, currency: Currency = "UZS"): string {
  return currency === "USD"
    ? t("credit.limit.usd.range", { min: formatDollars(bounds.min), max: formatDollars(bounds.max) })
    : t("credit.limit.range", { min: formatUzs(bounds.min), max: formatUzs(bounds.max) });
}

/** Text for a limit that cannot be read or does not fit the server's bounds. */
export function limitMessage(problem: AmountProblem, bounds: Bounds, t: Translate, currency: Currency = "UZS"): string {
  switch (problem) {
    case "empty":
      return t("credit.limit.required");
    case "too_small":
    case "too_large":
      return limitRange(bounds, t, currency);
    case "not_whole":
    case "invalid":
      return t(currency === "USD" ? "credit.limit.usd.invalid" : "credit.limit.invalid");
  }
}

function LimitForm({
  customer,
  own,
  bounds,
  currency,
  onSaved,
  onCancel,
}: {
  customer: Customer;
  /** The customer's own limit in this currency, or null when the shop's default applies. */
  own: number | null;
  bounds: Bounds;
  currency: Currency;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const inDollars = currency === "USD";
  const [text, setText] = useState(own === null ? "" : amountInput(own, currency));
  const [problem, setProblem] = useState<string | null>(null);
  // One action, one key: setting a limit and removing it are different payloads, so different keys.
  const { state, submit } = useSubmit((limit: number | null, key) => {
    // Each limit is a field of its own: the so'm limit is never sent as dollars, nor the other way.
    const patch: CustomerPatch = inDollars ? { creditLimitUsd: limit } : { creditLimit: limit };
    return api.updateCustomer(customer.id, patch, key).then(onSaved);
  });

  const parsed = parseLimit(text, bounds, currency);
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!parsed.ok) {
      setProblem(limitMessage(parsed.problem, bounds, t, currency));
      return;
    }
    if (parsed.amount === own) {
      onCancel(); // nothing changed, and the server refuses an empty change
      return;
    }
    submit(parsed.amount);
  };

  const failure = state.status === "error" ? state.error : null;
  const refused =
    failure?.code === "VALIDATION" && (inDollars ? "credit_limit_usd" : "credit_limit") in failure.fields
      ? limitMessage("too_small", bounds, t, currency)
      : null;
  const shown = problem ?? refused;
  const pending = state.status === "pending";
  // The so'm form keeps the ids it always had; the dollar form has its own beside it.
  const id = inDollars ? "credit-limit-usd" : "credit-limit";

  return (
    <form
      className="form"
      onSubmit={onSubmit}
      noValidate
      // The dollar form is named by the heading it stands under; the so'm form keeps the name it had.
      {...(inDollars ? { "aria-labelledby": USD_TITLE } : { "aria-label": t("credit.title") })}
    >
      {failure ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor={id}>{t(inDollars ? "credit.limit.usd.field" : "credit.limit.field")}</label>
        <input
          id={id}
          className="input input--amount"
          inputMode={inDollars ? "decimal" : "numeric"}
          autoComplete="off"
          value={text}
          aria-invalid={shown !== null}
          aria-describedby={`${id}-error ${id}-hint`}
          onChange={(event) => {
            setText(event.target.value);
            setProblem(null);
          }}
        />
        <p className="field__hint" id={`${id}-hint`}>
          {parsed.ok ? formatMoney(parsed.amount, language, currency) : limitRange(bounds, t, currency)}
        </p>
        <FieldError id={`${id}-error`} message={shown} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("credit.limit.save")}
        </button>
        {own !== null ? (
          <button type="button" className="button" onClick={() => submit(null)} disabled={pending}>
            {t("credit.limit.remove")}
          </button>
        ) : null}
        <button type="button" className="button" onClick={onCancel} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </form>
  );
}

/** The limit of one currency: what applies, and for a manager or an owner the form that changes it. */
function Limit({
  customer,
  settings,
  currency,
  onSaved,
}: {
  customer: Customer;
  settings: CreditSettings;
  currency: Currency;
  onSaved: () => void;
}) {
  const { role } = useWorkspace();
  const { t, language } = useI18n();
  const [editing, setEditing] = useState(false);
  const figures = limitIn(currency, customer, settings);
  const bounds = currency === "USD" ? settings.usd?.bounds : settings.bounds;
  if (figures === null || bounds === undefined) {
    return null;
  }
  const { own, limit } = figures;
  return (
    <>
      <p>
        {limit === null
          ? t("credit.limit.none")
          : t(own !== null ? "credit.limit.own" : "credit.limit.default", {
              amount: formatMoney(limit, language, currency),
            })}
      </p>
      {limit !== null && !mayExceed(role, settings) ? <p className="hint">{t("credit.limit.sellersStopped")}</p> : null}
      {!canManage(role) ? null : editing ? (
        <LimitForm
          customer={customer}
          own={own}
          bounds={bounds}
          currency={currency}
          onSaved={() => {
            // Closed here, not by the reload: a fast answer may never show the loading state between.
            setEditing(false);
            onSaved();
          }}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <p className="actions">
          <button type="button" className="button" onClick={() => setEditing(true)}>
            {t(own === null ? "credit.limit.set" : "credit.limit.change")}
          </button>
        </p>
      )}
    </>
  );
}

/**
 * The credit limit that applies to a customer (REQ-044): their own, else the shop's default. Every
 * member of staff sees it; a manager or an owner sets, changes or removes the customer's own. In a shop
 * that works in dollars the dollar debt has a limit of its own under the so'm one: the so'm limit says
 * nothing about dollars, and the two are set apart.
 */
export function CreditLimitSection({ customer, onSaved }: { customer: Customer; onSaved: () => void }) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const { state, reload } = useLoad((signal) => api.readCreditSettings(signal), [api]);

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else {
    const settings = state.data;
    body = (
      <>
        <Limit customer={customer} settings={settings} currency="UZS" onSaved={onSaved} />
        {customer.usd && settings.usd ? (
          <div role="group" aria-labelledby={USD_TITLE}>
            <h3 id={USD_TITLE}>{t("currency.usd.title")}</h3>
            <Limit customer={customer} settings={settings} currency="USD" onSaved={onSaved} />
          </div>
        ) : null}
      </>
    );
  }

  return (
    <section aria-labelledby="credit-title">
      <h2 id="credit-title">{t("credit.title")}</h2>
      {body}
    </section>
  );
}
