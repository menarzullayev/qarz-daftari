import { useMemo, useState, type FormEvent, type ReactNode } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { ApiError } from "../api";
import { formatCustomerCount, formatMoney, tashkentDay } from "../format";
import { useLoad } from "../hooks";
import { type Column, useDesktop } from "../layout";
import { canManage, isRole } from "../navigation";
import { toIsoDate } from "../promise";
import { dayText } from "../promiseParts";
import { Link } from "../router";
import { NotFoundScreen } from "../screens";
import { useWorkspace } from "../workspace/context";
import { Empty, Failure, FieldError, Loading } from "../workspace/parts";
import "./messages";
import {
  checkPeriod,
  isPeriodProblem,
  MAX_PERIOD_DAYS,
  type Period,
  type PeriodProblem,
  type PeriodProblems,
  presetOf,
  presetPeriod,
  PRESETS,
} from "./period";
import "./reports.css";
import { type OverdueBand, type OverdueReport, type PeriodReport, reports } from "./reportsApi";

const NO_PROBLEMS: PeriodProblems = { from: null, to: null };

function problemText(problem: PeriodProblem | null, t: Translate): string | null {
  return problem === null ? null : t(`reports.problem.${problem}`, problem === "PERIOD_TOO_LONG" ? { days: MAX_PERIOD_DAYS } : {});
}

/** What the server says is wrong with the period it was sent (`fields.from`, `fields.to`). */
function refusedPeriod(error: ApiError | null): PeriodProblems {
  if (error?.code !== "VALIDATION") {
    return NO_PROBLEMS;
  }
  const read = (field: string) => {
    const problem = error.fields[field];
    return isPeriodProblem(problem) ? problem : null;
  };
  return { from: read("from"), to: read("to") };
}

/** How long a membership's code is: the end of its identifier, as on the panel's staff screen. */
const CODE_LENGTH = 6;

/**
 * A list of a report: a real table on a wide screen of the web panel, readable rows on a phone. The
 * figures are the same either way; `row` is how one item reads when there are no columns to align it in.
 */
function Figures<T>({
  caption,
  columns,
  items,
  rowKey,
  row,
  foot,
  footRow,
}: {
  caption: string;
  columns: readonly Column<T>[];
  items: readonly T[];
  rowKey: (item: T) => string;
  row: (item: T) => { name: ReactNode; amount?: string; detail?: string };
  foot?: readonly ReactNode[];
  footRow?: { name: string; amount?: string; detail?: string };
}) {
  const desktop = useDesktop();
  if (desktop) {
    return <desktop.Table caption={caption} columns={columns} items={items} rowKey={rowKey} {...(foot ? { foot } : {})} />;
  }
  const line = (key: string, { name, amount, detail }: { name: ReactNode; amount?: string; detail?: string }, total = false) => (
    <li key={key} className={total ? "row row--total" : "row"}>
      <p className="row__link">
        <span className="row__name">{name}</span>
        {amount === undefined ? null : <span className="row__amount">{amount}</span>}
      </p>
      {detail === undefined ? null : <p className="row__meta">{detail}</p>}
    </li>
  );
  return (
    <ul className="rows" aria-label={caption}>
      {items.map((item) => line(rowKey(item), row(item)))}
      {footRow ? line("total", footRow, true) : null}
    </ul>
  );
}

