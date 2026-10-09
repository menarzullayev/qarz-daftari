import { type FormEvent, useRef, useState } from "react";

import { useI18n, type Translate } from "../i18n/I18nProvider";
import { type ApiError, toApiError } from "../shared/api";
import { useLoad, useSubmit } from "../shared/hooks";
import { Confirm, errorText, Failure, FieldError, formatInstant, Loading } from "../shared/workspace/parts";
import type { AdminApi, PaymentCard, PlatformSettings, SettingValue } from "./adminApi";
import "./messages";
import {
  CARD_LABEL_MAX,
  cardProblems,
  cardRows,
  CARDS_MAX,
  cardTag,
  cleanReason,
  isCode,
  loweredPlan,
  planSwitchedOff,
  parseSetting,
  REASON_MAX,
  REASON_MIN,
  SETTING_RULES,
  type SettingRule,
  settingText,
  shortId,
} from "./rules";
import { known, NONE } from "./ShopsScreen";

/** What a field must hold, in words: the setting's type and range. */
function ruleText(rule: SettingRule, t: Translate): string {
  switch (rule.kind) {
    case "switch":
      return t("admin.settings.rule.switch");
    case "number":
      return t("admin.settings.rule.number", { low: rule.low, high: rule.high });
    case "cards":
      return t("admin.settings.rule.cards", { max: CARDS_MAX });
    case "chat":
      return t("admin.settings.rule.chat");
  }
}

/** A stored value as a person reads it in the list of what changed. */
function valueText(value: SettingValue, t: Translate): string {
  if (typeof value === "boolean") {
    return t(value ? "admin.settings.on" : "admin.settings.off");
  }
  if (Array.isArray(value)) {
    // By name and last four digits, in order: the whole numbers are in the editor above, not here.
    return value.length === 0 ? t("admin.settings.empty") : value.map(cardTag).join(", ");
  }
  return value === null ? t("admin.settings.empty") : String(value);
}

/**
 * The cards owners may pay to, as a list the administrator edits: a name and a number for each, added,
 * removed and moved up or down. The first is the primary card and is marked so. What is wrong with a row
 * is said under it once the form was sent, as with every other field.
 */
function CardsEditor({
  id,
  rows,
  checked,
  onChange,
}: {
  id: string;
  rows: PaymentCard[];
  /** Whether to say what is wrong: after a save was tried, until the rows are touched. */
  checked: boolean;
  onChange: (rows: PaymentCard[]) => void;
}) {
  const { t } = useI18n();
  const problems = cardProblems(rows);
  const set = (place: number, change: Partial<PaymentCard>) => onChange(rows.map((row, at) => (at === place ? { ...row, ...change } : row)));
  const move = (place: number, to: number) => {
    const next = [...rows];
    const [moved] = next.splice(place, 1);
    if (moved !== undefined) {
      next.splice(to, 0, moved);
      onChange(next);
    }
  };
  return (
    <>
      {rows.length === 0 ? <p className="state">{t("admin.cards.none")}</p> : null}
      <ol className="rows">
        {rows.map((row, place) => {
          const problem = checked ? (problems[place] ?? null) : null;
          const at = { place: place + 1 };
          const rowId = `${id}-${place}`;
          return (
            // Rows have no identity but their place: a row is what was typed, and may be empty or twice.
            <li key={place} className="row" aria-label={t("admin.cards.place", at)}>
              {place === 0 ? <p className="row__meta">{t("admin.cards.primary")}</p> : null}
              <div className="field">
                <label htmlFor={`${rowId}-label`}>{t("admin.cards.label", at)}</label>
                <input
                  id={`${rowId}-label`}
                  className="input"
                  autoComplete="off"
                  maxLength={CARD_LABEL_MAX * 2}
                  placeholder={t("admin.cards.label.hint")}
                  value={row.label}
                  aria-invalid={problem === "label"}
                  aria-describedby={`${rowId}-error`}
                  onChange={(event) => set(place, { label: event.target.value })}
                />
              </div>
              <div className="field">
                <label htmlFor={`${rowId}-number`}>{t("admin.cards.number", at)}</label>
                <input
                  id={`${rowId}-number`}
                  className="input"
                  inputMode="numeric"
                  autoComplete="off"
                  value={row.number}
                  aria-invalid={problem === "number" || problem === "duplicate"}
                  aria-describedby={`${rowId}-error`}
                  onChange={(event) => set(place, { number: event.target.value })}
                />
                <FieldError
                  id={`${rowId}-error`}
                  message={problem === null ? null : t(`admin.cards.problem.${problem}`, { max: CARD_LABEL_MAX })}
                />
              </div>
              <p className="actions">
                <button type="button" className="button button--small" aria-label={t("admin.cards.up", at)} disabled={place === 0} onClick={() => move(place, place - 1)}>
                  ↑
                </button>
                <button
                  type="button"
                  className="button button--small"
                  aria-label={t("admin.cards.down", at)}
                  disabled={place === rows.length - 1}
                  onClick={() => move(place, place + 1)}
                >
                  ↓
                </button>
                <button type="button" className="button button--small" aria-label={t("admin.cards.remove", at)} onClick={() => onChange(rows.filter((_, other) => other !== place))}>
                  ✕
                </button>
              </p>
            </li>
          );
        })}
      </ol>
      {rows.length >= CARDS_MAX ? (
        <p className="field__hint">{t("admin.cards.full", { max: CARDS_MAX })}</p>
      ) : (
        <button type="button" className="button" onClick={() => onChange([...rows, { number: "", label: "" }])}>
          {t("admin.cards.add")}
        </button>
      )}
    </>
  );
}

