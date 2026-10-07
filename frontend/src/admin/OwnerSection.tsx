import { type FormEvent, useRef, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import type { ApiError } from "../shared/api";
import { useSubmit } from "../shared/hooks";
import { Confirm, FieldError } from "../shared/workspace/parts";
import type { AdminApi, AdminShop } from "./adminApi";
import "./messages";
import { cleanReason, isCode, isOwnerRefusal, REASON_MAX, REASON_MIN, telegramId } from "./rules";

type Draft = { newOwnerTgId: number; reason: string };
type Problems = { owner: string | null; reason: string | null; code: string | null };
const NO_PROBLEMS: Problems = { owner: null, reason: null, code: null };

/**
 * Gives the shop to another person, for an owner who lost their Telegram account (operations runbook 7).
 * The new owner is named by Telegram identifier and must have started the bot. The change needs a reason
 * and, every time, a fresh code from the authenticator; nothing is sent before the "yes" that follows
 * the statement of what will happen.
 */
export function OwnerSection({
  api,
  shop,
  deletionPending,
  onChanged,
}: {
  api: AdminApi;
  shop: AdminShop;
  deletionPending: boolean;
  onChanged: (changed: AdminShop) => void;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState(false);
  const [owner, setOwner] = useState("");
  const [reason, setReason] = useState("");
  const [code, setCode] = useState("");
  const [problems, setProblems] = useState<Problems>(NO_PROBLEMS);
  const [draft, setDraft] = useState<Draft | null>(null);
  // The code is not part of what is asked for: the same change sent again with a newer code is the same
  // request to the server, so it is read when the request is made and never compared.
  const codeNow = useRef("");
  const { state, submit, reset } = useSubmit((payload: Draft, key) =>
    api.reassignOwner(shop.id, { ...payload, code: codeNow.current }, key).then((changed) => {
      setOpen(false);
      setDraft(null);
      setOwner("");
      setReason("");
      setCode("");
      setDone(true);
      onChanged(changed);
    }),
  );
  const pending = state.status === "pending";
  const failure: ApiError | null = state.status === "error" ? state.error : null;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const id = telegramId(owner);
    const clean = cleanReason(reason);
    const found: Problems = {
      owner: id === null ? t("admin.owner.newOwner.invalid") : id === shop.ownerTgId ? t("admin.owner.refused.already_owner") : null,
      reason: clean === null ? t("admin.reason.invalid", { min: REASON_MIN, max: REASON_MAX }) : null,
      code: isCode(code.trim()) ? null : t("door.code.invalid"),
    };
    setProblems(found);
    if (id !== null && clean !== null && found.owner === null && found.code === null) {
      reset();
      codeNow.current = code.trim();
      setDraft({ newOwnerTgId: id, reason: clean });
    }
  };

  if (draft !== null) {
    const refusal = failure?.code === "OWNER_REASSIGNMENT_REFUSED" ? failure.fields["reason"] : undefined;
    return (
      <section aria-labelledby="shop-owner">
        <h2 id="shop-owner">{t("admin.owner.title")}</h2>
        <Confirm
          question={
            <>
              {isOwnerRefusal(refusal) ? <p className="field__error">{t(`admin.owner.refused.${refusal}`)}</p> : null}
              <p>{t("admin.owner.confirm", { shop: shop.name, id: draft.newOwnerTgId })}</p>
              <p>{t("admin.change.confirm.reason", { reason: draft.reason })}</p>
              <ul>
                <li>{t("admin.owner.effect.old")}</li>
                <li>{t("admin.owner.effect.transfer")}</li>
                {deletionPending ? <li>{t("admin.owner.effect.deletion")}</li> : null}
                <li>{t("admin.owner.effect.told")}</li>
              </ul>
            </>
          }
          yes={t("admin.owner.yes")}
          no={t("action.back")}
          pending={pending}
          error={failure}
          onYes={() => submit(draft)}
          onNo={() => {
            // Back to the form with what was typed, but not the code: it is used up or about to expire.
            setDraft(null);
            setCode("");
            reset();
          }}
        />
      </section>
    );
  }

  return (
    <section aria-labelledby="shop-owner">
      <h2 id="shop-owner">{t("admin.owner.title")}</h2>
      {done ? (
        <p className="notice notice--done" role="status">
          {t("admin.owner.done")}
        </p>
      ) : null}
      {!open ? (
        <p className="actions">
          <button
            type="button"
            className="button"
            onClick={() => {
              setOpen(true);
              setDone(false);
            }}
          >
            {t("admin.owner.change")}
          </button>
        </p>
      ) : (
        <form className="form notice" onSubmit={onSubmit} noValidate aria-label={t("admin.owner.change")}>
          <p>{t("admin.owner.when")}</p>
          <div className="field">
            <label htmlFor="owner-new">{t("admin.owner.newOwner")}</label>
            <input
              id="owner-new"
              className="input"
              inputMode="numeric"
              autoComplete="off"
              maxLength={19}
              value={owner}
              aria-invalid={problems.owner !== null}
              aria-describedby="owner-new-hint owner-new-error"
              onChange={(event) => {
                setOwner(event.target.value);
                setProblems((current) => ({ ...current, owner: null }));
              }}
            />
            <p className="field__hint" id="owner-new-hint">
              {t("admin.owner.newOwner.hint")}
            </p>
            <FieldError id="owner-new-error" message={problems.owner} />
          </div>
          <div className="field">
            <label htmlFor="owner-reason">{t("admin.reason")}</label>
            <textarea
              id="owner-reason"
              className="input input--text"
              rows={2}
              maxLength={2000}
              value={reason}
              aria-invalid={problems.reason !== null}
              aria-describedby="owner-reason-hint owner-reason-error"
              onChange={(event) => {
                setReason(event.target.value);
                setProblems((current) => ({ ...current, reason: null }));
              }}
            />
            <p className="field__hint" id="owner-reason-hint">
              {t("admin.owner.reason.hint")}
            </p>
            <FieldError id="owner-reason-error" message={problems.reason} />
          </div>
          <div className="field">
            <label htmlFor="owner-code">{t("admin.settings.code")}</label>
            <input
              id="owner-code"
              className="input"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              aria-invalid={problems.code !== null}
              aria-describedby="owner-code-hint owner-code-error"
              onChange={(event) => {
                setCode(event.target.value);
                setProblems((current) => ({ ...current, code: null }));
              }}
            />
            <p className="field__hint" id="owner-code-hint">
              {t("admin.owner.code.hint")}
            </p>
            <FieldError id="owner-code-error" message={problems.code} />
          </div>
          <p className="actions">
            <button type="submit" className="button button--primary">
              {t("admin.change.continue")}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setOpen(false);
                setProblems(NO_PROBLEMS);
              }}
            >
              {t("action.cancel")}
            </button>
          </p>
        </form>
      )}
    </section>
  );
}
