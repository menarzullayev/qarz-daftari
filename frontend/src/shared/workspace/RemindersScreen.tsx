import { useState, type FormEvent } from "react";

import { translate } from "../../i18n/catalog";
import { useI18n, type Translate } from "../../i18n/I18nProvider";
import { DEFAULT_LANGUAGE } from "../../i18n/detect";
import type { Language } from "../../i18n/types";
import type { ApiError, ReminderSettings, ReminderSettingsPatch, ReminderTemplate } from "../api";
import { formatMoney } from "../format";
import { useLoad, useSubmit } from "../hooks";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useMay, useWorkspace } from "./context";
import { Empty, errorText, Failure, FieldError, Loading, Money } from "./parts";

/** The amount an example reminder mentions, in whole UZS. */
export const EXAMPLE_AMOUNT = 45_000;

/** The whole hours a shop may choose, first to last. An order the server did not mean gives none. */
export function hourChoices(hours: ReminderSettings["hours"]): number[] {
  const choices: number[] = [];
  for (let hour = Math.max(0, hours.first); hour <= Math.min(23, hours.last); hour += 1) {
    choices.push(hour);
  }
  return choices;
}

/**
 * A wording exactly as the server keeps it, with its three placeholders filled, so the person reads
 * what a customer will read. Nothing else in the text is touched.
 */
export function fillWording(wording: string, example: { shop: string; name: string; amount: string }): string {
  return wording.replace(/\{(shop|name|amount)\}/g, (_whole, name: "shop" | "name" | "amount") => example[name]);
}

/**
 * The wording shown: the one in the reader's own language, and the Uzbek one when the server has none in
 * it. The server sends a wording for each of its languages; a customer is reminded in theirs.
 */
function wordingFor(wordings: Readonly<Record<string, string>>, own: Language): { code: Language; text: string } | null {
  for (const code of [own, DEFAULT_LANGUAGE]) {
    const text = wordings[code];
    if (typeof text === "string" && text !== "") {
      return { code, text };
    }
  }
  return null;
}

function Wordings({ title, wordings }: { title: string; wordings: Readonly<Record<string, string>> }) {
  const { t, language } = useI18n();
  const { shopName } = useWorkspace();
  const shown = wordingFor(wordings, language);
  return (
    <>
      <p className="wording__when">{title}</p>
      {shown ? (
        // The example name and the amount are written as the message's own language writes them.
        <p className="wording" lang={shown.code}>
          {fillWording(shown.text, {
            shop: shopName ?? t("reminders.example.shop"),
            name: translate(shown.code, "reminders.example.name"),
            amount: formatMoney(EXAMPLE_AMOUNT, shown.code),
          })}
        </p>
      ) : null}
    </>
  );
}

function TemplateChoice({
  template,
  checked,
  disabled,
  onChoose,
}: {
  template: ReminderTemplate;
  checked: boolean;
  disabled: boolean;
  onChoose: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="template">
      <label className="choice">
        <input type="radio" name="reminder-template" checked={checked} disabled={disabled} onChange={onChoose} />
        <span>{t("reminders.template.option", { number: template.id })}</span>
      </label>
      <Wordings title={t("reminders.template.dueToday")} wordings={template.dueToday} />
      <Wordings title={t("reminders.template.overdue")} wordings={template.overdue} />
    </div>
  );
}

type FieldErrors = { hour: string | null; template: string | null };

function refusedFields(error: ApiError | null, settings: ReminderSettings, t: Translate): FieldErrors {
  if (error?.code !== "VALIDATION") {
    return { hour: null, template: null };
  }
  return {
    hour: "hour" in error.fields ? t("reminders.hour.invalid", { first: settings.hours.first, last: settings.hours.last }) : null,
    template: "template" in error.fields ? t("reminders.template.invalid") : null,
  };
}

