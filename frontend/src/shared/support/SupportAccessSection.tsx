import { type ReactNode, useMemo, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { type ApiError, toApiError } from "../api";
import { usePagedList, useSubmit } from "../hooks";
import { type Column, useDesktop } from "../layout";
import { useWorkspace } from "../workspace/context";
import { Confirm, Empty, Failure, formatInstant, Loading, LoadMore } from "../workspace/parts";
import "./messages";
import { adminCode, isOpen, type SupportAccess, type SupportApi, supportOf } from "./supportApi";

/** One access that is open now: who, why, until when, and the way to end it after a question. */
function OpenAccess({ client, access, onEnded, onStale }: { client: SupportApi; access: SupportAccess; onEnded: () => void; onStale: (error: ApiError) => void }) {
  const { t, language } = useI18n();
  const [asking, setAsking] = useState(false);
  const end = useSubmit((id: string, key) =>
    client.end(id, key).then(onEnded, (error: unknown) => {
      const failure = toApiError(error);
      // It ran out, or the administrator closed it, while this screen was open: nothing is left to end.
      if (failure.code === "SUPPORT_ACCESS_NOT_OPEN") {
        onStale(failure);
      }
      throw error;
    }),
  );
  const code = adminCode(access.adminId);

  return (
    <div className="notice notice--error">
      <p>{t("support.open", { code, date: formatInstant(access.endsAt, language) })}</p>
      <p>{t("support.open.reason", { reason: access.reason })}</p>
      {asking ? (
        <Confirm
          question={t("support.end.confirm", { code })}
          yes={t("support.end.yes")}
          no={t("support.end.no")}
          pending={end.state.status === "pending"}
          error={end.state.status === "error" ? end.state.error : null}
          onYes={() => end.submit(access.id)}
          onNo={() => {
            setAsking(false);
            end.reset();
          }}
        />
      ) : (
        <p className="actions">
          <button type="button" className="button" onClick={() => setAsking(true)}>
            {t("support.end")}
          </button>
        </p>
      )}
    </div>
  );
}

function Section({ onChanged }: { onChanged?: (() => void) | undefined }) {
  const { api, now } = useWorkspace();
  const { t, language } = useI18n();
  const desktop = useDesktop();
  const client = useMemo(() => supportOf(api), [api]);
  const { state, reload, loadMore } = usePagedList((cursor, signal) => client.list(cursor, signal), [client]);
  const [ended, setEnded] = useState(false);
  const [stale, setStale] = useState<ApiError | null>(null);
  const changed = () => {
    reload();
    onChanged?.();
  };

  const outcome = (access: SupportAccess): string => {
    if (isOpen(access, now())) {
      return t("support.outcome.active");
    }
    if (access.closedAt === null) {
      return t("support.outcome.expired");
    }
    const date = formatInstant(access.closedAt, language);
    if (access.closedBy === "owner" || access.closedBy === "admin") {
      return t(`support.outcome.${access.closedBy}`, { date });
    }
    return t("support.outcome.closed", { date });
  };

  let body: ReactNode;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else {
    const open = state.items.filter((access) => isOpen(access, now()));
    const columns: Column<SupportAccess>[] = [
      { id: "admin", header: t("support.col.admin"), rowHeader: true, cell: (access) => adminCode(access.adminId) },
      { id: "reason", header: t("support.col.reason"), cell: (access) => access.reason },
      { id: "from", header: t("support.col.from"), cell: (access) => formatInstant(access.startsAt, language) },
      { id: "to", header: t("support.col.to"), cell: (access) => formatInstant(access.endsAt, language) },
      { id: "outcome", header: t("support.col.outcome"), cell: outcome },
    ];
    body = (
      <>
        {open.length === 0 ? <p>{t("support.none.open")}</p> : null}
        {open.map((access) => (
          <OpenAccess
            key={access.id}
            client={client}
            access={access}
            onEnded={() => {
              setEnded(true);
              setStale(null);
              changed();
            }}
            onStale={(error) => {
              setStale(error);
              setEnded(false);
              changed();
            }}
          />
        ))}
        <h3>{t("support.history")}</h3>
        {state.items.length === 0 ? (
          <Empty>{t("support.history.none")}</Empty>
        ) : desktop ? (
          <desktop.Table caption={t("support.history")} columns={columns} items={state.items} rowKey={(access) => access.id} />
        ) : (
          <ul className="rows" aria-label={t("support.history")}>
            {state.items.map((access) => (
              <li key={access.id} className="row">
                <p className="row__link">
                  <span className="row__name">{adminCode(access.adminId)}</span>
                  <span className="row__amount">{outcome(access)}</span>
                </p>
                <p className="row__meta">{access.reason}</p>
                <p className="row__meta">
                  {formatInstant(access.startsAt, language)} — {formatInstant(access.endsAt, language)}
                </p>
              </li>
            ))}
          </ul>
        )}
        {state.nextCursor !== null ? <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} /> : null}
      </>
    );
  }

  return (
    <section aria-labelledby="support-title">
      <h2 id="support-title">{t("support.title")}</h2>
      <p>{t("support.explain")}</p>
      {ended ? (
        <p className="notice notice--done" role="status">
          {t("support.ended")}
        </p>
      ) : null}
      {stale ? <Failure error={stale} /> : null}
      {body}
    </section>
  );
}

/**
 * Support access under the shop's settings (REQ-059): whether an administrator can read the shop now,
 * with the reason they gave and until when, the way to end it, and every access the shop has had. It is
 * the owner's alone: nobody else is shown it and nothing is asked for them.
 */
export default function SupportAccessSection({ onChanged }: { /** Told when an access ends here. */ onChanged?: (() => void) | undefined }) {
  const { role } = useWorkspace();
  return role === "owner" ? <Section onChanged={onChanged} /> : null;
}
