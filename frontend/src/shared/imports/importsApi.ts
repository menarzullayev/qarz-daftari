import { reading, type ShopApi } from "../api";

/**
 * Import of customers with opening balances (REQ-062, REQ-063), for managers and owners. Built on the
 * shop API's `send` in the module that is loaded with the import screen, so these calls are not part of
 * the first load. Shapes follow backend/src/qarz/application/imports.py (`batch_body`, `_write_preview`)
 * and interface/imports_api.py.
 */

const { record, text, textOrNull, whole, wholeOrNull, list } = reading;

/** What a file may be (backend/src/qarz/domain/files.py, domain/imports.py). */
export const IMPORT_MAX_BYTES = 5 * 1024 * 1024;
export const IMPORT_MAX_ROWS = 2000;
export const UNDO_HOURS = 24;
export const TEMPLATE_NAME = "qarz-daftari-import.xlsx";

/** The server's words for a batch's state. */
export const IMPORT_STATES = ["uploaded", "validated", "rejected", "failed", "applying", "applied", "undoing", "undone", "discarded"] as const;
export type ImportState = (typeof IMPORT_STATES)[number];

/** Why a file as a whole cannot be an import (`FileProblem`, and "file_gone" for a file no longer kept). */
export const FILE_PROBLEMS = [
  "empty",
  "too_large",
  "not_a_spreadsheet",
  "encoding",
  "malformed",
  "expands_too_much",
  "no_header",
  "missing_column",
  "unknown_column",
  "duplicate_column",
  "no_rows",
  "too_many_rows",
  "file_gone",
] as const;
export type FileProblem = (typeof FILE_PROBLEMS)[number];

/** What is wrong with one cell (`RowProblem`). The cell's content is never part of the answer. */
export const ROW_PROBLEMS = [
  "name_missing",
  "name_too_long",
  "phone_invalid",
  "amount_missing",
  "amount_invalid",
  "amount_not_whole",
  "amount_too_small",
  "amount_too_large",
  "date_invalid",
  "date_too_old",
  "date_too_far",
  "note_too_long",
  "ambiguous_customer",
  "customer_archived",
] as const;
export type RowProblem = (typeof ROW_PROBLEMS)[number];

export const IMPORT_COLUMNS = ["name", "phone", "amount", "promised_date", "note"] as const;

/** Why a step the worker was asked for was not done, besides a file problem. */
export const STEP_REFUSALS = ["interrupted", "timeout", "file_store", "internal", "stale", "errors", "free_plan_full", "balance_used"] as const;
export type StepRefusal = (typeof STEP_REFUSALS)[number];

/** Why an undo is refused at once (`UndoRefusal`) or by the worker ("balance_used"). */
export const UNDO_REFUSALS = ["not_applied", "too_late", "balance_used"] as const;
export type UndoRefusal = (typeof UNDO_REFUSALS)[number];

export type RowError = { row: number; column: string; code: string };
export type ImportCounts = { newCustomers: number; existingCustomers: number; entries: number; amount: number };

export type ImportBatch = {
  id: string;
  /** The server's word; see `IMPORT_STATES`. */
  status: string;
  /** "xlsx" or "csv". */
  format: string | null;
  /** The membership that uploaded the file. */
  authorId: string;
  createdAt: string;
  appliedAt: string | null;
  /** Until when an undo can still be asked for; null when there is nothing to undo. */
  undoUntil: string | null;
  rows: number;
  /** Why the file as a whole could not be used; null when it could. */
  fileProblem: string | null;
  errors: RowError[];
  applied: ImportCounts | null;
  undone: { reversed: number; archived: number } | null;
  /** The last step that was asked for and not done: "check", "apply" or "undo", with the reason. */
  refused: { step: string; reason: string } | null;
};

export type PreviewRow = {
  row: number;
  name: string;
  phone: string | null;
  amount: number;
  promisedDate: string | null;
  note: string | null;
  /** "create", "existing" or "same_as_row". */
  action: string;
  /** "phone" or "name"; null for a new customer. */
  matchedBy: string | null;
  /** For "same_as_row": the earlier row whose new customer this row is added to. */
  sameAsRow: number | null;
  /** The customer of the shop the row is added to, for "existing". */
  customer: { id: string; displayName: string; phone: string | null; balance: number } | null;
};

export type ImportPreview = {
  /** Named when applying; null while a row has a problem. */
  plan: string | null;
  errors: RowError[];
  counts: ImportCounts;
  rows: PreviewRow[];
};

export type ImportDetail = ImportBatch & { preview: ImportPreview | null };

/** Whether the worker still has a step to do: the only time the list is worth reading again. */
export function isUnfinished(batch: { status: string }): boolean {
  return batch.status === "uploaded" || batch.status === "applying" || batch.status === "undoing";
}

