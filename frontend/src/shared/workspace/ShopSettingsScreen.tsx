import { type FormEvent, useState } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import { LANGUAGES } from "../../i18n/types";
import type { ApiError, ShopSettings, ShopSettingsPatch } from "../api";
import { useLoad, useSubmit } from "../hooks";
import { onDemand } from "../onDemand";
import { may, type Viewer } from "../permissions";
import { NotFoundScreen } from "../screens";
import { useWorkspace } from "./context";
import { errorText, Failure, FieldError, Loading } from "./parts";

/** Bounds the server checks (backend/src/qarz/application/shops.py, `ShopUpdate.validate`). */
export const MAX_SHOP_NAME = 80;
export const MIN_PROMISE_DAYS = 1;
export const MAX_PROMISE_DAYS_SETTING = 365;

/**
 * By role a manager reads the settings, only the owner changes them, and a seller has no such section;
 * what the server said the member holds decides when it said anything.
 */
export function settingsAccess(viewer: Viewer): "edit" | "read" | "none" {
  if (may(viewer, "shop.edit")) {
    return "edit";
  }
  return may(viewer, "settings.view") ? "read" : "none";
}

/** A whole number of days within the bounds, or null. "30.5", "1e2" and "30 kun" are not days. */
export function parsePromiseDays(input: string): number | null {
  const text = input.trim();
  if (!/^\d{1,3}$/.test(text)) {
    return null;
  }
  const days = Number(text);
  return days >= MIN_PROMISE_DAYS && days <= MAX_PROMISE_DAYS_SETTING ? days : null;
}

// The phone a shop shows on the page behind a customer's read-only link: behind a platform switch, so
// its code is fetched apart and shows nothing until the server has said the switch is on.
const ShareContactSection = onDemand(() => import("../share/ShareContactSection"));

type FieldErrors = { name: string | null; days: string | null };
const NO_ERRORS: FieldErrors = { name: null, days: null };

function nameMessage(t: Translate): string {
  return t("settings.name.invalid", { max: MAX_SHOP_NAME });
}

function daysMessage(t: Translate): string {
  return t("settings.promiseDays.invalid", { min: MIN_PROMISE_DAYS, max: MAX_PROMISE_DAYS_SETTING });
}

function refusedFields(error: ApiError | null, t: Translate): FieldErrors {
  if (error?.code !== "VALIDATION") {
    return NO_ERRORS;
  }
  return {
    name: "name" in error.fields ? nameMessage(t) : null,
    days: "default_promise_days" in error.fields ? daysMessage(t) : null,
  };
}

function ReadOnly({ settings }: { settings: ShopSettings }) {
  const { t } = useI18n();
  const language = LANGUAGES.find((code) => code === settings.lang);
  return (
    <>
      <p className="notice">{t("settings.readOnly")}</p>
      <dl className="facts">
        <dt>{t("settings.name")}</dt>
        <dd>{settings.name}</dd>
        <dt>{t("settings.lang")}</dt>
        <dd>{language ? t(`lang.${language}`) : settings.lang}</dd>
        <dt>{t("settings.promiseDays")}</dt>
        <dd>{t("settings.promiseDays.value", { count: settings.defaultPromiseDays })}</dd>
        {/* Only while the platform offers dollars: otherwise the shop has no such setting to read. */}
        {settings.usdOn === undefined ? null : (
          <>
            <dt>{t("settings.usd")}</dt>
            <dd>{t(settings.usdOn ? "settings.usd.on" : "settings.usd.off")}</dd>
          </>
        )}
      </dl>
    </>
  );
}

