import { reading, type ShopApi } from "../api";

/**
 * Exports of a shop's book (REQ-028), for managers and owners. Built on the shop API's `send` in the
 * module that is loaded with the export screen, so these calls are not part of the first load. Shapes
 * follow backend/src/qarz/application/exports.py (`job_body`) and interface/exports_api.py.
 */

const { record, text, textOrNull, wholeOrNull, flag } = reading;

/** How many exports a shop may ask for in one Tashkent day, and how long a file is kept (domain/exports.py). */
export const DAILY_LIMIT = 5;
export const RETENTION_DAYS = 7;

export type ExportJob = {
  id: string;
  /** The server's word: "queued", "running", "done" or "failed". */
  status: string;
  /** Why it failed: "interrupted", "timeout", "file_store" or "internal"; null unless it failed. */
  error: string | null;
  /** The membership that asked. */
  requestedBy: string;
  createdAt: string;
  finishedAt: string | null;
  /** How many rows the workbook holds; null until it is made. */
  rows: number | null;
  /** Whether the file can be downloaded now. A finished export whose file is gone has expired. */
  available: boolean;
  availableUntil: string | null;
};

/** Where the file can be downloaded for five minutes. The address is a credential: it is never stored. */
export type ExportLink = { url: string; expiresAt: string };

export const EXPORT_STATES = ["queued", "running", "done", "failed", "expired"] as const;
export type ExportState = (typeof EXPORT_STATES)[number];

/** What the screen says about a job: the server's status, and "expired" for a finished one without its file. */
export function exportState(job: ExportJob): ExportState | null {
  if (job.status === "done") {
    return job.available ? "done" : "expired";
  }
  return job.status === "queued" || job.status === "running" || job.status === "failed" ? job.status : null;
}

/** Whether the job is still on its way: the only time the list is worth reading again. */
export function isUnfinished(job: ExportJob): boolean {
  return job.status === "queued" || job.status === "running";
}

function exportJob(value: unknown): ExportJob {
  const body = record(value);
  return {
    id: text(body["id"]),
    status: text(body["status"]),
    error: textOrNull(body["error"]),
    requestedBy: text(body["requested_by"]),
    createdAt: text(body["created_at"]),
    finishedAt: textOrNull(body["finished_at"]),
    rows: wholeOrNull(body["rows"]),
    available: flag(body["available"]),
    availableUntil: textOrNull(body["available_until"]),
  };
}

function exportLink(value: unknown): ExportLink {
  const body = record(value);
  return { url: text(body["url"]), expiresAt: text(body["expires_at"]) };
}

export function exportsOf(api: ShopApi) {
  const path = `${api.base}/exports`;
  return {
    /** Asks for an export. It is made in the background; the answer is the job as it waits. */
    request(idempotencyKey: string): Promise<ExportJob> {
      return api.send({ method: "POST", path, idempotencyKey, read: exportJob });
    },

    /** The shop's newest exports, newest first. */
    list(signal?: AbortSignal): Promise<ExportJob[]> {
      return api.send({ method: "GET", path, signal, read: reading.items(exportJob) });
    },

    /** Asks for a link to a finished export's file. */
    link(jobId: string): Promise<ExportLink> {
      return api.send({ method: "GET", path: `${path}/${encodeURIComponent(jobId)}/download`, read: exportLink });
    },
  };
}

export type ExportsApi = ReturnType<typeof exportsOf>;
