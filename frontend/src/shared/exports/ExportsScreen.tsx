import { useEffect, useMemo, useState, type ReactNode } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import { type ApiError, isAbort, toApiError } from "../api";
import { useSubmit } from "../hooks";
import { type Column, useDesktop } from "../layout";
import { NotFoundScreen } from "../screens";
import { useMay, useWorkspace } from "../workspace/context";
import { Empty, errorText, Failure, formatInstant, Loading } from "../workspace/parts";
import {
  DAILY_LIMIT,
  type ExportJob,
  type ExportLink,
  type ExportsApi,
  exportsOf,
  exportState,
  isUnfinished,
  RETENTION_DAYS,
} from "./exportsApi";
import "./messages";

/** How often the list is read again while an export is being made. Calm: a workbook takes a while. */
export const POLL_MS = 5000;

const SHEETS = ["summary", "customers", "ledger", "promises", "goods"] as const;
const FAILURES: readonly string[] = ["interrupted", "timeout", "file_store", "internal"];
const NOT_READY: readonly string[] = ["queued", "running", "failed", "expired"];
const NOT_ALLOWED: readonly string[] = ["in_progress", "daily_limit"];
const CODE_LENGTH = 6;

type Jobs =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  /** `stale`: the list could not be read again; it is shown as it was, and no longer refreshes itself. */
  | { status: "ready"; items: ExportJob[]; stale: ApiError | null };

/**
 * The shop's exports, read when the screen opens and again at a calm interval for as long as one of
 * them is unfinished. Nothing is asked once every export is finished, and nothing after the screen is
 * left: the timer and the request in flight are both cancelled.
 */
function useExportJobs(client: ExportsApi, pollMs: number) {
  const [state, setState] = useState<Jobs>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = () => {
      client.list(controller.signal).then(
        (items) => {
          if (controller.signal.aborted) {
            return;
          }
          setState({ status: "ready", items, stale: null });
          if (items.some(isUnfinished)) {
            timer = setTimeout(read, pollMs);
          }
        },
        (error: unknown) => {
          if (controller.signal.aborted || isAbort(error)) {
            return;
          }
          const failure = toApiError(error);
          // A list already on the screen stays there; only a first read that fails has nothing to show.
          setState((current) => (current.status === "ready" ? { ...current, stale: failure } : { status: "error", error: failure }));
        },
      );
    };
    read();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [client, pollMs, attempt]);

  return {
    state,
    /** Reads the list again without taking it off the screen. */
    refresh: () => setAttempt((count) => count + 1),
    /** Starts over after a first read that failed. */
    reload: () => {
      setState({ status: "loading" });
      setAttempt((count) => count + 1);
    },
  };
}

function refusedRequest(error: ApiError, t: Translate): string | null {
  const reason = error.fields["reason"];
  if (error.code !== "EXPORT_NOT_ALLOWED" || typeof reason !== "string" || !NOT_ALLOWED.includes(reason)) {
    return null;
  }
  return reason === "daily_limit" ? t("exports.refused.daily_limit", { limit: DAILY_LIMIT }) : t("exports.refused.in_progress");
}

function notReady(error: ApiError, t: Translate): string | null {
  const status = error.fields["status"];
  if (error.code !== "EXPORT_NOT_READY" || typeof status !== "string" || !NOT_READY.includes(status)) {
    return null;
  }
  return t(`exports.notReady.${status as "queued" | "running" | "failed" | "expired"}`);
}

/**
 * A finished export is downloaded in two steps, as a receipt is opened. First a link is asked for with
 * the session; then the link, which needs no session and works for five minutes, is opened by the
 * person. The link is a credential: it lives in this component's state only.
 */
function Download({ client, jobId, onNotReady }: { client: ExportsApi; jobId: string; onNotReady: () => void }) {
  const { t, language } = useI18n();
  const { state, submit } = useSubmit(
    (id: string): Promise<ExportLink> =>
      client.link(id).catch((error: unknown) => {
        // The list is behind the server: it is read again, and the refusal stays shown.
        if (toApiError(error).code === "EXPORT_NOT_READY") {
          onNotReady();
        }
        throw error;
      }),
  );
  const pending = state.status === "pending";

  return (
    <div className="receipt">
      {state.status === "error" ? (
        <p className="field__error" role="alert">
          {notReady(state.error, t) ?? errorText(state.error, t)}
        </p>
      ) : null}
      {state.status === "done" ? (
        <>
          <p className="actions">
            <a className="button" href={state.result.url} target="_blank" rel="noopener noreferrer">
              {t("exports.link.open")}
            </a>
          </p>
          <p className="row__meta">{t("exports.link.valid", { date: formatInstant(state.result.expiresAt, language) })}</p>
        </>
      ) : null}
      <p className="actions">
        <button type="button" className="button button--small" onClick={() => submit(jobId)} disabled={pending}>
          {pending ? t("state.loading") : state.status === "done" ? t("exports.link.again") : t("exports.link.get")}
        </button>
      </p>
    </div>
  );
}