function SettingsForm({ settings }: { settings: ShopSettings }) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  // What the server holds now: the loaded settings, then whatever the last save answered.
  const [saved, setSaved] = useState(settings);
  const [name, setName] = useState(settings.name);
  const [lang, setLang] = useState(settings.lang);
  const [days, setDays] = useState(String(settings.defaultPromiseDays));
  // "This shop also works in dollars." There is a switch only while the server sends the setting.
  const [usdOn, setUsdOn] = useState(settings.usdOn === true);
  const [errors, setErrors] = useState<FieldErrors>(NO_ERRORS);
  const { state, submit, reset } = useSubmit((patch: ShopSettingsPatch, key) =>
    api.updateSettings(patch, key).then(
      (updated) => {
        setSaved(updated);
        setName(updated.name);
        setLang(updated.lang);
        setDays(String(updated.defaultPromiseDays));
        setUsdOn(updated.usdOn === true);
      },
      (error: ApiError) => {
        // Dollars stay on while a customer owes dollars: the switch goes back to what the server holds,
        // and the server's own words say why.
        if (error.code === "USD_BALANCE_OPEN") {
          setUsdOn(true);
        }
        throw error;
      },
    ),
  );

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const cleanName = name.trim();
    const parsedDays = parsePromiseDays(days);
    const found: FieldErrors = {
      name: cleanName === "" || [...cleanName].length > MAX_SHOP_NAME ? nameMessage(t) : null,
      days: parsedDays === null ? daysMessage(t) : null,
    };
    setErrors(found);
    if (found.name !== null || parsedDays === null) {
      return;
    }
    // Send only what changed; the server refuses an empty change.
    const patch: ShopSettingsPatch = {};
    if (cleanName !== saved.name) {
      patch.name = cleanName;
    }
    if (lang !== saved.lang) {
      patch.lang = lang;
    }
    if (parsedDays !== saved.defaultPromiseDays) {
      patch.defaultPromiseDays = parsedDays;
    }
    if (saved.usdOn !== undefined && usdOn !== saved.usdOn) {
      patch.usdOn = usdOn;
    }
    if (Object.keys(patch).length > 0) {
      submit(patch);
    }
  };

  const failure = state.status === "error" ? state.error : null;
  const refused = refusedFields(failure, t);
  const shown: FieldErrors = { name: errors.name ?? refused.name, days: errors.days ?? refused.days };
  const pending = state.status === "pending";
  // Typing after a save takes the "saved" notice away: it would no longer describe what is on the screen.
  const touched = (field: keyof FieldErrors | null) => {
    if (field !== null) {
      setErrors((current) => ({ ...current, [field]: null }));
    }
    if (state.status === "done") {
      reset();
    }
  };

  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      {failure ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      {state.status === "done" ? (
        <p className="notice notice--done" role="status">
          {t("settings.saved")}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="settings-name">{t("settings.name")}</label>
        <input
          id="settings-name"
          className="input"
          value={name}
          autoComplete="off"
          aria-invalid={shown.name !== null}
          aria-describedby="settings-name-error"
          onChange={(event) => {
            setName(event.target.value);
            touched("name");
          }}
        />
        <FieldError id="settings-name-error" message={shown.name} />
      </div>
      <div className="field">
        <label htmlFor="settings-lang">{t("settings.lang")}</label>
        <select
          id="settings-lang"
          className="input"
          value={lang}
          onChange={(event) => {
            setLang(event.target.value);
            touched(null);
          }}
        >
          {LANGUAGES.map((code) => (
            <option key={code} value={code} lang={code}>
              {t(`lang.${code}`)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="settings-days">{t("settings.promiseDays")}</label>
        <input
          id="settings-days"
          className="input"
          inputMode="numeric"
          value={days}
          autoComplete="off"
          aria-invalid={shown.days !== null}
          aria-describedby="settings-days-error"
          onChange={(event) => {
            setDays(event.target.value);
            touched("days");
          }}
        />
        <FieldError id="settings-days-error" message={shown.days} />
      </div>
      {saved.usdOn === undefined ? null : (
        <div className="field">
          <label className="choice">
            <input
              type="checkbox"
              checked={usdOn}
              aria-describedby="settings-usd-hint"
              onChange={(event) => {
                setUsdOn(event.target.checked);
                touched(null);
              }}
            />
            <span>{t("settings.usd")}</span>
          </label>
          <p className="field__hint" id="settings-usd-hint">
            {t("settings.usd.hint")}
          </p>
        </div>
      )}
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("action.save")}
        </button>
      </p>
    </form>
  );
}

function Settings({ editable }: { editable: boolean }) {
  const { api } = useWorkspace();
  const { state, reload } = useLoad((signal) => api.readSettings(signal), [api]);
  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }
  return editable ? <SettingsForm settings={state.data} /> : <ReadOnly settings={state.data} />;
}

/**
 * The shop's own settings by role (REQ-049): its name, its language, and the usual number of days a
 * customer has to pay. The owner changes them, a manager reads them, and a
 * seller is shown nothing and asks the server nothing.
 */
export function ShopSettingsScreen() {
  const { role, permissions } = useWorkspace();
  const viewer = { role, permissions };
  const access = settingsAccess(viewer);
  if (access === "none") {
    return <NotFoundScreen />;
  }
  return (
    <>
      <Settings editable={access === "edit"} />
      {may(viewer, "customers.share") ? <ShareContactSection editable={access === "edit"} /> : null}
    </>
  );
}
