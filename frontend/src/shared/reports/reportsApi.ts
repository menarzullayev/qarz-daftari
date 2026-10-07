import { reading, type ShopApi } from "../api";

/**
 * The reports of a shop (REQ-046), for managers and owners. Built on the shop API's `send` in the module
 * that is loaded with the reports screen, so these calls are not part of the first load. Shapes follow
 * backend/src/qarz/application/reports.py. Every amount is a whole number of UZS; a response where one
 * is not is refused, never shown.
 */

const { record, text, whole, wholeOrNull, list } = reading;

export type Activity = { amount: number; count: number };

export type PeriodReport = {
  from: string;
  to: string;
  outstanding: { start: number; end: number };
  credit: Activity & { customers: number };
  payments: Activity & { customers: number };
  opening: Activity;
  reversals: Activity;
  netChange: number;
  newCustomers: number;
  disputesOpened: number;
  /** `percent` is null when nothing fell due in the period. */
  onTime: { dueAmount: number; onTimeAmount: number; percent: number | null };
  /** Every day of the period, in order. */
  days: { date: string; credit: number; payments: number }[];
  /** The largest balances at the end of the period, largest first. */
  topDebtors: { customerId: string; displayName: string; balance: number }[];
  staff: { membershipId: string; role: string; credit: Activity; payments: Activity }[];
};

export type OverdueBand = {
  band: string;
  /** Days past the promised date: the first and the last of the band; the last band has no end. */
  fromDays: number;
  toDays: number | null;
  amount: number;
  customers: number;
};

export type OverdueReport = { asOf: string; total: { amount: number; customers: number }; bands: OverdueBand[] };

function activity(value: unknown): Activity {
  const body = record(value);
  return { amount: whole(body["amount"]), count: whole(body["count"]) };
}

function withCustomers(value: unknown): Activity & { customers: number } {
  return { ...activity(value), customers: whole(record(value)["customers"]) };
}

function periodReport(value: unknown): PeriodReport {
  const body = record(value);
  const outstanding = record(body["outstanding"]);
  const onTime = record(body["on_time"]);
  return {
    from: text(body["from"]),
    to: text(body["to"]),
    outstanding: { start: whole(outstanding["start"]), end: whole(outstanding["end"]) },
    credit: withCustomers(body["credit"]),
    payments: withCustomers(body["payments"]),
    opening: activity(body["opening"]),
    reversals: activity(body["reversals"]),
    netChange: whole(body["net_change"]),
    newCustomers: whole(body["new_customers"]),
    disputesOpened: whole(body["disputes_opened"]),
    onTime: {
      dueAmount: whole(onTime["due_amount"]),
      onTimeAmount: whole(onTime["on_time_amount"]),
      percent: wholeOrNull(onTime["percent"]),
    },
    days: list(body["days"], (element) => {
      const day = record(element);
      return { date: text(day["date"]), credit: whole(day["credit"]), payments: whole(day["payments"]) };
    }),
    topDebtors: list(body["top_debtors"], (element) => {
      const debtor = record(element);
      return {
        customerId: text(debtor["customer_id"]),
        displayName: text(debtor["display_name"]),
        balance: whole(debtor["balance"]),
      };
    }),
    staff: list(body["staff"], (element) => {
      const member = record(element);
      return {
        membershipId: text(member["membership_id"]),
        role: text(member["role"]),
        credit: activity(member["credit"]),
        payments: activity(member["payments"]),
      };
    }),
  };
}

function overdueReport(value: unknown): OverdueReport {
  const body = record(value);
  const total = record(body["total"]);
  return {
    asOf: text(body["as_of"]),
    total: { amount: whole(total["amount"]), customers: whole(total["customers"]) },
    bands: list(body["bands"], (element) => {
      const band = record(element);
      return {
        band: text(band["band"]),
        fromDays: whole(band["from_days"]),
        toDays: wholeOrNull(band["to_days"]),
        amount: whole(band["amount"]),
        customers: whole(band["customers"]),
      };
    }),
  };
}

export function reports(api: ShopApi) {
  const base = `${api.base}/reports`;
  return {
    /** One period of Tashkent days, both ends included, as "YYYY-MM-DD". */
    period(from: string, to: string, signal?: AbortSignal): Promise<PeriodReport> {
      return api.send({ method: "GET", path: `${base}/period`, query: { from, to }, signal, read: periodReport });
    },

    /** Overdue debt by how long past its promised date it is, as of today. */
    overdue(signal?: AbortSignal): Promise<OverdueReport> {
      return api.send({ method: "GET", path: `${base}/overdue`, signal, read: overdueReport });
    },
  };
}
