import { type FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { Language, MessageKey } from "../../i18n/types";
import { type ApiError, isAbort, toApiError } from "../api";
import { formatCalendarDay, formatMoney } from "../format";
import { useLoad, useSubmit } from "../hooks";
import { type Column, useDesktop } from "../layout";
import { parseIsoDate } from "../promise";
import { NotFoundScreen } from "../screens";
import { useMay, useWorkspace } from "../workspace/context";
import { Confirm, Empty, errorText, Failure, FieldError, formatInstant, Loading } from "../workspace/parts";
import {
  canDiscard,
  FILE_PROBLEMS,
  type FileProblem,
  IMPORT_COLUMNS,
  IMPORT_MAX_BYTES,
  IMPORT_MAX_ROWS,
  IMPORT_STATES,
  type ImportBatch,
  type ImportCounts,
  type ImportPreview,
  type ImportsApi,
  importsOf,
  type ImportState,
  isUnfinished,
  type PreviewRow,
  ROW_PROBLEMS,
  type RowError,
  type RowProblem,
  STEP_REFUSALS,
  type StepRefusal,
  TEMPLATE_NAME,
  UNDO_HOURS,
  UNDO_REFUSALS,
  type UndoRefusal,
} from "./importsApi";
import "./messages";

/** How often the list is read again while the worker checks, applies or undoes. Calm: a file takes a while. */
export const POLL_MS = 5000;
/** How many rows of a preview are drawn before the person asks for all of them. */
export const PREVIEW_ROWS = 100;

const MAX_MB = IMPORT_MAX_BYTES / (1024 * 1024);
const CODE_LENGTH = 6;
/** What the file chooser offers; the name is checked again before sending, and the bytes by the server. */
const ACCEPT = ".xlsx,.csv";
const NONE = "—";
const has = <T extends string>(words: readonly T[], word: string | undefined): word is T => (words as readonly string[]).includes(word ?? "");

/** Why a file as a whole is refused, in the person's words; a word this client does not know gets the general one. */
export function problemText(problem: string, t: Translate): string {
  if (!has(FILE_PROBLEMS, problem)) {
    return t("imports.problem.other");
  }
  const key = `imports.problem.${problem satisfies FileProblem}` as const;
  return problem === "too_large" ? t(key, { size: MAX_MB }) : problem === "too_many_rows" ? t(key, { rows: IMPORT_MAX_ROWS }) : t(key);
}

/** What can be told about a chosen file before it is sent. What it holds only the server can say, from its bytes. */
export function uploadProblem(file: { name: string; size: number } | null): "required" | "empty" | "too_large" | "type" | null {
  if (file === null) {
    return "required";
  }
  if (!/\.(xlsx|csv)$/i.test(file.name)) {
    return "type";
  }
  if (file.size === 0) {
    return "empty";
  }
  return file.size > IMPORT_MAX_BYTES ? "too_large" : null;
}

function uploadProblemText(problem: NonNullable<ReturnType<typeof uploadProblem>>, t: Translate): string {
  return problem === "required" ? t("imports.file.required") : problem === "type" ? t("imports.file.type") : problemText(problem, t);
}

/** A refused upload next to the file field: the server's word for the file, or a body it would not read. */
function refusedUpload(error: ApiError, t: Translate): string | null {
  if (error.code === "BODY_TOO_LARGE") {
    return problemText("too_large", t);
  }
  const word = error.fields["file"];
  return error.code === "VALIDATION" && word !== undefined ? problemText(word, t) : null;
}

/** Why a step the worker was asked for was not done. */
function reasonText(reason: string, t: Translate): string {
  if (has(UNDO_REFUSALS, reason)) {
    return undoRefusedText(reason, t);
  }
  if (has(STEP_REFUSALS, reason)) {
    return t(`imports.refused.reason.${reason satisfies StepRefusal as Exclude<StepRefusal, "balance_used">}`);
  }
  return has(FILE_PROBLEMS, reason) ? problemText(reason, t) : t("imports.refused.reason.other", { reason });
}

function undoRefusedText(reason: UndoRefusal, t: Translate): string {
  return reason === "too_late" ? t("imports.undo.refused.too_late", { hours: UNDO_HOURS }) : t(`imports.undo.refused.${reason}`);
}

const STEPS: Readonly<Record<string, MessageKey>> = {
  check: "imports.refused.step.check",
  apply: "imports.refused.step.apply",
  undo: "imports.refused.step.undo",
};

type Batches =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  /** `stale`: the list could not be read again; it is shown as it was, and no longer refreshes itself. */
  | { status: "ready"; items: ImportBatch[]; currencyColumn: boolean; stale: ApiError | null };

/**
 * The shop's imports, read when the screen opens and again at a calm interval for as long as the worker
 * has a step to do on one of them. Nothing is asked once every import rests, and nothing after the
 * screen is left: the timer and the request in flight are both cancelled.
 */
function useImports(client: ImportsApi, pollMs: number) {
  const [state, setState] = useState<Batches>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = () => {
      client.listed(controller.signal).then(
        ({ items, currencyColumn }) => {
          if (controller.signal.aborted) {
            return;
          }
          setState({ status: "ready", items, currencyColumn, stale: null });
          if (items.some(isUnfinished)) {
            timer = setTimeout(read, pollMs);
          }
        },
        (error: unknown) => {
          if (controller.signal.aborted || isAbort(error)) {
            return;
          }
          const failure = toApiError(error);
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
    refresh: () => setAttempt((count) => count + 1),
    reload: () => {
      setState({ status: "loading" });
      setAttempt((count) => count + 1);
    },
  };
}

/**
 * The empty workbook, in two steps like every other file: it is asked for with the session, then saved
 * by the person from a link. Nothing is saved by itself.
 */
function Template({ client }: { client: ImportsApi }) {
  const { t } = useI18n();
  const [state, setState] = useState<{ pending: boolean; url: string | null; error: ApiError | null }>({ pending: false, url: null, error: null });
  const url = state.url;
  useEffect(() => (url === null ? undefined : () => URL.revokeObjectURL(url)), [url]);
  const get = () => {
    setState({ pending: true, url: null, error: null });
    client.template().then(
      (file) => setState({ pending: false, url: URL.createObjectURL(file), error: null }),
      (error: unknown) => setState({ pending: false, url: null, error: toApiError(error) }),
    );
  };
  return (
    <div className="receipt">
      {state.error ? (
        <p className="field__error" role="alert">
          {errorText(state.error, t)}
        </p>
      ) : null}
      {url === null ? (
        <p className="actions">
          <button type="button" className="button button--small" onClick={get} disabled={state.pending}>
            {state.pending ? t("state.loading") : t("imports.template.get")}
          </button>
        </p>
      ) : (
        <>
          <p className="row__meta" role="status">
            {t("imports.template.ready")}
          </p>
          <p className="actions">
            <a className="button" href={url} download={TEMPLATE_NAME}>
              {t("imports.template.save")}
            </a>
          </p>
        </>
      )}
    </div>
  );
}

function UploadForm({ client, onUploaded }: { client: ImportsApi; onUploaded: (batch: ImportBatch) => void }) {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // The file is not part of what `useSubmit` compares, so a changed file is told to it by name and size.
  const { state, submit } = useSubmit((_: { file: string }, key) => {
    if (file === null) {
      throw new RangeError("no file");
    }
    return client.upload(file, key).then(onUploaded);
  });
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found = uploadProblem(file);
    setProblem(found === null ? null : uploadProblemText(found, t));
    if (found === null && file !== null) {
      submit({ file: `${file.name}/${file.size}/${file.lastModified}` });
    }
  };
  const failure = state.status === "error" ? state.error : null;
  const refused = failure === null ? null : refusedUpload(failure, t);
  const shown = problem ?? refused;
  const pending = state.status === "pending";
  return (
    <form className="form" onSubmit={onSubmit} noValidate aria-label={t("imports.upload")}>
      {failure !== null && refused === null ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      {state.status === "done" ? (
        <p className="notice notice--done" role="status">
          {t("imports.uploaded")}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="import-file">{t("imports.file")}</label>
        <input
          id="import-file"
          type="file"
          className="input"
          accept={ACCEPT}
          aria-invalid={shown !== null}
          aria-describedby="import-file-hint import-file-error"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setProblem(null);
          }}
        />
        <p className="field__hint" id="import-file-hint">
          {t("imports.file.hint", { size: MAX_MB })}
        </p>
        <FieldError id="import-file-error" message={shown} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("imports.upload")}
        </button>
      </p>
    </form>
  );
}

/**
 * One thing asked of an import: a question first, and nothing sent until "yes". What is sent is fixed
 * when the question is asked, so it is what the person was looking at. A refusal the catalog has words
 * for ends the question; any other failure leaves it open, to be answered again with the same key.
 */
function Step<P>({
  label,
  question,
  yes,
  asked,
  payload,
  send,
  refusedText,
  primary = false,
}: {
  label: string;
  question: string;
  yes: string;
  /** Said once the server has taken the request, until the list shows what became of it. */
  asked: string;
  payload: P;
  send: (payload: P, idempotencyKey: string) => Promise<unknown>;
  /** The words for a refusal that ends the question; "" when the caller says them itself; null leaves it open. */
  refusedText: (error: ApiError) => string | null;
  primary?: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState<{ payload: P } | null>(null);
  const { state, submit, reset } = useSubmit(send);
  const failure = state.status === "error" ? state.error : null;
  const ended = failure === null ? null : refusedText(failure);
  if (state.status === "done") {
    return (
      <p className="notice notice--done" role="status">
        {asked}
      </p>
    );
  }
  if (open !== null && ended === null) {
    return (
      <Confirm
        question={question}
        yes={yes}
        no={t("action.back")}
        pending={state.status === "pending"}
        error={failure}
        onYes={() => submit(open.payload)}
        onNo={() => {
          setOpen(null);
          reset();
        }}
      />
    );
  }
  return (
    <>
      {ended ? (
        <p className="notice notice--error" role="alert">
          {ended}
        </p>
      ) : null}
      <p className="actions">
        <button
          type="button"
          className={primary ? "button button--primary" : "button"}
          onClick={() => {
            reset();
            setOpen({ payload });
          }}
        >
          {label}
        </button>
      </p>
    </>
  );
}

function columnText(column: string, t: Translate): string {
  return has(IMPORT_COLUMNS, column) ? t(`imports.column.${column}`) : column;
}

function rowProblemText(code: string, t: Translate): string {
  return has(ROW_PROBLEMS, code) ? t(`imports.row.${code satisfies RowProblem}`) : code;
}

/** The problems of the rows, by row and column. The cells' content is not here: the server does not return it. */
function RowErrors({ errors }: { errors: readonly RowError[] }) {
  const { t } = useI18n();
  const desktop = useDesktop();
  const key = (error: RowError) => `${error.row}/${error.column}/${error.code}`;
  return (
    <section aria-labelledby="import-errors">
      <h3 id="import-errors">{t("imports.errors.title")}</h3>
      <p>{t("imports.errors.explain")}</p>
      {desktop ? (
        <desktop.Table
          caption={t("imports.errors.title")}
          columns={[
            { id: "row", header: t("imports.errors.col.row"), numeric: true, rowHeader: true, cell: (error: RowError) => error.row },
            { id: "column", header: t("imports.errors.col.column"), cell: (error: RowError) => columnText(error.column, t) },
            { id: "problem", header: t("imports.errors.col.problem"), cell: (error: RowError) => rowProblemText(error.code, t) },
          ]}
          items={errors}
          rowKey={key}
        />
      ) : (
        <ul className="rows" aria-label={t("imports.errors.title")}>
          {errors.map((error) => (
            <li key={key(error)} className="row">
              <p className="row__link">
                <span className="row__name">{`${t("imports.errors.col.row")} ${error.row} · ${columnText(error.column, t)}`}</span>
              </p>
              <p className="row__meta">{rowProblemText(error.code, t)}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The totals of an import, each currency's by itself: "320 000 so'm", or that and "12.50 $" side by side. */
function totalText(counts: ImportCounts, language: Language, t: Translate): string {
  const uzs = formatMoney(counts.amount, language);
  if (counts.usd === undefined || counts.usd === 0) {
    return uzs;
  }
  const usd = formatMoney(counts.usd, language, "USD");
  return counts.amount === 0 ? usd : t("imports.total.both", { uzs, usd });
}

function Preview({ preview, total }: { preview: ImportPreview; total: number }) {
  const { t, language } = useI18n();
  const desktop = useDesktop();
  const [all, setAll] = useState(false);
  const rows = all ? preview.rows : preview.rows.slice(0, PREVIEW_ROWS);
  const money = (amount: number) => formatMoney(amount, language);
  // A row's amount in the row's own currency: a dollar row is cents and reads "12.50 $".
  const rowMoney = (row: PreviewRow) => formatMoney(row.amount, language, row.currency ?? "UZS");
  // What a matched customer owes now: in so'm and, in a shop that works in dollars, in dollars beside it.
  const owes = (customer: NonNullable<PreviewRow["customer"]>) =>
    customer.usd === undefined ? money(customer.balance) : `${money(customer.balance)} · ${formatMoney(customer.usd, language, "USD")}`;
  const usdTotal = preview.counts.usd;
  const day = (iso: string | null) => {
    const parsed = iso === null ? null : parseIsoDate(iso);
    return parsed ? formatCalendarDay(parsed, language) : (iso ?? NONE);
  };
  const action = (row: PreviewRow): string => {
    if (row.action === "create") {
      return t("imports.action.create");
    }
    if (row.action === "same_as_row" && row.sameAsRow !== null) {
      return t("imports.action.same_as_row", { row: row.sameAsRow });
    }
    if (row.action === "existing" && row.customer !== null) {
      const how = row.matchedBy === "phone" || row.matchedBy === "name" ? row.matchedBy : "other";
      return t(`imports.action.existing.${how}`, { name: row.customer.displayName, balance: owes(row.customer) });
    }
    return row.action;
  };
  const columns: Column<PreviewRow>[] = [
    { id: "row", header: t("imports.errors.col.row"), numeric: true, rowHeader: true, cell: (row) => row.row },
    { id: "name", header: t("imports.column.name"), cell: (row) => row.name },
    { id: "phone", header: t("imports.column.phone"), cell: (row) => row.phone ?? NONE },
    { id: "amount", header: t("imports.column.amount"), numeric: true, cell: rowMoney },
    { id: "promised", header: t("imports.column.promised_date"), cell: (row) => day(row.promisedDate) },
    { id: "note", header: t("imports.column.note"), cell: (row) => row.note ?? NONE },
    { id: "action", header: t("imports.preview.col.action"), cell: action },
  ];
  return (
    <section aria-labelledby="import-preview">
      <h3 id="import-preview">{t("imports.preview.title")}</h3>
      <dl className="facts">
        <dt>{t("imports.counts.newCustomers")}</dt>
        <dd>{preview.counts.newCustomers}</dd>
        <dt>{t("imports.counts.existingCustomers")}</dt>
        <dd>{preview.counts.existingCustomers}</dd>
        <dt>{t("imports.counts.entries")}</dt>
        <dd>{preview.counts.entries}</dd>
        <dt>{t("imports.counts.amount")}</dt>
        <dd>{money(preview.counts.amount)}</dd>
        {/* The dollar rows have a total of their own, never added to the so'm one. */}
        {usdTotal === undefined ? null : (
          <>
            <dt>{t("imports.counts.amount.usd")}</dt>
            <dd>{formatMoney(usdTotal, language, "USD")}</dd>
          </>
        )}
      </dl>
      {desktop ? (
        <desktop.Table caption={t("imports.preview.rows")} columns={columns} items={rows} rowKey={(row) => String(row.row)} />
      ) : (
        <ul className="rows" aria-label={t("imports.preview.rows")}>
          {rows.map((row) => (
            <li key={row.row} className="row">
              <p className="row__link">
                <span className="row__name">{`${row.row}. ${row.name}`}</span>
                <span className="row__amount">{rowMoney(row)}</span>
              </p>
              <p className="row__meta">{[row.phone, row.promisedDate === null ? null : day(row.promisedDate), row.note].filter((part) => part !== null).join(" · ")}</p>
              <p className="row__meta">{action(row)}</p>
            </li>
          ))}
        </ul>
      )}
      {rows.length < preview.rows.length ? (
        <>
          <p className="hint">{t("imports.preview.partial", { shown: rows.length, count: total })}</p>
          <p className="actions">
            <button type="button" className="button button--small" onClick={() => setAll(true)}>
              {t("imports.preview.all")}
            </button>
          </p>
        </>
      ) : null}
    </section>
  );
}

function refusedBy(code: string, words: (reason: string | undefined) => string) {
  return (error: ApiError): string | null => (error.code === code ? words(error.fields["reason"]) : null);
}

/** A checked import: what applying it would do, and the question before it is applied. */
function Validated({ client, batch, refresh }: { client: ImportsApi; batch: ImportBatch; refresh: () => void }) {
  const { t, language } = useI18n();
  const { state, reload } = useLoad((signal) => client.read(batch.id, signal), [client, batch.id]);
  // Why the last apply was refused. It is kept here, above the preview, which is read again meanwhile.
  const [refused, setRefused] = useState<string | null>(null);
  const said =
    refused === null ? null : (
      <p className="notice notice--error" role="alert">
        {refused}
      </p>
    );
  if (state.status === "loading") {
    return (
      <>
        {said}
        <Loading />
      </>
    );
  }
  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }
  const preview = state.data.preview;
  if (preview === null || preview.plan === null) {
    // The list is behind the server, or the preview is no longer kept: the list is what says where it stands.
    return <p className="notice notice--error">{t("imports.preview.gone")}</p>;
  }
  return (
    <>
      {said}
      <Preview preview={preview} total={state.data.rows} />
      <Step
        primary
        label={t("imports.apply")}
        question={t("imports.apply.confirm", {
          entries: preview.counts.entries,
          amount: totalText(preview.counts, language, t),
          customers: preview.counts.newCustomers,
        })}
        yes={t("imports.apply.yes")}
        asked={t("imports.apply.asked")}
        payload={{ plan: preview.plan }}
        send={(payload, key) =>
          client.apply(batch.id, payload.plan, key).then(
            () => {
              setRefused(null);
              refresh();
            },
            (error: unknown) => {
              const failure = toApiError(error);
              // The preview on the screen is not the server's any more: it and the list are read again.
              if (failure.code === "IMPORT_NOT_APPLICABLE") {
                setRefused(t(failure.fields["reason"] === "stale" ? "imports.notApplicable.stale" : "imports.notApplicable.other"));
                refresh();
                reload();
              }
              throw error;
            },
          )
        }
        refusedText={refusedBy("IMPORT_NOT_APPLICABLE", () => "")}
      />
    </>
  );
}

function Batch({ client, batch, refresh }: { client: ImportsApi; batch: ImportBatch; refresh: () => void }) {
  const { t, language } = useI18n();
  const state = has(IMPORT_STATES, batch.status) ? t(`imports.state.${batch.status satisfies ImportState}`) : batch.status;
  const refused = batch.refused;
  const refusedStep = refused === null ? undefined : STEPS[refused.step];
  const after = (sent: Promise<unknown>, code: string) =>
    sent.then(refresh, (error: unknown) => {
      // "Not in that state any more" means the list is behind: it is read again.
      if (toApiError(error).code === code) {
        refresh();
      }
      throw error;
    });
  const notApplicable = refusedBy("IMPORT_NOT_APPLICABLE", () => t("imports.notApplicable.other"));

  let body: ReactNode = null;
  if (isUnfinished(batch)) {
    body = <p className="row__meta">{t("imports.waiting.note")}</p>;
  } else if (batch.status === "validated") {
    body = <Validated client={client} batch={batch} refresh={refresh} />;
  } else if (batch.status === "rejected") {
    body =
      batch.fileProblem !== null ? (
        <p className="notice notice--error">{problemText(batch.fileProblem, t)}</p>
      ) : batch.errors.length > 0 ? (
        <RowErrors errors={batch.errors} />
      ) : null;
  } else if (batch.status === "applied") {
    body = (
      <>
        {batch.applied ? (
          <p className="notice notice--done">
            {t("imports.result.applied", {
              entries: batch.applied.entries,
              amount: totalText(batch.applied, language, t),
              created: batch.applied.newCustomers,
              existing: batch.applied.existingCustomers,
            })}
          </p>
        ) : null}
        {batch.undoUntil ? <p className="hint">{t("imports.undo.until", { date: formatInstant(batch.undoUntil, language) })}</p> : null}
        <Step
          label={t("imports.undo")}
          question={t("imports.undo.confirm")}
          yes={t("imports.undo.yes")}
          asked={t("imports.undo.asked")}
          payload={null}
          send={(_: null, key) => after(client.undo(batch.id, key), "IMPORT_UNDO_REFUSED")}
          refusedText={(error) => {
            const reason = error.fields["reason"];
            return error.code === "IMPORT_UNDO_REFUSED" && has(UNDO_REFUSALS, reason) ? undoRefusedText(reason, t) : null;
          }}
        />
      </>
    );
  } else if (batch.status === "undone" && batch.undone) {
    body = <p className="notice notice--done">{t("imports.result.undone", { reversed: batch.undone.reversed, archived: batch.undone.archived })}</p>;
  } else if (batch.status === "discarded") {
    body = <p className="row__meta">{t("imports.discarded")}</p>;
  }

  return (
    <section aria-labelledby="import-current">
      <h2 id="import-current">{t("imports.current")}</h2>
      <dl className="facts">
        <dt>{t("imports.col.uploaded")}</dt>
        <dd>{formatInstant(batch.createdAt, language)}</dd>
        <dt>{t("imports.col.state")}</dt>
        <dd>{state}</dd>
        <dt>{t("imports.col.rows")}</dt>
        <dd>{batch.rows}</dd>
      </dl>
      {refused === null ? null : (
        <div className="notice notice--error" role="note">
          {refusedStep ? <p>{t(refusedStep)}</p> : null}
          <p>{reasonText(refused.reason, t)}</p>
        </div>
      )}
      {body}
      {canDiscard(batch) ? (
        <Step
          label={t("imports.discard")}
          question={t("imports.discard.confirm")}
          yes={t("imports.discard.yes")}
          asked={t("imports.discarded")}
          payload={null}
          send={(_: null, key) => after(client.discard(batch.id, key), "IMPORT_NOT_APPLICABLE")}
          refusedText={notApplicable}
        />
      ) : null}
    </section>
  );
}

function Imports({ pollMs }: { pollMs: number }) {
  const { api, membershipId, shopMode = null } = useWorkspace();
  const { t, language } = useI18n();
  const desktop = useDesktop();
  const client = useMemo(() => importsOf(api), [api]);
  const { state, refresh, reload } = useImports(client, pollMs);
  const [openId, setOpenId] = useState<string | null>(null);
  const current = state.status === "ready" ? (state.items.find((batch) => batch.id === openId) ?? null) : null;

  const stateText = (batch: ImportBatch): string => (has(IMPORT_STATES, batch.status) ? t(`imports.state.${batch.status satisfies ImportState}`) : batch.status);
  const by = (batch: ImportBatch): string =>
    batch.authorId === membershipId ? t("imports.by.you") : t("imports.by.member", { code: batch.authorId.slice(-CODE_LENGTH) });
  const open = (batch: ImportBatch): ReactNode =>
    batch.id === openId ? (
      t("imports.opened")
    ) : (
      <button type="button" className="button button--small" onClick={() => setOpenId(batch.id)}>
        {t("imports.open")}
      </button>
    );

  let list: ReactNode;
  if (state.status === "loading") {
    list = <Loading />;
  } else if (state.status === "error") {
    list = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    list = <Empty>{t("imports.none")}</Empty>;
  } else if (desktop) {
    const columns: Column<ImportBatch>[] = [
      { id: "uploaded", header: t("imports.col.uploaded"), rowHeader: true, cell: (batch) => formatInstant(batch.createdAt, language) },
      { id: "by", header: t("imports.col.by"), cell: by },
      { id: "state", header: t("imports.col.state"), cell: stateText },
      { id: "rows", header: t("imports.col.rows"), numeric: true, cell: (batch) => batch.rows },
      { id: "open", header: t("imports.col.open"), cell: open },
    ];
    list = <desktop.Table caption={t("imports.list")} columns={columns} items={state.items} rowKey={(batch) => batch.id} />;
  } else {
    list = (
      <ul className="rows" aria-label={t("imports.list")}>
        {state.items.map((batch) => (
          <li key={batch.id} className="row">
            <p className="row__link">
              <span className="row__name">{formatInstant(batch.createdAt, language)}</span>
              <span className="row__amount">{stateText(batch)}</span>
            </p>
            <p className="row__meta">{`${by(batch)} · ${t("imports.rows", { count: batch.rows })}`}</p>
            <p className="actions">{open(batch)}</p>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <>
      <section aria-labelledby="import-title">
        <h2 id="import-title">{t("imports.title")}</h2>
        <p>{t("imports.explain")}</p>
        <p>{t("imports.columns")}</p>
        {/* Only a shop that works in dollars has the currency column, and only it is told of it. */}
        {state.status === "ready" && state.currencyColumn ? <p>{t("imports.columns.currency")}</p> : null}
        <p>{t("imports.steps")}</p>
        <p className="hint">{t("imports.limits", { size: MAX_MB, rows: IMPORT_MAX_ROWS })}</p>
        {shopMode === null ? <p className="hint">{t("imports.limited")}</p> : <p className="notice notice--error">{t(`imports.mode.${shopMode}`)}</p>}
        <Template client={client} />
        <UploadForm
          client={client}
          onUploaded={(batch) => {
            setOpenId(batch.id);
            refresh();
          }}
        />
      </section>
      {current === null ? null : <Batch key={`${current.id}/${current.status}`} client={client} batch={current} refresh={refresh} />}
      <section aria-labelledby="import-list">
        <h2 id="import-list">{t("imports.list")}</h2>
        {state.status === "ready" && state.stale ? (
          <div className="notice notice--error" role="alert">
            <p>{t("imports.stale")}</p>
            <p>{errorText(state.stale, t)}</p>
            <button type="button" className="button" onClick={refresh}>
              {t("imports.refresh")}
            </button>
          </div>
        ) : null}
        {list}
      </section>
    </>
  );
}

/**
 * Import of customers with opening balances (REQ-062, REQ-063): the template, the upload, what the
 * check found, what applying would do, the apply and the undo, each after a question, and the recent
 * imports. For managers and owners; a seller gets the not-found screen and nothing is asked.
 */
export default function ImportScreen({ pollMs = POLL_MS }: { pollMs?: number }) {
  const can = useMay();
  return can("imports.run") ? <Imports pollMs={pollMs} /> : <NotFoundScreen />;
}
