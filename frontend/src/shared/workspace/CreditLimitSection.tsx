import { useState, type FormEvent } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { AmountProblem } from "../money";
import { formatUzs } from "../money";
import type { CreditSettings, Customer } from "../api";
import { formatMoney } from "../format";
import { useLoad, useSubmit } from "../hooks";
import { useMay, useWorkspace } from "./context";
import { effectiveLimit, mayExceed, parseLimit } from "./creditRules";
import { errorText, Failure, FieldError, Loading } from "./parts";

/** Text for a limit that cannot be read or does not fit the server's bounds. */
export function limitMessage(problem: AmountProblem, bounds: CreditSettings["bounds"], t: Translate): string {
  switch (problem) {
    case "empty":
      return t("credit.limit.required");
    case "too_small":
    case "too_large":
      return t("credit.limit.range", { min: formatUzs(bounds.min), max: formatUzs(bounds.max) });
    case "not_whole":
    case "invalid":
      return t("credit.limit.invalid");
  }
}

function LimitForm({
  customer,
  bounds,
  onSaved,
  onCancel,
}: {
  customer: Customer;
  bounds: CreditSettings["bounds"];
  onSaved: () => void;
  onCancel: () => void;
}) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const [text, setText] = useState(customer.creditLimit === null ? "" : formatUzs(customer.creditLimit));
  const [problem, setProblem] = useState<string | null>(null);
  // One action, one key: setting a limit and removing it are different payloads, so different keys.
  const { state, submit } = useSubmit((creditLimit: number | null, key) =>
    api.updateCustomer(customer.id, { creditLimit }, key).then(onSaved),
  );

  const parsed = parseLimit(text, bounds);
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!parsed.ok) {
      setProblem(limitMessage(parsed.problem, bounds, t));
      return;
    }
    if (parsed.amount === customer.creditLimit) {
      onCancel(); // nothing changed, and the server refuses an empty change
      return;
    }
    submit(parsed.amount);
  };

  const failure = state.status === "error" ? state.error : null;
  const refused =
    failure?.code === "VALIDATION" && "credit_limit" in failure.fields ? limitMessage("too_small", bounds, t) : null;
  const shown = problem ?? refused;
  const pending = state.status === "pending";

  return (
    <form className="form" onSubmit={onSubmit} noValidate aria-label={t("credit.title")}>
      {failure ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="credit-limit">{t("credit.limit.field")}</label>
        <input
          id="credit-limit"
          className="input input--amount"
          inputMode="numeric"
          autoComplete="off"
          value={text}
          aria-invalid={shown !== null}
          aria-describedby="credit-limit-error credit-limit-hint"
          onChange={(event) => {
            setText(event.target.value);
            setProblem(null);
          }}
        />
        <p className="field__hint" id="credit-limit-hint">
          {parsed.ok
            ? formatMoney(parsed.amount, language)
            : t("credit.limit.range", { min: formatUzs(bounds.min), max: formatUzs(bounds.max) })}
        </p>
        <FieldError id="credit-limit-error" message={shown} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("credit.limit.save")}
        </button>
        {customer.creditLimit !== null ? (
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

/**
 * The credit limit that applies to a customer (REQ-044): their own, else the shop's default. Every
 * member of staff sees it; a manager or an owner sets, changes or removes the customer's own.
 */
export function CreditLimitSection({ customer, onSaved }: { customer: Customer; onSaved: () => void }) {
  const { api, role, permissions } = useWorkspace();
  const can = useMay();
  const { t, language } = useI18n();
  const { state, reload } = useLoad((signal) => api.readCreditSettings(signal), [api]);
  const [editing, setEditing] = useState(false);

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else {
    const settings = state.data;
    const limit = effectiveLimit(customer.creditLimit, settings.defaultLimit);
    body = (
      <>
        <p>
          {limit === null
            ? t("credit.limit.none")
            : t(customer.creditLimit !== null ? "credit.limit.own" : "credit.limit.default", {
                amount: formatMoney(limit, language),
              })}
        </p>
        {limit !== null && !mayExceed({ role, permissions }, settings) ? <p className="hint">{t("credit.limit.sellersStopped")}</p> : null}
        {!can("customers.edit") ? null : editing ? (
          <LimitForm
            customer={customer}
            bounds={settings.bounds}
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
              {t(customer.creditLimit === null ? "credit.limit.set" : "credit.limit.change")}
            </button>
          </p>
        )}
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