function SettingsForm({ settings, editable }: { settings: ReminderSettings; editable: boolean }) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  // What the server holds now: the loaded settings, then whatever the last save answered.
  const [saved, setSaved] = useState(settings);
  const [draft, setDraft] = useState({
    on: settings.on,
    hour: settings.hour,
    template: settings.template,
    smsOn: settings.smsOn,
  });
  const { state, submit, reset } = useSubmit((patch: ReminderSettingsPatch, key) =>
    api.updateReminders(patch, key).then((updated) => {
      setSaved(updated);
      setDraft({ on: updated.on, hour: updated.hour, template: updated.template, smsOn: updated.smsOn });
    }),
  );
  const hours = hourChoices(saved.hours);

  const change = (next: Partial<typeof draft>) => {
    setDraft((current) => ({ ...current, ...next }));
    if (state.status === "done") {
      reset();
    }
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    // Send only what changed; the server refuses an empty change. The same change sent again after a
    // failure carries the same key, so a retry does not change the settings twice.
    const patch: ReminderSettingsPatch = {};
    if (draft.on !== saved.on) {
      patch.on = draft.on;
    }
    if (draft.hour !== saved.hour) {
      patch.hour = draft.hour;
    }
    if (draft.template !== saved.template) {
      patch.template = draft.template;
    }
    if (draft.smsOn !== saved.smsOn) {
      patch.smsOn = draft.smsOn;
    }
    if (Object.keys(patch).length > 0) {
      submit(patch);
    }
  };

  const failure = state.status === "error" ? state.error : null;
  const refused = refusedFields(failure, saved, t);
  const saving = state.status === "pending";
  // A member who reads the settings without the right to change them sees them, fixed, with no "save".
  const pending = saving || !editable;

  return (
    <form className="form" onSubmit={onSubmit} noValidate aria-labelledby="reminder-settings-title">
      {failure ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      {state.status === "done" ? (
        <p className="notice notice--done" role="status">
          {t("reminders.saved")}
        </p>
      ) : null}

      <div className="field">
        <label className="choice">
          <input type="checkbox" checked={draft.on} disabled={pending} onChange={(event) => change({ on: event.target.checked })} />
          <span>{t("reminders.on")}</span>
        </label>
        <p className="field__hint">{t("reminders.on.hint")}</p>
      </div>

      <div className="field">
        <label htmlFor="reminder-hour">{t("reminders.hour")}</label>
        <select
          id="reminder-hour"
          className="input"
          value={draft.hour}
          disabled={pending}
          aria-invalid={refused.hour !== null}
          aria-describedby="reminder-hour-error"
          onChange={(event) => change({ hour: Number(event.target.value) })}
        >
          {/* An hour the server holds outside its own range is still shown, so saving does not move it. */}
          {(hours.includes(draft.hour) ? hours : [draft.hour, ...hours]).map((hour) => (
            <option key={hour} value={hour}>
              {t("reminders.hour.value", { hour: String(hour).padStart(2, "0") })}
            </option>
          ))}
        </select>
        <FieldError id="reminder-hour-error" message={refused.hour} />
      </div>

      <fieldset className="field choices">
        <legend>{t("reminders.template")}</legend>
        <p className="field__hint">{t("reminders.template.example")}</p>
        {saved.templates.map((template) => (
          <TemplateChoice
            key={template.id}
            template={template}
            checked={draft.template === template.id}
            disabled={pending}
            onChoose={() => change({ template: template.id })}
          />
        ))}
        <FieldError id="reminder-template-error" message={refused.template} />
      </fieldset>

      <div className="field">
        <label className="choice">
          <input
            type="checkbox"
            checked={draft.smsOn}
            disabled={pending}
            onChange={(event) => change({ smsOn: event.target.checked })}
          />
          <span>{t("reminders.sms")}</span>
        </label>
        <p className="field__hint">{t("reminders.sms.hint")}</p>
        {/* Only a shop that works in dollars is told, and only while an SMS does not carry them. */}
        {saved.usdBySms === false ? <p className="field__hint">{t("reminders.sms.usd")}</p> : null}
      </div>

      {editable ? (
        <p className="actions">
          <button type="submit" className="button button--primary" disabled={saving}>
            {saving ? t("state.saving") : t("action.save")}
          </button>
        </p>
      ) : null}
    </form>
  );
}

function Settings({ editable }: { editable: boolean }) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const { state, reload } = useLoad((signal) => api.readReminders(signal), [api]);
  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else {
    body = <SettingsForm settings={state.data} editable={editable} />;
  }
  return (
    <section aria-labelledby="reminder-settings-title">
      <h2 id="reminder-settings-title">{t("reminders.settings.title")}</h2>
      {body}
    </section>
  );
}

/** Customers with something due and no channel to be reminded through (REQ-043). */
function Unreachable() {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const { state, reload } = useLoad((signal) => api.listUnreachable(signal), [api]);

  let body;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.data.length === 0) {
    body = <Empty>{t("reminders.unreachable.none")}</Empty>;
  } else {
    body = (
      <>
        <p className="hint">{t("reminders.unreachable.hint")}</p>
        <ul className="rows">
          {state.data.map((customer) => (
            <li key={customer.customerId} className="row">
              {/* The customer's page is where a personal link is made. */}
              <Link to={`/customers/${customer.customerId}`} className="row__link">
                <span className="row__name">{customer.displayName}</span>
                <span className="row__amount">
                  <Money uzs={customer.amount} usd={customer.usdAmount} />
                </span>
              </Link>
              <p className="row__meta">{customer.phone ?? t("reminders.unreachable.noPhone")}</p>
              {/* A number is there and still nothing reaches them: the reason, in a shop that works in dollars. */}
              {customer.reason === "usd_needs_telegram" ? <p className="row__meta">{t("reminders.unreachable.usd")}</p> : null}
            </li>
          ))}
        </ul>
      </>
    );
  }
  return (
    <section aria-labelledby="unreachable-title">
      <h2 id="unreachable-title">{t("reminders.unreachable.title")}</h2>
      {body}
    </section>
  );
}

/**
 * Reminders for a manager or an owner (REQ-022 to REQ-025, REQ-042, REQ-043): whether they go out, at
 * what hour and in which wording, the shop's SMS switch, and who cannot be reached. A seller is shown
 * nothing and asks the server nothing.
 */
export function RemindersScreen() {
  const can = useMay();
  if (!can("reminders.send") && !can("settings.view")) {
    return <NotFoundScreen />;
  }
  return (
    <>
      {can("settings.view") ? <Settings editable={can("settings.edit")} /> : null}
      {can("reminders.send") ? <Unreachable /> : null}
    </>
  );
}
