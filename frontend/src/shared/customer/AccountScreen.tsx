import { useState, type ReactNode } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { MessageKey } from "../../i18n/types";
import type { AccountApi, AccountDetail, AccountEntry, ApiError, RemovalOutcome } from "../api";
import { formatCalendarDay, formatMoney } from "../format";
import { type Submission, useLoad, useSubmit } from "../hooks";
import { parseIsoDate } from "../promise";
import { GoodsList } from "../workspace/GoodsEditor";
import { Confirm, ENTRY_KIND_LABELS, errorText, Failure, formatInstant, Loading, ReasonForm } from "../workspace/parts";

/**
 * Whether "dispute" is offered for an entry: only one that adds to the debt, is not reversed and has
 * never been disputed (BR-11). The server also checks how old the entry is; its refusal is shown.
 */
export function canDispute(entry: AccountEntry): boolean {
  return (entry.kind === "credit" || entry.kind === "opening") && !entry.reversed && entry.dispute === null;
}

const DISPUTE_STATE: Readonly<Record<string, MessageKey>> = {
  open: "my.dispute.open",
  declined: "my.dispute.declined",
  withdrawn: "my.dispute.withdrawn",
  reversed: "my.dispute.reversed",
};

/** Why the server refused a dispute, in the customer's words (`fields.reason` of DISPUTE_NOT_ALLOWED). */
const REFUSALS: Readonly<Record<string, MessageKey>> = {
  not_a_debt: "my.dispute.refused.notADebt",
  reversed: "my.dispute.refused.reversed",
  already_disputed: "my.dispute.refused.alreadyDisputed",
  too_late: "my.dispute.refused.tooLate",
  not_open: "my.dispute.refused.notOpen",
};

function refusalDetail(error: ApiError | null, t: (key: MessageKey) => string): string | null {
  const key = error?.code === "DISPUTE_NOT_ALLOWED" ? REFUSALS[error.fields["reason"] ?? ""] : undefined;
  return key ? t(key) : null;
}

/**
 * One entry as the customer sees it. The only things a customer can do about an entry are to dispute it
 * and to take that dispute back: they are never asked to confirm an entry (BR-10), so there is no
 * such control here and there must never be one.
 */
function EntryRow({
  entry,
  disputing,
  busy,
  disputeError,
  withdrawError,
  onAsk,
  onDispute,
  onCancel,
  onWithdraw,
}: {
  entry: AccountEntry;
  disputing: boolean;
  busy: boolean;
  disputeError: ApiError | null;
  withdrawError: ApiError | null;
  onAsk: () => void;
  onDispute: (reason: string) => void;
  onCancel: () => void;
  onWithdraw: (disputeId: string) => void;
}) {
  const { t, language } = useI18n();
  const promised = entry.promisedDate === null ? null : parseIsoDate(entry.promisedDate);
  const dispute = entry.dispute;
  return (
    <li className={entry.reversed ? "row row--struck" : "row"}>
      <p className="row__link">
        <span className="row__name">{t(ENTRY_KIND_LABELS[entry.kind] ?? "entry.kind.other")}</span>
        <span className="row__amount">{formatMoney(entry.amount, language)}</span>
      </p>
      <p className="row__meta">{formatInstant(entry.createdAt, language)}</p>
      {entry.lines.length > 0 ? <GoodsList lines={entry.lines} /> : null}
      {promised && !entry.reversed ? (
        <p className="row__meta">{t("entry.promised", { date: formatCalendarDay(promised, language) })}</p>
      ) : null}
      {entry.reversed ? <p className="row__meta">{t("entry.reversed")}</p> : null}

      {dispute ? (
        <div className="notice">
          <p className="row__warning">{t(DISPUTE_STATE[dispute.status] ?? "my.dispute.closed")}</p>
          <p>{t("my.dispute.reason", { reason: dispute.reason })}</p>
          {dispute.declineReason ? <p>{t("my.dispute.declineReason", { reason: dispute.declineReason })}</p> : null}
          {withdrawError ? (
            <div role="alert">
              <p className="field__error">{errorText(withdrawError, t)}</p>
              {refusalDetail(withdrawError, t) ? <p className="field__error">{refusalDetail(withdrawError, t)}</p> : null}
            </div>
          ) : null}
          {dispute.status === "open" ? (
            <p className="actions">
              <button type="button" className="button button--small" onClick={() => onWithdraw(dispute.id)} disabled={busy}>
                {t("my.dispute.withdraw")}
              </button>
            </p>
          ) : null}
        </div>
      ) : null}

      {canDispute(entry) ? (
        disputing ? (
          <ReasonForm
            id={`dispute-${entry.id}`}
            label={t("my.dispute.label")}
            hint={t("my.dispute.hint")}
            submitLabel={t("my.dispute.submit")}
            pending={busy}
            error={disputeError}
            errorDetail={refusalDetail(disputeError, t)}
            onSubmit={onDispute}
            onCancel={onCancel}
          />
        ) : (
          <button type="button" className="button button--small" onClick={onAsk} disabled={busy}>
            {t("my.dispute.action")}
          </button>
        )
      ) : null}
    </li>
  );
}