function Totals({ report }: { report: PeriodReport }) {
  const { t, language } = useI18n();
  const money = (amount: number) => formatMoney(amount, language);
  const activity = (count: number, customers?: number) => {
    const entries = t("reports.entries", { count });
    return customers === undefined ? entries : t("reports.activity", { entries, customers: formatCustomerCount(customers, language) });
  };
  const { outstanding, credit, payments, opening, reversals } = report;
  // The server states that this holds exactly; it is shown in figures so a person can check it, and a
  // response in which it does not hold is said to be wrong rather than passed off as a report.
  const computed = outstanding.start + credit.amount + opening.amount - payments.amount;

  return (
    <>
      <dl className="figures">
        <div className="figure">
          <dt>{t("reports.outstanding.start")}</dt>
          <dd>{money(outstanding.start)}</dd>
        </div>
        <div className="figure">
          <dt>{t("reports.outstanding.end")}</dt>
          <dd>{money(outstanding.end)}</dd>
        </div>
        <div className="figure">
          <dt>{t("reports.netChange")}</dt>
          <dd>{report.netChange > 0 ? `+${money(report.netChange)}` : money(report.netChange)}</dd>
        </div>
        <div className="figure">
          <dt>{t("reports.credit")}</dt>
          <dd>{money(credit.amount)}</dd>
          <dd className="figure__note">{activity(credit.count, credit.customers)}</dd>
        </div>
        <div className="figure">
          <dt>{t("reports.payments")}</dt>
          <dd>{money(payments.amount)}</dd>
          <dd className="figure__note">{activity(payments.count, payments.customers)}</dd>
        </div>
        <div className="figure">
          <dt>{t("reports.opening")}</dt>
          <dd>{money(opening.amount)}</dd>
          <dd className="figure__note">{activity(opening.count)}</dd>
        </div>
        <div className="figure">
          <dt>{t("reports.reversals")}</dt>
          <dd>{money(reversals.amount)}</dd>
          <dd className="figure__note">{activity(reversals.count)}</dd>
        </div>
        <div className="figure">
          <dt>{t("reports.newCustomers")}</dt>
          <dd>{formatCustomerCount(report.newCustomers, language)}</dd>
        </div>
        <div className="figure">
          <dt>{t("reports.disputes")}</dt>
          <dd>{report.disputesOpened}</dd>
        </div>
      </dl>

      <section aria-labelledby="reports-equation">
        <h3 id="reports-equation">{t("reports.equation")}</h3>
        <p className="hint">{t("reports.equation.hint")}</p>
        <p className="equation">
          {t("reports.equation.line", {
            start: money(outstanding.start),
            credit: money(credit.amount),
            opening: money(opening.amount),
            payments: money(payments.amount),
            end: money(outstanding.end),
          })}
        </p>
        {computed === outstanding.end ? null : (
          <p className="notice notice--error" role="alert">
            {t("reports.equation.mismatch", { computed: money(computed), end: money(outstanding.end) })}
          </p>
        )}
      </section>
    </>
  );
}

function OnTime({ report }: { report: PeriodReport }) {
  const { t, language } = useI18n();
  const { percent, dueAmount, onTimeAmount } = report.onTime;
  return (
    <section aria-labelledby="reports-on-time">
      <h3 id="reports-on-time">{t("reports.onTime")}</h3>
      {percent === null ? (
        <p className="state">{t("reports.onTime.none")}</p>
      ) : (
        <>
          <p className="balance">
            <strong>{t("reports.onTime.value", { percent })}</strong>
          </p>
          <p className="row__meta">
            {t("reports.onTime.amounts", {
              due: formatMoney(dueAmount, language),
              onTime: formatMoney(onTimeAmount, language),
            })}
          </p>
        </>
      )}
    </section>
  );
}

type Day = PeriodReport["days"][number];
type Debtor = PeriodReport["topDebtors"][number];
type Member = PeriodReport["staff"][number];

function Days({ days }: { days: readonly Day[] }) {
  const { t, language } = useI18n();
  const money = (amount: number) => formatMoney(amount, language);
  // A year of days of which most saw nothing is not a table a person reads: only days with a sale or
  // a payment are listed, and the note under the list says how many were left out.
  const busy = days.filter((day) => day.credit !== 0 || day.payments !== 0);
  const quiet = days.length - busy.length;
  const columns: Column<Day>[] = [
    { id: "day", header: t("reports.days.day"), rowHeader: true, cell: (day) => dayText(day.date, language) },
    { id: "credit", header: t("reports.days.credit"), numeric: true, cell: (day) => money(day.credit) },
    { id: "payments", header: t("reports.days.payments"), numeric: true, cell: (day) => money(day.payments) },
  ];
  return (
    <section aria-labelledby="reports-days">
      <h3 id="reports-days">{t("reports.days")}</h3>
      {busy.length === 0 ? (
        <Empty>{t("reports.days.none")}</Empty>
      ) : (
        <>
          <Figures
            caption={t("reports.days")}
            columns={columns}
            items={busy}
            rowKey={(day) => day.date}
            row={(day) => ({
              name: dayText(day.date, language),
              detail: t("reports.days.row", { credit: money(day.credit), payments: money(day.payments) }),
            })}
          />
          {quiet > 0 ? <p className="row__meta">{t("reports.days.quiet", { count: quiet })}</p> : null}
        </>
      )}
    </section>
  );
}

