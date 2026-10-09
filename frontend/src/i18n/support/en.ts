import type { EnPlural, PartialCatalog } from "../types";
import type { uzSupport } from "./uz";

/** English text of the owner's view of support access. An absent key reads Uzbek at run time. */
export const enSupport: PartialCatalog<typeof uzSupport, EnPlural> = {
  "support.title": "Support access",
  "support.explain":
    "To solve a problem, a platform administrator can see the shop's customers and entries for a time, read-only. They give a reason, the access lasts at most 24 hours, and every view of theirs is written to the activity log. You can end the access at any time.",
  "support.none.open": "No administrator can see the shop's data right now.",
  "support.open": "An administrator ({code}) can see the shop's data right now. The access ends on {date}.",
  "support.open.reason": "Reason given: {reason}",
  "support.end": "End access",
  "support.end.confirm":
    "End the access of the administrator ({code}) now? They will no longer be able to see the shop's data.",
  "support.end.yes": "Yes, end it",
  "support.end.no": "No",
  "support.ended": "Access ended.",
  "support.history": "Access history",
  "support.history.none": "Administrators have not entered this shop yet.",
  "support.col.admin": "Administrator",
  "support.col.reason": "Reason",
  "support.col.from": "Started",
  "support.col.to": "End time",
  "support.col.outcome": "How it ended",
  "support.outcome.active": "Open now",
  "support.outcome.expired": "Expired",
  "support.outcome.owner": "Ended by the owner · {date}",
  "support.outcome.admin": "Closed by the administrator · {date}",
  "support.outcome.closed": "Closed · {date}",
  "support.banner": "An administrator can see the shop's data right now. The access ends on {date}.",
  "support.banner.reason": "Reason: {reason}",
  "support.banner.open": "View and end",
};
