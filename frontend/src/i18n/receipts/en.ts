import type { EnPlural, PartialCatalog } from "../types";
import type { uzReceipts } from "./uz";

/** English text of the owner's subscription receipts. A key that is absent here reads Uzbek at run time. */
export const enReceipts: PartialCatalog<typeof uzReceipts, EnPlural> = {
  "receipts.title": "Send a receipt",
  "receipts.explain":
    "After you transfer the payment to the card above, send the receipt here. Once an administrator has reviewed it, the subscription is extended.",
  "receipts.months": "How many months did you pay for",
  "receipts.months.hint": "From 1 to {max}.",
  "receipts.months.invalid": "The number of months must be a whole number from 1 to {max}.",
  "receipts.amount": "Amount transferred",
  "receipts.amount.hint":
    "Worked out from the price: {amount} (months: {months}). If you transferred another amount, change it.",
  "receipts.amount.invalid": "The amount must be from {min} to {max}, in whole soum.",
  "receipts.file": "Receipt",
  "receipts.file.required": "Choose the receipt file.",
  "receipts.send": "Send receipt",
  "receipts.sent":
    "Receipt sent. Once an administrator has reviewed it, the result will appear in this list.",
  "receipts.tooManyWaiting":
    "You have {max} receipts not yet reviewed: you cannot send more. Wait for the administrator's answer.",
  "receipts.storeDown": "Could not save the receipt right now. Try again a little later.",
  "receipts.history": "Receipts sent",
  "receipts.none": "No receipts sent yet.",
  "receipts.col.sent": "Sent",
  "receipts.col.amount": "Amount",
  "receipts.col.months": "Months",
  "receipts.col.state": "Status",
  "receipts.col.card": "Card",
  "receipts.card": "Card: {card}",
  "receipts.card.changed": "The list of cards has changed. Refresh the page and choose the card again.",
  "receipts.state.submitted": "Waiting for review",
  "receipts.state.approved": "Approved. Months counted: {months}",
  "receipts.state.approved.plain": "Approved",
  "receipts.state.rejected": "Rejected. Reason: {reason}",
  "receipts.state.rejected.plain": "Rejected",
  "receipts.stated": { one: "for {count} month", other: "for {count} months" },
};