function Exports({ pollMs }: { pollMs: number }) {
  const { api, membershipId } = useWorkspace();
  const { t, language } = useI18n();
  const desktop = useDesktop();
  const client = useMemo(() => exportsOf(api), [api]);
  const { state, refresh, reload } = useExportJobs(client, pollMs);
  const request = useSubmit((_: null, key) =>
    client.request(key).then(refresh, (error: unknown) => {
      // "One is being made already" means the list is behind: it is read again.
      if (toApiError(error).code === "EXPORT_NOT_ALLOWED") {
        refresh();
      }
      throw error;
    }),
  );
  const pending = request.state.status === "pending";
  const failure = request.state.status === "error" ? request.state.error : null;

  const stateText = (job: ExportJob): string => {
    const known = exportState(job);
    return known === null ? job.status : t(`exports.state.${known}`);
  };
  const detail = (job: ExportJob): string | null => {
    switch (exportState(job)) {
      case "done": {
        const rows = job.rows === null ? null : t("exports.rows", { count: job.rows });
        const kept = job.availableUntil === null ? null : t("exports.keptUntil", { date: formatInstant(job.availableUntil, language) });
        return [rows, kept].filter((part) => part !== null).join(" · ") || null;
      }
      case "expired":
        return t("exports.expired.note");
      case "failed":
        return job.error !== null && FAILURES.includes(job.error)
          ? t(`exports.failed.${job.error as "interrupted" | "timeout" | "file_store" | "internal"}`)
          : t("exports.failed.other");
      case "queued":
      case "running":
        return t("exports.waiting.note");
      default:
        return null;
    }
  };
  const by = (job: ExportJob): string =>
    job.requestedBy === membershipId ? t("exports.by.you") : t("exports.by.member", { code: job.requestedBy.slice(-CODE_LENGTH) });
  const file = (job: ExportJob): ReactNode =>
    exportState(job) === "done" ? <Download client={client} jobId={job.id} onNotReady={refresh} /> : null;
  const stateCell = (job: ExportJob): ReactNode => {
    const more = detail(job);
    return (
      <>
        {stateText(job)}
        {more === null ? null : <span className="facts__note">{more}</span>}
      </>
    );
  };

  let list: ReactNode;
  if (state.status === "loading") {
    list = <Loading />;
  } else if (state.status === "error") {
    list = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    list = <Empty>{t("exports.none")}</Empty>;
  } else if (desktop) {
    const columns: Column<ExportJob>[] = [
      { id: "asked", header: t("exports.col.asked"), rowHeader: true, cell: (job) => formatInstant(job.createdAt, language) },
      { id: "by", header: t("exports.col.by"), cell: by },
      { id: "state", header: t("exports.col.state"), cell: stateCell },
      { id: "file", header: t("exports.col.file"), cell: file },
    ];
    list = <desktop.Table caption={t("exports.list")} columns={columns} items={state.items} rowKey={(job) => job.id} />;
  } else {
    list = (
      <ul className="rows" aria-label={t("exports.list")}>
        {state.items.map((job) => {
          const more = detail(job);
          return (
            <li key={job.id} className="row">
              <p className="row__link">
                <span className="row__name">{formatInstant(job.createdAt, language)}</span>
                <span className="row__amount">{stateText(job)}</span>
              </p>
              <p className="row__meta">{by(job)}</p>
              {more === null ? null : <p className="row__meta">{more}</p>}
              {file(job)}
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <>
      <section aria-labelledby="exports-title">
        <h2 id="exports-title">{t("exports.title")}</h2>
        <p>{t("exports.explain")}</p>
        <ul>
          {SHEETS.map((sheet) => (
            <li key={sheet}>{t(`exports.sheet.${sheet}`)}</li>
          ))}
        </ul>
        <p className="notice">{t("exports.personal")}</p>
        <p className="hint">{t("exports.limits", { days: RETENTION_DAYS, limit: DAILY_LIMIT })}</p>
        {failure ? (
          <p className="notice notice--error" role="alert">
            {refusedRequest(failure, t) ?? errorText(failure, t)}
          </p>
        ) : null}
        {request.state.status === "done" ? (
          <p className="notice notice--done" role="status">
            {t("exports.requested")}
          </p>
        ) : null}
        <p className="actions">
          <button type="button" className="button button--primary" onClick={() => request.submit(null)} disabled={pending}>
            {pending ? t("state.saving") : t("exports.request")}
          </button>
        </p>
      </section>
      <section aria-labelledby="exports-list">
        <h2 id="exports-list">{t("exports.list")}</h2>
        {state.status === "ready" && state.stale ? (
          <div className="notice notice--error" role="alert">
            <p>{t("exports.stale")}</p>
            <p>{errorText(state.stale, t)}</p>
            <button type="button" className="button" onClick={refresh}>
              {t("exports.refresh")}
            </button>
          </div>
        ) : null}
        {list}
      </section>
    </>
  );
}

/**
 * Export of the shop's book (REQ-028): what the file holds, the request, the recent exports with their
 * state, and the download. For managers and owners; in a suspended shop the server gives it to the
 * owner alone, so a manager is told that and nothing is asked. `after` is the other half of the section,
 * the import, which is loaded apart; nobody who gets the not-found screen is shown it.
 */
export default function ExportsScreen({ pollMs = POLL_MS, after = null }: { pollMs?: number; after?: ReactNode }) {
  const { role, shopMode } = useWorkspace();
  const can = useMay();
  const { t } = useI18n();
  if (!can("reports.export")) {
    return <NotFoundScreen />;
  }
  return (
    <>
      {shopMode === "suspended" && role !== "owner" ? (
        <section aria-labelledby="exports-title">
          <h2 id="exports-title">{t("exports.title")}</h2>
          <p className="notice notice--error">{t("exports.suspended")}</p>
        </section>
      ) : (
        <Exports pollMs={pollMs} />
      )}
      {after}
    </>
  );
}
