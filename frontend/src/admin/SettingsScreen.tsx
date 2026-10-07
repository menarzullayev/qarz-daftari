import { type FormEvent, useRef, useState } from "react";

import { useI18n, type Translate } from "../i18n/I18nProvider";
import type { ApiError } from "../shared/api";
import { useLoad, useSubmit } from "../shared/hooks";
import { errorText, Failure, FieldError, formatInstant, Loading } from "../shared/workspace/parts";
import type { AdminApi, PlatformSettings, SettingValue } from "./adminApi";
import "./messages";
import { cleanReason, isCode, parseSetting, REASON_MAX, REASON_MIN, SETTING_RULES, type SettingRule, settingText, shortId } from "./rules";
import { known, NONE } from "./ShopsScreen";

/** What a field must hold, in words: the setting's type and range. */
function ruleText(rule: SettingRule, t: Translate): string {
  switch (rule.kind) {
    case "switch":
      return t("admin.settings.rule.switch");
    case "number":
      return t("admin.settings.rule.number", { low: rule.low, high: rule.high });
    case "card":
      return t("admin.settings.rule.card");
    case "chat":
      return t("admin.settings.rule.chat");
  }
}

/** A stored value as a person reads it in the list of what changed. */
function valueText(value: SettingValue, t: Translate): string {
  if (typeof value === "boolean") {
    return t(value ? "admin.settings.on" : "admin.settings.off");
  }
  return value === null ? t("admin.settings.empty") : String(value);
}

function without(problems: Readonly<Record<string, string>>, ...keys: string[]): Record<string, string> {
  return Object.fromEntries(Object.entries(problems).filter(([key]) => !keys.includes(key)));
}

type Change = { key: string; before: SettingValue; after: SettingValue };