type Asking = "disconnect" | "removal" | null;

function failureOf(state: Submission<unknown>): ApiError | null {
  return state.status === "error" ? state.error : null;
}

function Detail({
  account,
  api,
  removal,
  reload,
  onDisconnected,
  onRemoval,
}: {
  account: AccountDetail;
  api: AccountApi;
  /** The server's answer to a removal request made on this screen, kept across the reload. */
  removal: RemovalOutcome | null;
  reload: () => void;
  onDisconnected: () => void;
  onRemoval: (outcome: RemovalOutcome) => void;
}) {
  const { t, language } = useI18n();
  const [disputing, setDisputing] = useState<string | null>(null);
  const [asking, setAsking] = useState<Asking>(null);

  // These writes take no idempotency key; `useSubmit` is used for its lock against a double tap.
  // A reload may bring the new account without this component being replaced, so each write also
  // closes the question it answered.
  const dispute = useSubmit((payload: { entryId: string; reason: string }) =>
    api.openDispute(payload.entryId, payload.reason).then(() => {
      setDisputing(null);
      reload();
    }),
  );
  const withdraw = useSubmit((disputeId: string) => api.withdrawDispute(disputeId).then(reload));
  const disconnect = useSubmit(() => api.disconnect().then(onDisconnected));
  const remove = useSubmit(() =>
    api.requestRemoval().then((outcome) => {
      setAsking(null);
      onRemoval(outcome);
    }),
  );
  const busy = [dispute, withdraw, disconnect, remove].some((write) => write.state.status === "pending");

  const ask = (next: Asking) => {
    setAsking(next);
    disconnect.reset();
    remove.reset();
  };

  return (
    <>
      <h2 className="subject">{account.shopName}</h2>
      <p className="row__meta">{t("my.knownAs", { name: account.displayName })}</p>

      <p className="balance balance--large">
        <span>{t("my.balance")}</span> <strong>{formatMoney(account.balance, language)}</strong>
      </p>
      {account.overdueAmount > 0 ? (
        <p className="row__warning">{t("overdue.amount", { amount: formatMoney(account.overdueAmount, language) })}</p>
      ) : null}
      {account.dueToday > 0 ? (
        <p className="row__meta">{t("due.todayAmount", { amount: formatMoney(account.dueToday, language) })}</p>
      ) : null}

      <section aria-labelledby="my-entries-title">
        <h2 id="my-entries-title">{t("customer.entries")}</h2>
        {account.entries.length === 0 ? (
          <p className="state">{t("customer.entries.none")}</p>
        ) : (
          <ul className="rows">
            {account.entries.map((entry) => (
              <EntryRow
                key={entry.id}
                entry={entry}
                disputing={disputing === entry.id}
                busy={busy}
                disputeError={disputing === entry.id ? failureOf(dispute.state) : null}
                withdrawError={entry.dispute?.status === "open" ? failureOf(withdraw.state) : null}
                onAsk={() => {
                  setDisputing(entry.id);
                  dispute.reset();
                }}
                onDispute={(reason) => dispute.submit({ entryId: entry.id, reason })}
                onCancel={() => {
                  setDisputing(null);
                  dispute.reset();
                }}
                onWithdraw={withdraw.submit}
              />
            ))}
          </ul>
        )}
        {account.entriesTotal > account.entries.length ? (
          <p className="row__meta">
            {t("customer.entries.partial", { shown: account.entries.length, total: account.entriesTotal })}
          </p>
        ) : null}
      </section>

      <section aria-labelledby="my-manage-title">
        <h2 id="my-manage-title">{t("my.manage")}</h2>
        {removal?.waitingForBalance != null ? (
          <p className="notice notice--done" role="status">
            {t("my.removal.waiting", { amount: formatMoney(removal.waitingForBalance, language) })}
          </p>
        ) : account.removalRequested ? (
          <p className="notice">{t("my.removal.requested")}</p>
        ) : null}

        {asking === "disconnect" ? (
          <Confirm
            question={t("my.disconnect.confirm", { shop: account.shopName })}
            yes={t("my.disconnect.yes")}
            no={t("action.cancel")}
            pending={busy}
            error={failureOf(disconnect.state)}
            onYes={() => disconnect.submit("disconnect")}
            onNo={() => ask(null)}
          />
        ) : asking === "removal" ? (
          <Confirm
            question={
              <>
                <p>{t("my.removal.confirm")}</p>
                <ul className="consequences">
                  <li>{t("my.removal.consequence.final")}</li>
                  <li>{t("my.removal.consequence.debt")}</li>
                </ul>
              </>
            }
            yes={t("my.removal.yes")}
            no={t("action.cancel")}
            pending={busy}
            error={failureOf(remove.state)}
            onYes={() => remove.submit("removal")}
            onNo={() => ask(null)}
          />
        ) : (
          <p className="actions">
            <button type="button" className="button" onClick={() => ask("disconnect")} disabled={busy}>
              {t("my.disconnect")}
            </button>
            {account.removalRequested || removal !== null ? null : (
              <button type="button" className="button" onClick={() => ask("removal")} disabled={busy}>
                {t("my.removal")}
              </button>
            )}
          </p>
        )}
      </section>
    </>
  );
}