function without(problems: Readonly<Record<string, string>>, ...keys: string[]): Record<string, string> {
  return Object.fromEntries(Object.entries(problems).filter(([key]) => !keys.includes(key)));
}

type Change = { key: string; before: SettingValue; after: SettingValue };
type Payload = { changes: Record<string, SettingValue>; reason: string | null };
/**
 * A change that lowers the free plan, or switches it off (`to` is then null), and would limit `shops`
 * shops: asked about before it is sent.
 */
type Lowering = { payload: Payload; from: number; to: number; shops: number } | { payload: Payload; to: null; shops: number };

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
  // The free plan lowered or switched off: the question before the change is sent, and what the server said after it.
  const [lowering, setLowering] = useState<Lowering | null>(null);
  const [asking, setAsking] = useState<{ status: "idle" | "pending" } | { status: "error"; error: ApiError }>({ status: "idle" });
  const [limited, setLimited] = useState<number | null>(null);
  // Which question is the current one: an answer to an earlier one, asked before a field changed, is dropped.
  const question = useRef(0);
  // The code is not part of what is asked for: the same change sent again with a newer code is the same
  // request to the server, so it is read when the request is made and never compared.
  const codeNow = useRef<string | null>(null);
  const { state, submit, reset } = useSubmit((payload: Payload, key) =>
    api.updateSettings(payload.changes, codeNow.current, payload.reason, key).then((answer) => {
      setChanged(Object.entries(payload.changes).map(([name, after]) => ({ key: name, before: saved.values[name] ?? null, after })));
      setLowering(null);
      setLimited(answer.planLimited);
      setSaved(answer);
      setTexts(Object.fromEntries(Object.entries(answer.values).map(([name, value]) => [name, settingText(value)])));
      setCode("");
      setReason("");
    }),
  );
  const pending = state.status === "pending";
  const failure: ApiError | null = state.status === "error" ? state.error : asking.status === "error" ? asking.error : null;

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
    } else if (settingText(parsed.value) !== settingText(saved.values[key] ?? null)) {
      // Compared as text: a list of cards is equal to another by what it holds, not by being the same list.
      changes[key] = parsed.value;
    }
  }
  const codeNeeded = Object.keys(changes).some((key) => saved.needsCode.includes(key));

  const touch = (key: string, value: string) => {
    setTexts((current) => ({ ...current, [key]: value }));
    setProblems((current) => without(current, key, "_"));
    setChanged(null);
    setLowering(null);
    question.current += 1;
    setAsking({ status: "idle" });
    reset();
  };

  const send = (payload: Payload) => {
    codeNow.current = codeNeeded ? code.trim() : null;
    submit(payload);
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: Record<string, string> = {};
    for (const key of invalid) {
      const rule = SETTING_RULES[key];
      // The cards say what is wrong row by row; the field itself only says that something is.
      found[key] = rule?.kind === "cards" ? t("admin.cards.invalid") : rule ? ruleText(rule, t) : "";
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
    setChanged(null);
    const payload = { changes, reason: cleanedReason };
    // Switched off, the plan holds nobody whatever number is saved with the switch: that one question
    // covers a number changed in the same save.
    const off = planSwitchedOff(saved.values, changes);
    const lowered = off ? null : loweredPlan(saved.values, changes);
    if (!off && lowered === null) {
      send(payload);
      return;
    }
    if (asking.status === "pending") {
      return;
    }
    // A lower number limits the shops that are over it, and the plan switched off every shop it holds. How
    // many is asked first, and nothing is sent until the administrator has seen the number and said yes.
    reset();
    setAsking({ status: "pending" });
    const asked = ++question.current;
    (lowered === null ? api.previewFreePlanOff() : api.previewFreePlan(lowered.to)).then(
      (shops) => {
        if (asked !== question.current) {
          return;
        }
        setAsking({ status: "idle" });
        if (shops === null || shops === 0) {
          send(payload);
        } else {
          setLowering(lowered === null ? { payload, to: null, shops } : { payload, ...lowered, shops });
        }
      },
      (error: unknown) => {
        if (asked === question.current) {
          setAsking({ status: "error", error: toApiError(error) });
        }
      },
    );
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
          {limited !== null && limited > 0 ? <p>{t("admin.settings.plan.limited", { count: limited })}</p> : null}
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
        if (rule.kind === "cards") {
          return (
            <fieldset className="field" key={key} aria-describedby={`${id}-hint ${id}-error`}>
              <legend>{known("admin.setting", key, t)}</legend>
              <CardsEditor id={id} rows={cardRows(texts[key] ?? "")} checked={problems[key] !== undefined} onChange={(rows) => touch(key, JSON.stringify(rows))} />
              {notes}
            </fieldset>
          );
        }
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
      {lowering !== null ? (
        <Confirm
          question={
            <>
              <p>{lowering.to === null ? t("admin.settings.plan.off") : t("admin.settings.plan.confirm", { from: lowering.from, to: lowering.to })}</p>
              <p>{t("admin.settings.plan.shops", { count: lowering.shops })}</p>
              <p>{t("admin.settings.plan.ownersTold")}</p>
            </>
          }
          yes={t("admin.settings.plan.yes")}
          no={t("action.back")}
          pending={pending}
          onYes={() => send(lowering.payload)}
          onNo={() => {
            setLowering(null);
            reset();
          }}
        />
      ) : (
        <p className="actions">
          <button type="submit" className="button button--primary" disabled={pending || asking.status === "pending"}>
            {pending ? t("state.saving") : t("action.save")}
          </button>
        </p>
      )}
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