function TopDebtors({ debtors }: { debtors: readonly Debtor[] }) {
  const { t, language } = useI18n();
  const link = (debtor: Debtor) => <Link to={`/customers/${debtor.customerId}`}>{debtor.displayName}</Link>;
  const columns: Column<Debtor>[] = [
    { id: "customer", header: t("reports.debtors.customer"), rowHeader: true, cell: link },
    { id: "balance", header: t("reports.debtors.balance"), numeric: true, cell: (debtor) => formatMoney(debtor.balance, language) },
  ];
  return (
    <section aria-labelledby="reports-debtors">
      <h3 id="reports-debtors">{t("reports.debtors")}</h3>
      {debtors.length === 0 ? (
        <Empty>{t("reports.debtors.none")}</Empty>
      ) : (
        <Figures
          caption={t("reports.debtors")}
          columns={columns}
          items={debtors}
          rowKey={(debtor) => debtor.customerId}
          row={(debtor) => ({ name: link(debtor), amount: formatMoney(debtor.balance, language) })}
        />
      )}
    </section>
  );
}

function Staff({ staff }: { staff: readonly Member[] }) {
  const { membershipId } = useWorkspace();
  const { t, language } = useI18n();
  const money = (amount: number) => formatMoney(amount, language);
  // The API names no one: a member of staff is their role and the end of their membership's identifier.
  const name = (member: Member) => {
    const label = t("reports.staff.member", {
      role: isRole(member.role) ? t(`role.${member.role}`) : member.role,
      code: member.membershipId.slice(-CODE_LENGTH),
    });
    return member.membershipId === membershipId ? t("reports.staff.you", { member: label }) : label;
  };
  const columns: Column<Member>[] = [
    { id: "who", header: t("reports.staff.who"), rowHeader: true, cell: name },
    { id: "credit", header: t("reports.staff.credit"), numeric: true, cell: (member) => money(member.credit.amount) },
    { id: "creditCount", header: t("reports.staff.creditCount"), numeric: true, cell: (member) => member.credit.count },
    { id: "payments", header: t("reports.staff.payments"), numeric: true, cell: (member) => money(member.payments.amount) },
    { id: "paymentCount", header: t("reports.staff.paymentCount"), numeric: true, cell: (member) => member.payments.count },
  ];
  return (
    <section aria-labelledby="reports-staff">
      <h3 id="reports-staff">{t("reports.staff")}</h3>
      {staff.length === 0 ? (
        <Empty>{t("reports.staff.none")}</Empty>
      ) : (
        <Figures
          caption={t("reports.staff")}
          columns={columns}
          items={staff}
          rowKey={(member) => member.membershipId}
          row={(member) => ({
            name: name(member),
            detail: t("reports.staff.row", {
              credit: money(member.credit.amount),
              creditCount: member.credit.count,
              payments: money(member.payments.amount),
              paymentCount: member.payments.count,
            }),
          })}
        />
      )}
    </section>
  );
}

function PeriodFigures({ period, onRefused }: { period: Period; onRefused: (problems: PeriodProblems) => void }) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const calls = useMemo(() => reports(api), [api]);
  const { state, reload } = useLoad(
    (signal) =>
      calls.period(period.from, period.to, signal).catch((error: ApiError) => {
        const refused = refusedPeriod(error);
        if (refused.from !== null || refused.to !== null) {
          onRefused(refused);
        }
        throw error;
      }),
    [calls, period.from, period.to],
  );

  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }
  const report = state.data;
  const title =
    report.from === report.to
      ? t("reports.period.oneDay", { date: dayText(report.from, language) })
      : t("reports.period.title", { from: dayText(report.from, language), to: dayText(report.to, language) });
  return (
    <section aria-labelledby="reports-period">
      <h2 id="reports-period">{title}</h2>
      <Totals report={report} />
      <OnTime report={report} />
      <Days days={report.days} />
      <TopDebtors debtors={report.topDebtors} />
      <Staff staff={report.staff} />
    </section>
  );
}

function bandName(band: OverdueBand, t: Translate): string {
  return band.toDays === null
    ? t("reports.band.over", { from: band.fromDays })
    : t("reports.band.range", { from: band.fromDays, to: band.toDays });
}

