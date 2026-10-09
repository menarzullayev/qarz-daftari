import { reading, type ShopApi } from "../api";

/**
 * The owner's receipts for the subscription paid by card transfer (REQ-054, REQ-055). Built on the shop
 * API's `send` in the module that the subscription screen loads on demand. Shapes follow
 * backend/src/qarz/application/subscription_receipts.py (`receipt_body`) and
 * interface/subscription_receipts_api.py; the bounds are those of domain/subscription_receipts.py.
 */

const { record, text, textOrNull, wholeOrNull } = reading;

export const MAX_MONTHS = 36;
export const MIN_AMOUNT = 1_000;
export const MAX_AMOUNT = 10_000_000 * MAX_MONTHS;
/** How many of a shop's receipts may wait for the administrator at once. */
export const MAX_WAITING = 3;

export type OwnReceipt = {
  id: string;
  statedAmount: number | null;
  statedMonths: number | null;
  /** The server's word: "submitted", "approved" or "rejected". */
  status: string;
  /** The months the administrator counted; null until the receipt is approved. */
  months: number | null;
  rejectReason: string | null;
  createdAt: string;
  decidedAt: string | null;
  /** The card the receipt says was paid to, as its label and last four digits; null when not said. */
  paidToCard: string | null;
};

function ownReceipt(value: unknown): OwnReceipt {
  const body = record(value);
  return {
    id: text(body["id"]),
    statedAmount: wholeOrNull(body["stated_amount"]),
    statedMonths: wholeOrNull(body["stated_months"]),
    status: text(body["status"]),
    months: wholeOrNull(body["months"]),
    rejectReason: textOrNull(body["reject_reason"]),
    createdAt: text(body["created_at"]),
    decidedAt: textOrNull(body["decided_at"]),
    paidToCard: textOrNull(body["paid_to_card"]),
  };
}

/** The months as typed: a whole number from 1 to 36 in plain digits, or null. */
export function parseMonths(input: string): number | null {
  const typed = input.trim();
  if (!/^[0-9]{1,2}$/.test(typed)) {
    return null;
  }
  const months = Number(typed);
  return months >= 1 && months <= MAX_MONTHS ? months : null;
}

export function receiptsOf(api: ShopApi) {
  const path = `${api.base}/subscription/receipts`;
  return {
    /**
     * Sends a receipt as a multipart form with the fields `amount`, `months` and `receipt`, and `card`,
     * the number of the card that was paid to, when the owner chose one. The server refuses a number that
     * is not one of the cards it offers now.
     */
    submit(
      input: { amount: number; months: number; receipt: Blob; card?: string | null },
      idempotencyKey: string,
    ): Promise<OwnReceipt> {
      if (!Number.isSafeInteger(input.amount) || !Number.isSafeInteger(input.months)) {
        throw new RangeError("amount and months must be whole numbers");
      }
      const form = new FormData();
      form.set("amount", String(input.amount));
      form.set("months", String(input.months));
      form.set("receipt", input.receipt);
      if (input.card !== undefined && input.card !== null) {
        form.set("card", input.card);
      }
      return api.send({ method: "POST", path, form, idempotencyKey, read: ownReceipt });
    },

    /** The shop's receipts and what became of each, newest first. */
    list(signal?: AbortSignal): Promise<OwnReceipt[]> {
      return api.send({ method: "GET", path, signal, read: reading.items(ownReceipt) });
    },
  };
}

export type ReceiptsApi = ReturnType<typeof receiptsOf>;
