import type { EnPlural, PartialCatalog } from "../types";
import type { uzShare } from "./uz";

/** English text of a customer's read-only link, as staff see it. An absent key reads Uzbek at run time. */
export const enShare: PartialCatalog<typeof uzShare, EnPlural> = {
  "share.title": "Link for the customer (without Telegram)",
  "share.explain":
    "With the link or QR code the customer sees their own debt: without signing in, read-only. Whoever has the link can see it — give it only to the customer.",
  "share.none": "There is no link for this customer.",
  "share.active": "The link is valid until {date}.",
  "share.expired": "The link expired on {date}. It no longer opens.",
  "share.opened": "Last opened: {date}.",
  "share.neverOpened": "Not opened yet.",
  "share.create": "Create link",
  "share.replace": "Create new link",
  "share.replace.question":
    "If a new link is created, the earlier one stops working at once. So does a printed QR code. Continue?",
  "share.replace.yes": "Yes, create a new one",
  "share.revoke": "Revoke link",
  "share.revoke.question":
    "The link stops working at once. The customer will not be able to open it. Revoke it?",
  "share.revoke.yes": "Yes, revoke",
  "share.revoked": "The link was revoked.",
  "share.once":
    "The link is shown only now: it is not kept. Copy it or print it. If it is lost, create a new one.",
  "share.lost": "The link was created, but its text did not reach this screen. Create a new one.",
  "share.caption": "Scan this code to see your debt",
  "share.hide": "Hide link",
  "share.archived": "A link cannot be created for a customer in the archive.",
  "share.contact.title": "Phone on the customer link",
  "share.contact.explain":
    "When the customer opens the link, this phone is shown next to the shop name. If you leave it empty, no phone is shown.",
  "share.contact.label": "Shop phone",
  "share.contact.none": "No phone given.",
  "share.contact.saved": "Saved.",
  "share.contact.invalid": "The phone number is not valid. Example: +998 90 123 45 67",
};