function OverdueByAge() {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const calls = useMemo(() => reports(api), [api]);
  const { state, reload } = useLoad((signal) => calls.overdue(signal), [calls]);

  let body: ReactNode;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else {
    const report: OverdueReport = state.data;
    const money = (amount: number) => formatMoney(amount, language);
    const columns: Column<OverdueBand>[] = [
      { id: "band", header: t("reports.overdue.band"), rowHeader: true, cell: (band) => bandName(band, t) },
      { id: "amount", header: t("reports.overdue.amount"), numeric: true, cell: (band) => money(band.amount) },
      {
        id: "customers",
        header: t("reports.overdue.customers"),
        numeric: true,
        cell: (band) => formatCustomerCount(band.customers, language),
      },
    ];
    body = (
      <>
        <p className="row__meta">{t("reports.overdue.asOf", { date: dayText(report.asOf, language) })}</p>
        {report.total.amount === 0 ? (
          <Empty>{t("reports.overdue.none")}</Empty>
        ) : (
          <>
            <Figures
              caption={t("reports.overdue")}
              columns={columns}
              items={report.bands}
              rowKey={(band) => band.band}
              row={(band) => ({
                name: bandName(band, t),
                amount: money(band.amount),
                detail: formatCustomerCount(band.customers, language),
              })}
              foot={[t("reports.overdue.total"), money(report.total.amount), formatCustomerCount(report.total.customers, language)]}
              footRow={{
                name: t("reports.overdue.total"),
                amount: money(report.total.amount),
                detail: formatCustomerCount(report.total.customers, language),
              }}
            />
            <p className="hint">{t("reports.overdue.totalHint")}</p>
          </>
        )}
      </>
    );
  }
  return (
    <section aria-labelledby="reports-overdue">
      <h2 id="reports-overdue">{t("reports.overdue")}</h2>
      {body}
    </section>
  );
}

function Reports() {
  const { now } = useWorkspace();
  const { t } = useI18n();
  const today = tashkentDay(now());
  const [period, setPeriod] = useState<Period>(() => presetPeriod("month", today));
  const [from, setFrom] = useState(period.from);
  const [to, setTo] = useState(period.to);
  const [problems, setProblems] = useState<PeriodProblems>(NO_PROBLEMS);
  const active = presetOf(period, today);

  const show = (next: Period) => {
    setFrom(next.from);
    setTo(next.to);
    setProblems(NO_PROBLEMS);
    setPeriod(next);
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const checked = checkPeriod(from, to, today);
    if (checked.ok) {
      show(checked.period);
    } else {
      setProblems(checked.problems);
    }
  };

  const shown = { from: problemText(problems.from, t), to: problemText(problems.to, t) };
  return (
    <>
      <form className="period" onSubmit={onSubmit} noValidate aria-label={t("reports.period")}>
        <div className="toggle" role="group" aria-label={t("reports.period")}>
          {PRESETS.map((preset) => (
            <button
              key={preset}
              type="button"
              className="toggle__option"
              aria-pressed={active === preset}
              onClick={() => show(presetPeriod(preset, today))}
            >
              {t(`reports.preset.${preset}`)}
            </button>
          ))}
        </div>
        <div className="period__fields">
          <div className="field">
            <label htmlFor="reports-from">{t("reports.from")}</label>
            <input
              id="reports-from"
              type="date"
              className="input"
              value={from}
              max={toIsoDate(today)}
              aria-invalid={shown.from !== null}
              aria-describedby="reports-from-error reports-hint"
              onChange={(event) => {
                setFrom(event.target.value);
                setProblems((current) => ({ ...current, from: null }));
              }}
            />
            <FieldError id="reports-from-error" message={shown.from} />
          </div>
          <div className="field">
            <label htmlFor="reports-to">{t("reports.to")}</label>
            <input
              id="reports-to"
              type="date"
              className="input"
              value={to}
              max={toIsoDate(today)}
              aria-invalid={shown.to !== null}
              aria-describedby="reports-to-error reports-hint"
              onChange={(event) => {
                setTo(event.target.value);
                setProblems((current) => ({ ...current, to: null }));
              }}
            />
            <FieldError id="reports-to-error" message={shown.to} />
          </div>
          <button type="submit" className="button button--primary">
            {t("reports.show")}
          </button>
        </div>
        <p className="field__hint" id="reports-hint">
          {t("reports.period.hint", { days: MAX_PERIOD_DAYS })}
        </p>
      </form>
      <PeriodFigures period={period} onRefused={setProblems} />
      <OverdueByAge />
    </>
  );
}

/**
 * Reports for managers and owners (REQ-046): one period of days with what was sold on credit, repaid
 * and repaid on time, the largest debtors, disputes and entries per member of staff; and overdue debt
 * by how late it is. A seller has no such section, and this screen calls nothing for them.
 */
export default function ReportsScreen() {
  const { role } = useWorkspace();
  return canManage(role) ? <Reports /> : <NotFoundScreen />;
}
