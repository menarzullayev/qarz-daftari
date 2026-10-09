import type { EnPlural, PartialCatalog } from "../types";
import type { uzExports } from "./uz";

/** English text of the export screen. A key that is absent here reads Uzbek at run time. */
export const enExports: PartialCatalog<typeof uzExports, EnPlural> = {
  "exports.title": "Export",
  "exports.explain":
    "The shop's whole ledger is written to one Excel file (.xlsx). The file has five sheets:",
  "exports.sheet.summary":
    "Summary — shop name, number of customers and debtors, total debt, totals by month.",
  "exports.sheet.customers": "Customers — each customer's name, phone, status and debt.",
  "exports.sheet.ledger": "Ledger — all credit sale, payment and reversal entries.",
  "exports.sheet.promises": "Due dates — the due date history of each entry.",
  "exports.sheet.goods": "Items — the item lines of the entries.",
  "exports.personal":
    "Note: the file holds customers' names and phone numbers. Keep it only in a safe place and do not send it to strangers.",
  "exports.limits":
    "The file is kept for {days} days, then deleted. You can request an export at most {limit} times a day.",
  "exports.request": "Request export",
  "exports.requested": "Request accepted. When the file is ready, it will appear in this list.",
  "exports.refused.in_progress":
    "The earlier export is still being prepared. Request a new one when it is finished.",
  "exports.refused.daily_limit": "No exports left for today ({limit} a day). Request again tomorrow.",
  "exports.suspended": "The shop is suspended. In this state only the shop owner can get an export.",
  "exports.list": "Latest exports",
  "exports.none": "No export requested yet.",
  "exports.col.asked": "Requested",
  "exports.col.by": "Requested by",
  "exports.col.state": "Status",
  "exports.col.file": "File",
  "exports.by.you": "You",
  "exports.by.member": "Staff member · {code}",
  "exports.state.queued": "In the queue",
  "exports.state.running": "Being prepared",
  "exports.state.done": "Ready",
  "exports.state.failed": "Not prepared",
  "exports.state.expired": "File expired",
  "exports.rows": { one: "{count} row", other: "{count} rows" },
  "exports.keptUntil": "The file is kept until {date}, then deleted.",
  "exports.expired.note": "The file was deleted. Request a new export if you need it.",
  "exports.waiting.note": "The status refreshes by itself.",
  "exports.failed.interrupted": "The work was cut short. Request again.",
  "exports.failed.timeout": "The work took too long and was stopped. Request again.",
  "exports.failed.file_store": "Could not save the file. Request again a little later.",
  "exports.failed.internal": "Something went wrong while preparing. Request again.",
  "exports.failed.other": "The file was not prepared. Request again.",
  "exports.link.get": "Get download link",
  "exports.link.open": "Download file",
  "exports.link.valid": "The link is valid for 5 minutes, until {date}. If it does not open, get a new one.",
  "exports.link.again": "Get new link",
  "exports.notReady.queued": "The file is still in the queue. Wait until it is ready.",
  "exports.notReady.running": "The file is still being prepared. Wait until it is ready.",
  "exports.notReady.failed": "This export was not prepared. Request a new one.",
  "exports.notReady.expired": "The file has expired and was deleted. Request a new export.",
  "exports.stale": "Could not refresh the status.",
  "exports.refresh": "Refresh",
};
