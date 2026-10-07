import { type FormEvent, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import type { Language } from "../i18n/types";
import type { ApiError } from "../shared/api";
import { formatFullDate } from "../shared/format";
import { useSubmit } from "../shared/hooks";
import { Link } from "../shared/router";
import { useWorkspace } from "../shared/workspace/context";
import { Confirm, errorText, Failure, FieldError, Loading } from "../shared/workspace/parts";
import type { Deletion } from "./backoffice";
import "./messages";
import { useOffice } from "./office";

/** How long a shop goes on after its deletion is asked for (backend/src/qarz/domain/shop_deletion.py). */
export const WAITING_DAYS = 30;
const MAX_NAME_INPUT = 200;

/** The Tashkent day the shop's data is erased; null when the server gave no usable instant. */
export function erasureDay(deletion: Deletion, language: Language): string | null {
  if (deletion.due === null) {
    return null;
  }
  const due = new Date(deletion.due);
  return Number.isNaN(due.getTime()) ? null : formatFullDate(due, language);
}

/** A refusal that says this screen is behind the server: the request exists already, or no longer does. */
function isStale(error: ApiError): boolean {
  return error.code === "DELETION_ALREADY_REQUESTED" || error.code === "DELETION_NOT_REQUESTED";
}

function RequestForm({ onStale }: { onStale: (error: ApiError) => void }) {
  const { shopName } = useWorkspace();
  const { office, reloadDeletion } = useOffice();
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const { state, submit } = useSubmit((typed: string, key) =>
    office.requestDeletion(typed, key).then(reloadDeletion, (error: ApiError) => {
      if (isStale(error)) {
        onStale(error);
      }
      throw error;
    }),
  );

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const typed = name.trim();
    // Whether it is the shop's name is the server's to say: it holds the name as it is now.
    if (typed === "") {
      setProblem(t("deletion.confirm.required"));
      return;
    }
    submit(typed);
  };

  const failure = state.status === "error" ? state.error : null;
  const mismatch = failure?.code === "VALIDATION" && "confirm_name" in failure.fields;
  const shown = problem ?? (mismatch ? t("deletion.confirm.mismatch") : null);
  const pending = state.status === "pending";

  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      {failure && !mismatch ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="deletion-name">{t("deletion.confirm.label")}</label>
        <input
          id="deletion-name"
          className="input"
          value={name}
          maxLength={MAX_NAME_INPUT}
          autoComplete="off"
          aria-invalid={shown !== null}
          aria-describedby="deletion-name-hint deletion-name-error"
          onChange={(event) => {
            setName(event.target.value);
            setProblem(null);
          }}
        />
        {shopName ? (
          <p className="field__hint" id="deletion-name-hint">
            {t("deletion.confirm.hint", { name: shopName })}
          </p>
        ) : null}
        <FieldError id="deletion-name-error" message={shown} />
      </div>
      <p className="actions">
        <button type="submit" className="button" disabled={pending}>
          {pending ? t("state.saving") : t("deletion.submit")}
        </button>
      </p>
    </form>
  );
}

function Pending({ deletion, onStale }: { deletion: Deletion; onStale: (error: ApiError) => void }) {
  const { office, reloadDeletion } = useOffice();
  const { t, language } = useI18n();
  const [asking, setAsking] = useState(false);
  const cancel = useSubmit((_: null, key) =>
    office.cancelDeletion(key).then(reloadDeletion, (error: ApiError) => {
      if (isStale(error)) {
        onStale(error);
      }
      throw error;
    }),
  );
  const day = erasureDay(deletion, language);

  return (
    <div className="notice notice--error">
      <p>{day === null ? t("deletion.pending.noDate") : t("deletion.pending", { date: day })}</p>
      {asking ? (
        <Confirm
          question={t("deletion.cancel.confirm")}
          yes={t("deletion.cancel.yes")}
          no={t("confirm.no")}
          pending={cancel.state.status === "pending"}
          error={cancel.state.status === "error" ? cancel.state.error : null}
          onYes={() => cancel.submit(null)}
          onNo={() => {
            setAsking(false);
            cancel.reset();
          }}
        />
      ) : (
        <p className="actions">
          <button type="button" className="button" onClick={() => setAsking(true)}>
            {t("deletion.cancel")}
          </button>
        </p>
      )}
    </div>
  );
}

function DeleteShop() {
  const { deletion, reloadDeletion } = useOffice();
  const { t } = useI18n();
  // A refusal that outlives the form it came from: the state is read again, and the refusal stays shown.
  const [stale, setStale] = useState<ApiError | null>(null);
  const onStale = (error: ApiError) => {
    setStale(error);
    reloadDeletion();
  };

  return (
    <section aria-labelledby="deletion-title">
      <h2 id="deletion-title">{t("deletion.title")}</h2>
      <p>{t("deletion.explain.wait", { days: WAITING_DAYS })}</p>
      <p>{t("deletion.explain.erased")}</p>
      {/* What is erased cannot be brought back: the way to keep a copy is offered before the request (REQ-048). */}
      <p className="notice">
        <span>{t("deletion.export")}</span>{" "}
        <Link to="/import-export">{t("deletion.export.open")}</Link>
      </p>
      {stale ? <Failure error={stale} /> : null}
      {deletion.status === "loading" ? <Loading /> : null}
      {deletion.status === "error" ? <Failure error={deletion.error} onRetry={reloadDeletion} /> : null}
      {deletion.status === "ready" && deletion.data !== null ? (
        deletion.data.status === "deletion_pending" ? (
          <Pending deletion={deletion.data} onStale={onStale} />
        ) : (
          <RequestForm onStale={onStale} />
        )
      ) : null}
    </section>
  );
}

/**
 * Deleting the shop (REQ-048), under its settings: what is erased and when, the shop's name typed to
 * confirm, and while the request waits, its date and the way to cancel it. It is the owner's alone.
 */
export function DeletionSection() {
  const { role } = useWorkspace();
  return role === "owner" ? <DeleteShop /> : null;
}