function SettingsForm({ api, loaded }: { api: AdminApi; loaded: PlatformSettings }) {
  const { t, language } = useI18n();
  // What the server holds now: what was loaded, then whatever the last change answered.
  const [saved, setSaved] = useState(loaded);
  const [texts, setTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(loaded.values).map(([key, value]) => [key, settingText(value)])),
  );
  const [code, setCode] = useState("");
  const [reason, setReason] = useState("");
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [changed, setChanged] = useState<Change[] | null>(null);
  // The code is not part of what is asked for: the same change sent again with a newer code is the same
  // request to the server, so it is read when the request is made and never compared.
  const codeNow = useRef<string | null>(null);
  const { state, submit, reset } = useSubmit((payload: { changes: Record<string, SettingValue>; reason: string | null }, key) =>
    api.updateSettings(payload.changes, codeNow.current, payload.reason, key).then((answer) => {
      setChanged(Object.entries(payload.changes).map(([name, after]) => ({ key: name, before: saved.values[name] ?? null, after })));
      setSaved(answer);
      setTexts(Object.fromEntries(Object.entries(answer.values).map(([name, value]) => [name, settingText(value)])));
      setCode("");
      setReason("");
    }),
  );
  const pending = state.status === "pending";
  const failure: ApiError | null = state.status === "error" ? state.error : null;

  // What differs from the server, and which fields do not hold a value of their setting's type and range.
  const changes: Record<string, SettingValue> = {};
  const invalid: string[] = [];
  for (const [key, rule] of Object.entries(SETTING_RULES)) {
    if (!(key in saved.values)) {
      continue;
    }
    const parsed = parseSetting(rule, texts[key] ?? "");
    if (!parsed.ok) {
      invalid.push(key);
    } else if (parsed.value !== saved.values[key]) {
      changes[key] = parsed.value;
    }
  }
  const codeNeeded = Object.keys(changes).some((key) => saved.needsCode.includes(key));

  const touch = (key: string, value: string) => {
    setTexts((current) => ({ ...current, [key]: value }));
    setProblems((current) => without(current, key, "_"));
    setChanged(null);
    reset();
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: Record<string, string> = {};
    for (const key of invalid) {
      const rule = SETTING_RULES[key];
      found[key] = rule ? ruleText(rule, t) : "";
    }
    const cleanedReason = reason.trim() === "" ? null : cleanReason(reason);
    if (reason.trim() !== "" && cleanedReason === null) {
      found["reason"] = t("admin.reason.invalid", { min: REASON_MIN, max: REASON_MAX });
    }
    if (codeNeeded && !isCode(code.trim())) {
      found["code"] = t("door.code.invalid");
    }
    if (invalid.length === 0 && Object.keys(changes).length === 0) {
      found["_"] = t("admin.settings.nothing");
    }
    setProblems(found);
    if (Object.keys(found).length > 0) {
      return;
    }
    codeNow.current = codeNeeded ? code.trim() : null;
    setChanged(null);
    submit({ changes, reason: cleanedReason });
  };

  /** A server refusal of one field, next to that field: its name there is `changes.<key>`. */
  const refused = (key: string): string | null => {
    if (failure?.code !== "VALIDATION" || !(`changes.${key}` in failure.fields)) {
      return null;
    }
    const rule = SETTING_RULES[key];
    return rule ? ruleText(rule, t) : errorText(failure, t);
  };
  const codeRefused =
    failure?.code === "SECOND_FACTOR_INVALID" || failure?.code === "SECOND_FACTOR_LOCKED"
      ? errorText(failure, t)
      : failure?.code === "VALIDATION" && "code" in failure.fields
        ? t("door.code.invalid")
        : null;
  const fieldRefused = failure?.code === "VALIDATION" && Object.keys(failure.fields).some((name) => name.startsWith("changes.") || name === "code");
  const unknown = Object.keys(saved.values).filter((key) => !(key in SETTING_RULES));

  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      {failure && codeRefused === null && !fieldRefused ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      {changed ? (
        <div className="notice notice--done" role="status">
          <p>{t("admin.settings.saved")}</p>
          <ul className="consequences">
            {changed.map((change) => (
              <li key={change.key}>
                {t("admin.settings.changed", {
                  name: known("admin.setting", change.key, t),
                  before: valueText(change.before, t),
                  after: valueText(change.after, t),
                })}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {Object.entries(SETTING_RULES).map(([key, rule]) => {
        if (!(key in saved.values)) {
          return null;
        }
        const id = `setting-${key}`;
        const message = problems[key] ?? refused(key);
        const last = saved.changed[key];
        const sensitive = saved.needsCode.includes(key);
        const notes = (
          <>
            <p className="field__hint" id={`${id}-hint`}>
              {ruleText(rule, t)}
              {sensitive ? ` ${t("admin.settings.needsCode")}` : ""}
            </p>
            {last ? (
              <p className="row__meta">{t("admin.settings.lastChange", { date: formatInstant(last.at, language), admin: shortId(last.by) })}</p>
            ) : null}
            <FieldError id={`${id}-error`} message={message} />
          </>
        );
        return rule.kind === "switch" ? (
          <div className="field" key={key}>
            <label className="choice">
              <input
                id={id}
                type="checkbox"
                checked={texts[key] === "true"}
                aria-describedby={`${id}-hint ${id}-error`}
                onChange={(event) => touch(key, String(event.target.checked))}
              />
              <span>{known("admin.setting", key, t)}</span>
            </label>
            {notes}
          </div>
        ) : (
          <div className="field" key={key}>
            <label htmlFor={id}>{known("admin.setting", key, t)}</label>
            <input
              id={id}
              className="input"
              inputMode="numeric"
              autoComplete="off"
              value={texts[key] ?? ""}
              aria-invalid={message !== null}
              aria-describedby={`${id}-hint ${id}-error`}
              onChange={(event) => touch(key, event.target.value)}
            />
            {notes}
          </div>
        );
      })}

      {unknown.length > 0 ? (
        <dl className="facts">
          {unknown.map((key) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{valueText(saved.values[key] ?? null, t) || NONE}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <div className="field">
        <label htmlFor="settings-reason">{t("admin.settings.reason")}</label>
        <textarea
          id="settings-reason"
          className="input input--text"
          rows={2}
          maxLength={2000}
          value={reason}
          aria-invalid={problems["reason"] !== undefined}
          aria-describedby="settings-reason-error"
          onChange={(event) => {
            setReason(event.target.value);
            setProblems((current) => without(current, "reason"));
          }}
        />
        <FieldError id="settings-reason-error" message={problems["reason"] ?? null} />
      </div>

      {codeNeeded ? (
        <div className="field">
          <label htmlFor="settings-code">{t("admin.settings.code")}</label>
          <input
            id="settings-code"
            className="input input--amount"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            value={code}
            aria-invalid={(problems["code"] ?? codeRefused) !== null && (problems["code"] ?? codeRefused) !== undefined}
            aria-describedby="settings-code-hint settings-code-error"
            onChange={(event) => {
              setCode(event.target.value);
                setProblems((current) => without(current, "code"));
            }}
          />
          <p className="field__hint" id="settings-code-hint">
            {t("admin.settings.code.hint")}
          </p>
          <FieldError id="settings-code-error" message={problems["code"] ?? codeRefused} />
        </div>
      ) : null}

      <FieldError id="settings-nothing" message={problems["_"] ?? null} />
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("action.save")}
        </button>
      </p>
    </form>
  );
}

/**
 * The platform's switches and prices (REQ-N14): each with its type and range, changed without a release.
 * A change of a sensitive one asks for a code from the authenticator again, in the same form; after a
 * change the page says what changed, from what to what.
 */
export function SettingsScreen({ api }: { api: AdminApi }) {
  const { state, reload } = useLoad((signal) => api.readSettings(signal), [api]);
  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }
  return <SettingsForm api={api} loaded={state.data} />;
}
