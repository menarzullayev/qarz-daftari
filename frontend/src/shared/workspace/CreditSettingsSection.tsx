import { useState, type FormEvent } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { CreditSettings, CreditSettingsPatch } from "../api";
import { formatMoney } from "../format";
import { useLoad, useSubmit } from "../hooks";
import { amountInput, type Currency } from "../money";
import { canManage } from "../navigation";
import { useWorkspace } from "./context";
import { limitMessage, limitRange } from "./CreditLimitSection";
import { parseLimit } from "./creditRules";
import { errorText, Failure, FieldError, Loading } from "./parts";

function ReadOnly({ settings }: { settings: CreditSettings }) {
  const { t, language } = useI18n();
  return (
    <>
      <p className="notice">{t("credit.settings.readOnly")}</p>
      <dl className="facts">
        <dt>{t("credit.settings.default.label")}</dt>
        <dd>
          {settings.defaultLimit === null
            ? t("credit.settings.default.none")
            : formatMoney(settings.defaultLimit, language)}
        </dd>
        {settings.usd === undefined ? null : (
          <>
            <dt>{t("credit.settings.usd.default.label")}</dt>
            <dd>
              {settings.usd.defaultLimit === null
                ? t("credit.settings.default.none")
                : formatMoney(settings.usd.defaultLimit, language, "USD")}
            </dd>
          </>
        )}
        <dt>{t("credit.settings.sellers.label")}</dt>
        <dd>{t(settings.sellersMayExceed ? "credit.settings.sellers.yes" : "credit.settings.sellers.no")}</dd>
      </dl>
    </>
  );
}

const limitText = (limit: number | null, currency: Currency = "UZS") => (limit === null ? "" : amountInput(limit, currency));

function CreditForm({ settings }: { settings: CreditSettings }) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  // What the server holds now: the loaded settings, then whatever the last save answered.
  const [saved, setSaved] = useState(settings);
  const [text, setText] = useState(limitText(settings.defaultLimit));
  const [sellersMayExceed, setSellersMayExceed] = useState(settings.sellersMayExceed);
  const [problem, setProblem] = useState<string | null>(null);
  // The default dollar limit: a field of its own, there only in a shop that works in dollars.
  const [usdText, setUsdText] = useState(limitText(settings.usd?.defaultLimit ?? null, "USD"));
  const [usdProblem, setUsdProblem] = useState<string | null>(null);
  const { state, submit, reset } = useSubmit((patch: CreditSettingsPatch, key) =>
    api.updateCreditSettings(patch, key).then((updated) => {
      setSaved(updated);
      setText(limitText(updated.defaultLimit));
      setUsdText(limitText(updated.usd?.defaultLimit ?? null, "USD"));
      setSellersMayExceed(updated.sellersMayExceed);
    }),
  );

  // An empty field means "no default limit"; anything else must be a limit the server accepts.
  const empty = text.trim() === "";
  const parsed = parseLimit(text, saved.bounds);
  const usd = saved.usd;
  const usdEmpty = usdText.trim() === "";
  const usdParsed = usd ? parseLimit(usdText, usd.bounds, "USD") : null;
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const wrong = !empty && !parsed.ok ? limitMessage(parsed.problem, saved.bounds, t) : null;
    const usdWrong =
      usd && usdParsed && !usdEmpty && !usdParsed.ok ? limitMessage(usdParsed.problem, usd.bounds, t, "USD") : null;
    setProblem(wrong);
    setUsdProblem(usdWrong);
    if (wrong !== null || usdWrong !== null) {
      return;
    }
    const defaultLimit = empty || !parsed.ok ? null : parsed.amount;
    // Send only what changed; the server refuses an empty change.
    const patch: CreditSettingsPatch = {};
    if (defaultLimit !== saved.defaultLimit) {
      patch.defaultLimit = defaultLimit;
    }
    if (usd && usdParsed) {
      const defaultLimitUsd = usdEmpty || !usdParsed.ok ? null : usdParsed.amount;
      if (defaultLimitUsd !== usd.defaultLimit) {
        patch.defaultLimitUsd = defaultLimitUsd;
      }
    }
    if (sellersMayExceed !== saved.sellersMayExceed) {
      patch.sellersMayExceed = sellersMayExceed;
    }
    if (Object.keys(patch).length > 0) {
      submit(patch);
    }
  };

  const failure = state.status === "error" ? state.error : null;
  const refused =
    failure?.code === "VALIDATION" && "default_credit_limit" in failure.fields
      ? limitMessage("too_small", saved.bounds, t)
      : null;
  const shown = problem ?? refused;
  const usdShown =
    usdProblem ??
    (usd && failure?.code === "VALIDATION" && "default_credit_limit_usd" in failure.fields
      ? limitMessage("too_small", usd.bounds, t, "USD")
      : null);
  const pending = state.status === "pending";
  const touched = () => {
    if (state.status === "done") {
      reset();
    }
  };

  return (
    <form className="form" onSubmit={onSubmit} noValidate aria-label={t("credit.settings.title")}>
      {failure ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      {state.status === "done" ? (
        <p className="notice notice--done" role="status">
          {t("credit.settings.saved")}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="credit-default">{t("credit.settings.default")}</label>
        <input
          id="credit-default"
          className="input input--amount"
          inputMode="numeric"
          autoComplete="off"
          value={text}
          aria-invalid={shown !== null}
          aria-describedby="credit-default-error credit-default-hint"
          onChange={(event) => {
            setText(event.target.value);
            setProblem(null);
            touched();
          }}
        />
        <p className="field__hint" id="credit-default-hint">
          {parsed.ok ? formatMoney(parsed.amount, language) : t("credit.settings.default.hint")}
        </p>
        <FieldError id="credit-default-error" message={shown} />
      </div>
      {usd && usdParsed ? (
        <div className="field">
          <label htmlFor="credit-default-usd">{t("credit.settings.usd.default")}</label>
          <input
            id="credit-default-usd"
            className="input input--amount"
            inputMode="decimal"
            autoComplete="off"
            value={usdText}
            aria-invalid={usdShown !== null}
            aria-describedby="credit-default-usd-error credit-default-usd-hint"
            onChange={(event) => {
              setUsdText(event.target.value);
              setUsdProblem(null);
              touched();
            }}
          />
          <p className="field__hint" id="credit-default-usd-hint">
            {usdParsed.ok ? formatMoney(usdParsed.amount, language, "USD") : limitRange(usd.bounds, t, "USD")}
          </p>
          <FieldError id="credit-default-usd-error" message={usdShown} />
        </div>
      ) : null}
      <label className="choice">
        <input
          type="checkbox"
          checked={sellersMayExceed}
          onChange={(event) => {
            setSellersMayExceed(event.target.checked);
            touched();
          }}
        />
        <span>{t("credit.settings.sellersMayExceed")}</span>
      </label>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("credit.settings.save")}
        </button>
      </p>
    </form>
  );
}

/**
 * The shop's rules for selling on credit (REQ-044): a default limit for customers who have none of
 * their own, and whether a seller may sell above a limit. A manager or an owner changes them; a seller
 * only reads them.
 */
export function CreditSettingsSection() {
  const { api, role } = useWorkspace();
  const { t } = useI18n();
  const { state, reload } = useLoad((signal) => api.readCreditSettings(signal), [api]);

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else {
    body = canManage(role) ? <CreditForm settings={state.data} /> : <ReadOnly settings={state.data} />;
  }
  return (
    <section aria-labelledby="credit-settings-title">
      <h2 id="credit-settings-title">{t("credit.settings.title")}</h2>
      {body}
    </section>
  );
}
