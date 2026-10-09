import type { EnPlural, PartialCatalog } from "../types";
import type { uzReports } from "./uz";

/** English text of the reports screen. A key that is absent here reads Uzbek at run time. */
export const enReports: PartialCatalog<typeof uzReports, EnPlural> = {
  "reports.period": "Period",
  "reports.preset.today": "Today",
  "reports.preset.week": "This week",
  "reports.preset.month": "This month",
  "reports.preset.lastMonth": "Last month",
  "reports.from": "Start date",
  "reports.to": "End date",
  "reports.show": "Show",
  "reports.period.hint": "A period is at most {days} days long and cannot go past today.",
  "reports.problem.DATE_INVALID": "Pick a date.",
  "reports.problem.FROM_AFTER_TO": "The start date must not be after the end date.",
  "reports.problem.IN_FUTURE": "A period cannot end after today.",
  "reports.problem.PERIOD_TOO_LONG": "A period must not be longer than {days} days.",
  "reports.period.title": "{from} — {to}",
  "reports.period.oneDay": "{date}",

  "reports.outstanding.start": "Debt at the start of the period",
  "reports.outstanding.end": "Debt at the end of the period",
  "reports.netChange": "Change over the period",
  "reports.credit": "Credit sales",
  "reports.payments": "Paid back (payments)",
  "reports.opening": "Opening debt entered",
  "reports.reversals": "Reversed entries",
  "reports.entries": { one: "{count} entry", other: "{count} entries" },
  "reports.activity": "{entries}, {customers}",
  "reports.newCustomers": "New customers",
  "reports.disputes": "Disputes opened",

  "reports.equation": "Check",
  "reports.equation.hint":
    "Debt at the start + credit sales + opening debt − payments = debt at the end of the period",
  "reports.equation.line": "{start} + {credit} + {opening} − {payments} = {end}",
  "reports.equation.mismatch":
    "The figures do not match: the left side is {computed}, but the debt at the end of the period is {end}. Tell support.",

  "reports.onTime": "Paid back by the promised date",
  "reports.onTime.value": "{percent}%",
  "reports.onTime.amounts": "Of {due} that came due in this period, {onTime} was paid on time.",
  "reports.onTime.none": "No debt came due in this period.",

  "reports.days": "By day",
  "reports.days.day": "Day",
  "reports.days.credit": "Credit sales",
  "reports.days.payments": "Payments",
  "reports.days.row": "Credit sales: {credit} · Payments: {payments}",
  "reports.days.none": "No credit sales or payments were recorded in this period.",
  "reports.days.quiet": {
    one: "{count} day with no credit sales or payments is not shown.",
    other: "{count} days with no credit sales or payments are not shown.",
  },

  "reports.debtors": "Largest debtors (at the end of the period)",
  "reports.debtors.none": "No debtors at the end of the period.",
  "reports.debtors.customer": "Customer",
  "reports.debtors.balance": "Debt",

  "reports.staff": "By staff member",
  "reports.staff.none": "Staff recorded no credit sales or payments in this period.",
  "reports.staff.member": "{role} · {code}",
  "reports.staff.you": "{member} (you)",
  "reports.staff.who": "Staff member",
  "reports.staff.credit": "Credit sales",
  "reports.staff.creditCount": "Credit sale entries",
  "reports.staff.payments": "Payments",
  "reports.staff.paymentCount": "Payment entries",
  "reports.staff.row": "Credit sales: {credit} ({creditCount}) · Payments: {payments} ({paymentCount})",

  "reports.overdue": "Overdue debt, by delay",
  "reports.overdue.asOf": "As of {date}.",
  "reports.overdue.none": "No overdue debt.",
  "reports.overdue.band": "Delay",
  "reports.overdue.amount": "Amount",
  "reports.overdue.customers": "Customers",
  "reports.overdue.row": "{amount} · {customers}",
  "reports.overdue.total": "Total",
  "reports.overdue.totalHint":
    "One customer's debt can be in more than one row; in the total they are counted once.",
  "reports.band.range": "{from}–{to} days",
  "reports.band.over": "{from} days and more",
};