/** What the person may still ask of a batch in this state (application/imports.py). */
export function canDiscard(batch: { status: string }): boolean {
  return ["uploaded", "validated", "rejected", "failed"].includes(batch.status);
}

function rowError(value: unknown): RowError {
  const body = record(value);
  return { row: whole(body["row"]), column: text(body["column"]), code: text(body["code"]) };
}

function counts(value: unknown): ImportCounts {
  const body = record(value);
  return {
    newCustomers: whole(body["new_customers"]),
    existingCustomers: whole(body["existing_customers"]),
    entries: whole(body["entries"]),
    amount: whole(body["amount"]),
  };
}

const orNull = <T>(value: unknown, read: (value: unknown) => T): T | null => (value === null || value === undefined ? null : read(value));

function batch(value: unknown): ImportBatch {
  const body = record(value);
  return {
    id: text(body["id"]),
    status: text(body["status"]),
    format: textOrNull(body["format"] ?? null),
    authorId: text(body["author_id"]),
    createdAt: text(body["created_at"]),
    appliedAt: textOrNull(body["applied_at"]),
    undoUntil: textOrNull(body["undo_until"]),
    rows: whole(body["rows"]),
    fileProblem: textOrNull(body["file_problem"] ?? null),
    errors: list(body["errors"], rowError),
    applied: orNull(body["applied"], counts),
    undone: orNull(body["undone"], (element) => {
      const undone = record(element);
      return { reversed: whole(undone["reversed"]), archived: whole(undone["archived"]) };
    }),
    refused: orNull(body["refused"], (element) => {
      const refused = record(element);
      return { step: text(refused["step"]), reason: text(refused["reason"]) };
    }),
  };
}

function previewRow(value: unknown): PreviewRow {
  const body = record(value);
  return {
    row: whole(body["row"]),
    name: text(body["name"]),
    phone: textOrNull(body["phone"]),
    amount: whole(body["amount"]),
    promisedDate: textOrNull(body["promised_date"]),
    note: textOrNull(body["note"]),
    action: text(body["action"]),
    matchedBy: textOrNull(body["matched_by"]),
    sameAsRow: wholeOrNull(body["same_as_row"]),
    customer: orNull(body["customer"], (element) => {
      const customer = record(element);
      return {
        id: text(customer["id"]),
        displayName: text(customer["display_name"]),
        phone: textOrNull(customer["phone"]),
        balance: whole(customer["balance"]),
      };
    }),
  };
}

function detail(value: unknown): ImportDetail {
  const body = record(value);
  return {
    ...batch(body),
    preview: orNull(body["preview"], (element) => {
      const preview = record(element);
      return {
        plan: textOrNull(preview["plan"]),
        errors: list(preview["errors"], rowError),
        counts: counts(preview["counts"]),
        rows: list(preview["rows"], previewRow),
      };
    }),
  };
}

export function importsOf(api: ShopApi) {
  const path = `${api.base}/imports`;
  const one = (id: string) => `${path}/${encodeURIComponent(id)}`;
  return {
    /** The empty workbook with the headers the import reads, in the shop's language. */
    template(): Promise<Blob> {
      return api.send({
        method: "GET",
        path: `${path}/template`,
        binary: true,
        // `binary` hands over the answer's body as it came: a file, whatever it holds.
        read: (value) => value as Blob,
      });
    },

    /** Sends the file, which is the request body itself. It is checked in the background. */
    upload(file: Blob, idempotencyKey: string): Promise<ImportBatch> {
      return api.send({ method: "POST", path, raw: file, idempotencyKey, read: batch });
    },

    /** The shop's newest imports, newest first. */
    list(signal?: AbortSignal): Promise<ImportBatch[]> {
      return api.send({ method: "GET", path, signal, read: reading.items(batch) });
    },

    /** One import with what applying it would do, once it has been checked. */
    read(id: string, signal?: AbortSignal): Promise<ImportDetail> {
      return api.send({ method: "GET", path: one(id), signal, read: detail });
    },

    /** Asks for the import to be applied. `plan` names the preview that was shown; another is refused. */
    apply(id: string, plan: string, idempotencyKey: string): Promise<ImportBatch> {
      return api.send({ method: "POST", path: `${one(id)}/apply`, body: { plan }, idempotencyKey, read: batch });
    },

    /** Asks for an applied import to be taken back: its entries are reversed. */
    undo(id: string, idempotencyKey: string): Promise<ImportBatch> {
      return api.send({ method: "POST", path: `${one(id)}/undo`, idempotencyKey, read: batch });
    },

    /** Gives up an import that was not applied. */
    discard(id: string, idempotencyKey: string): Promise<ImportBatch> {
      return api.send({ method: "POST", path: `${one(id)}/discard`, idempotencyKey, read: batch });
    },
  };
}

export type ImportsApi = ReturnType<typeof importsOf>;