/**
 * A person's own account in one shop (REQ-019 to REQ-021, REQ-029, REQ-016): what they owe, what is
 * late, and the entries, exactly as the server returns them for this link and nothing else.
 */
export function AccountScreen({ api, back }: { api: AccountApi; /** The way to the list of accounts. */ back: ReactNode }) {
  const { t } = useI18n();
  const { state, reload } = useLoad((signal) => api.read(signal), [api]);
  const [ended, setEnded] = useState<"disconnected" | "removed" | null>(null);
  const [removal, setRemoval] = useState<RemovalOutcome | null>(null);

  if (ended !== null) {
    return (
      <>
        <p className="notice notice--done" role="status">
          {t(ended === "removed" ? "my.removal.done" : "my.disconnect.done")}
        </p>
        <p className="actions">{back}</p>
      </>
    );
  }
  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    // Not theirs, ended, or not a link at all: the server gives the same answer, and so does this screen.
    return state.error.code === "NOT_FOUND" ? (
      <>
        <p>{t("my.gone")}</p>
        <p className="actions">{back}</p>
      </>
    ) : (
      <Failure error={state.error} onRetry={reload} />
    );
  }
  return (
    <Detail
      account={state.data}
      api={api}
      removal={removal}
      reload={reload}
      onDisconnected={() => setEnded("disconnected")}
      onRemoval={(outcome) => {
        if (outcome.removed) {
          setEnded("removed");
        } else {
          setRemoval(outcome);
          reload();
        }
      }}
    />
  );
}
