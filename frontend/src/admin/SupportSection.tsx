import { type FormEvent, type ReactNode, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import { type ApiError, toApiError } from "../shared/api";
import { useLoad, useSubmit } from "../shared/hooks";
import { Link } from "../shared/router";
import { Confirm, Failure, FieldError, formatInstant, Loading } from "../shared/workspace/parts";
import type { AdminApi, SupportAccess } from "./adminApi";
import "./messages";
import {
  cleanReason,
  isOpenAccess,
  parseHours,
  REASON_MAX,
  REASON_MIN,
  shortId,
  SUPPORT_HOURS_MAX,
  SUPPORT_HOURS_MIN,
} from "./rules";

/**
 * Which administrator the person at this panel is. The API never says; it is learnt from the answer to
 * an access opened here, and held in memory for as long as the panel stays open. Until then it is null.
 */
export type Who = { me: string | null; learn: (adminId: string) => void };

type Draft = { reason: string; hours: number };

/** Opening an access: the reason and the hours, then the question. Nothing is sent before the "yes". */
function OpenForm({
  api,
  shop,
  onOpened,
  onRefused,
  onCancel,
}: {
  api: AdminApi;
  shop: { id: string; name: string };
  onOpened: (access: SupportAccess) => void;
  onRefused: (error: ApiError) => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [reason, setReason] = useState("");
  const [hours, setHours] = useState(String(SUPPORT_HOURS_MIN));
  const [problems, setProblems] = useState<{ reason: string | null; hours: string | null }>({ reason: null, hours: null });
  const [draft, setDraft] = useState<Draft | null>(null);
  const { state, submit, reset } = useSubmit((payload: Draft, key) =>
    api.openSupport(shop.id, payload, key).then(onOpened, (error: unknown) => {
      const failure = toApiError(error);
      // The caller's own access to this shop is open already: the section reads the shop's accesses again.
      if (failure.code === "SUPPORT_ACCESS_ALREADY_OPEN") {
        onRefused(failure);
      }
      throw error;
    }),
  );
  const failure = state.status === "error" ? state.error : null;
  const reasonMessage = t("admin.reason.invalid", { min: REASON_MIN, max: REASON_MAX });
  const hoursMessage = t("admin.support.hours.invalid", { min: SUPPORT_HOURS_MIN, max: SUPPORT_HOURS_MAX });

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const clean = cleanReason(reason);
    const whole = parseHours(hours);
    setProblems({ reason: clean === null ? reasonMessage : null, hours: whole === null ? hoursMessage : null });
    if (clean !== null && whole !== null) {
      reset();
      setDraft({ reason: clean, hours: whole });
    }
  };

  if (draft !== null) {
    // What the server found wrong with a field it checks again: said in the catalog's words.
    const refused = failure?.code === "VALIDATION" ? failure.fields : {};
    return (
      <Confirm
        question={
          <>
            {"reason" in refused ? <p className="field__error">{reasonMessage}</p> : null}
            {"hours" in refused ? <p className="field__error">{hoursMessage}</p> : null}
            <p>{t("admin.support.confirm", { shop: shop.name, hours: draft.hours })}</p>
            <p>{t("admin.change.confirm.reason", { reason: draft.reason })}</p>
            <p>{t("admin.support.confirm.told")}</p>
          </>
        }
        yes={t("admin.support.open.yes")}
        no={t("action.back")}
        pending={state.status === "pending"}
        error={failure}
        onYes={() => submit(draft)}
        onNo={() => {
          setDraft(null);
          reset();
        }}
      />
    );
  }

  return (
    <form className="form notice" onSubmit={onSubmit} noValidate aria-label={t("admin.support.open")}>
      <div className="field">
        <label htmlFor="support-reason">{t("admin.reason")}</label>
        <textarea
          id="support-reason"
          className="input input--text"
          rows={2}
          maxLength={2000}
          value={reason}
          aria-invalid={problems.reason !== null}
          aria-describedby="support-reason-hint support-reason-error"
          onChange={(event) => {
            setReason(event.target.value);
            setProblems((current) => ({ ...current, reason: null }));
          }}
        />
        <p className="field__hint" id="support-reason-hint">
          {t("admin.support.reason.hint")}
        </p>
        <FieldError id="support-reason-error" message={problems.reason} />
      </div>
      <div className="field">
        <label htmlFor="support-hours">{t("admin.support.hours")}</label>
        <input
          id="support-hours"
          className="input"
          inputMode="numeric"
          autoComplete="off"
          maxLength={2}
          value={hours}
          aria-invalid={problems.hours !== null}
          aria-describedby="support-hours-hint support-hours-error"
          onChange={(event) => {
            setHours(event.target.value);
            setProblems((current) => ({ ...current, hours: null }));
          }}
        />
        <p className="field__hint" id="support-hours-hint">
          {t("admin.support.hours.hint", { min: SUPPORT_HOURS_MIN, max: SUPPORT_HOURS_MAX })}
        </p>
        <FieldError id="support-hours-error" message={problems.hours} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary">
          {t("admin.change.continue")}
        </button>
        <button type="button" className="button" onClick={onCancel}>
          {t("action.cancel")}
        </button>
      </p>
    </form>
  );
}

type Step = "idle" | "opening" | "closing";
type Done = { kind: "opened"; endsAt: string } | { kind: "closed" };

/**
 * Support access on a shop's page (REQ-059): the accesses open to it now, opening one with a reason and
 * a number of hours, and closing one's own. Nothing of the shop's customers is on this page: they are
 * read on screens of their own, reached from here while an access is open.
 *
 * The API does not say which administrator the caller is. Once an access was opened here the panel
 * knows, and offers the customers and the closing only beside the caller's own access. Before that it
 * cannot tell an own access from a colleague's: it offers both whenever any is open, and says that the
 * server refuses them for an access that is not the caller's.
 */
export function SupportSection({ api, shop, now, who }: { api: AdminApi; shop: { id: string; name: string }; now: () => Date; who: Who }) {
  const { t, language } = useI18n();
  const { state, reload } = useLoad(
    (signal) => api.listSupport({ shopId: shop.id, open: true, cursor: null }, signal).then((page) => page.items),
    [api, shop.id],
  );
  const [step, setStep] = useState<Step>("idle");
  const [done, setDone] = useState<Done | null>(null);
  const [stale, setStale] = useState<ApiError | null>(null);
  const close = useSubmit((shopId: string, key) =>
    api.closeSupport(shopId, key).then(
      () => {
        setStep("idle");
        setDone({ kind: "closed" });
        setStale(null);
        reload();
      },
      (error: unknown) => {
        const failure = toApiError(error);
        // Nothing of the caller's was open: it ran out, the owner ended it, or it was a colleague's.
        if (failure.code === "SUPPORT_ACCESS_NOT_OPEN") {
          setStep("idle");
          setStale(failure);
          reload();
        }
        throw error;
      },
    ),
  );

  let body: ReactNode;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else {
    const open = state.data.filter((access) => isOpenAccess(access, now()));
    const mine = who.me === null ? open : open.filter((access) => access.adminId === who.me);
    const certain = who.me !== null;
    body = (
      <>
        {open.length === 0 ? (
          <p>{t("admin.support.none")}</p>
        ) : (
          <ul className="rows" aria-label={t("admin.support.openOnes")}>
            {open.map((access) => (
              <li key={access.id} className="row">
                <p>
                  {t(certain && access.adminId === who.me ? "admin.support.item.mine" : "admin.support.item", {
                    code: shortId(access.adminId),
                    date: formatInstant(access.endsAt, language),
                  })}
                </p>
                <p className="row__meta">{t("admin.support.item.reason", { reason: access.reason })}</p>
              </li>
            ))}
          </ul>
        )}
        {mine.length > 0 && !certain ? <p className="hint">{t("admin.support.unsure")}</p> : null}
        {step === "closing" ? (
          <Confirm
            question={t("admin.support.close.confirm", { shop: shop.name })}
            yes={t("admin.support.close.yes")}
            no={t("action.back")}
            pending={close.state.status === "pending"}
            error={close.state.status === "error" ? close.state.error : null}
            onYes={() => close.submit(shop.id)}
            onNo={() => {
              setStep("idle");
              close.reset();
            }}
          />
        ) : null}
        {step === "opening" ? (
          <OpenForm
            api={api}
            shop={shop}
            onOpened={(access) => {
              who.learn(access.adminId);
              setStep("idle");
              setDone({ kind: "opened", endsAt: access.endsAt });
              setStale(null);
              reload();
            }}
            onRefused={(error) => {
              setStep("idle");
              setStale(error);
              reload();
            }}
            onCancel={() => setStep("idle")}
          />
        ) : null}
        {step === "idle" ? (
          <p className="actions">
            {mine.length > 0 ? (
              <>
                <Link to={`/shops/${shop.id}/customers`} className="button button--primary">
                  {t("admin.support.customers")}
                </Link>
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    setStep("closing");
                    setDone(null);
                  }}
                >
                  {t("admin.support.close")}
                </button>
              </>
            ) : null}
            {certain && mine.length > 0 ? null : (
              <button
                type="button"
                className="button"
                onClick={() => {
                  setStep("opening");
                  setDone(null);
                }}
              >
                {t("admin.support.open")}
              </button>
            )}
          </p>
        ) : null}
      </>
    );
  }

  return (
    <section aria-labelledby="shop-support">
      <h2 id="shop-support">{t("admin.support.title")}</h2>
      <p>{t("admin.support.explain")}</p>
      {done ? (
        <p className="notice notice--done" role="status">
          {done.kind === "closed" ? t("admin.support.closed") : t("admin.support.opened", { date: formatInstant(done.endsAt, language) })}
        </p>
      ) : null}
      {/* The list was read again when this was refused; "try again" reads it once more and puts the
          refusal away. */}
      {stale ? (
        <Failure
          error={stale}
          onRetry={() => {
            setStale(null);
            reload();
          }}
        />
      ) : null}
      {body}
    </section>
  );
}
