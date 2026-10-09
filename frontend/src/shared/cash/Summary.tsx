import { type FormEvent, useState } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { ApiError } from "../api";
import { tashkentDay } from "../format";
import { useLoad } from "../hooks";
import type { Column } from "../layout";
import { toIsoDate } from "../promise";
import { dayText } from "../promiseParts";
import { Figures } from "../reports/Figures";
// The period, its presets and its problems are the reports' own: the same rules, the same words.
import "../reports/messages";
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
} from "../reports/period";
import { useWorkspace } from "../workspace/context";
import { Empty, Failure, FieldError, Loading } from "../workspace/parts";
import { money } from "./amounts";
import { Balances } from "./Balances";
import { type CashApi, type CashCurrency, type CashSummary, type Direction, DIRECTIONS } from "./cashApi";

const NO_PROBLEMS: PeriodProblems = { from: null, to: null };

function problemText(problem: PeriodProblem | null, t: Translate): string | null {
  return problem === null
    ? null
    : t(`reports.problem.${problem}`, problem === "PERIOD_TOO_LONG" ? { days: MAX_PERIOD_DAYS } : {});
}

function refusedPeriod(error: ApiError): PeriodProblems {
  if (error.code !== "VALIDATION") {
    return NO_PROBLEMS;
  }
  const read = (field: string) => {
    const problem = error.fields[field];
    return isPeriodProblem(problem) ? problem : null;
  };
  return { from: read("from"), to: read("to") };
}

type CategoryRow = CashSummary["categories"][number];
type DayRow = CashSummary["days"][number];

/** The share of a part in a whole, in whole percent; a part of nothing has no share. */
export function shareOf(part: number, whole: number): string {
  return whole > 0 ? `${Math.round((part * 100) / whole)}%` : "—";
}

function ByCategory({ report, several }: { report: CashSummary; several: boolean }) {
  const { t, language } = useI18n();
  if (report.categories.length === 0) {
    return <Empty>{t("cash.summary.categories.none")}</Empty>;
  }
  const table = (currency: CashCurrency, direction: Direction) => {
    const rows = report.categories.filter(
      (row) => row.currency === currency && row.category.direction === direction,
    );
    if (rows.length === 0) {
      return null;
    }
    const sum = rows.reduce((total, row) => total + row.amount, 0);
    const count = rows.reduce((total, row) => total + row.count, 0);
    const show = (amount: number) => money(amount, currency, language);
    const name = (row: CategoryRow) =>
      row.category.archived ? t("cash.summary.archived", { name: row.category.name }) : row.category.name;
    const heading = t(`cash.direction.${direction}`);
    const caption = several
      ? t("cash.summary.of", { direction: heading, currency: t(`cash.currency.${currency}`) })
      : heading;
    const columns: Column<CategoryRow>[] = [
      { id: "category", header: t("cash.summary.category"), rowHeader: true, cell: name },
      { id: "amount", header: t("cash.summary.amount"), numeric: true, cell: (row) => show(row.amount) },
      { id: "count", header: t("cash.summary.count"), numeric: true, cell: (row) => row.count },
      { id: "share", header: t("cash.summary.share"), numeric: true, cell: (row) => shareOf(row.amount, sum) },
    ];
    return (
      <section key={`${currency}-${direction}`} aria-labelledby={`cash-by-${currency}-${direction}`}>
        <h4 id={`cash-by-${currency}-${direction}`}>{caption}</h4>
        <Figures
          caption={caption}
          columns={columns}
          items={rows}
          rowKey={(row) => row.category.id}
          row={(row) => ({
            name: name(row),
            amount: show(row.amount),
            detail: t("cash.summary.categoryRow", { count: row.count, share: shareOf(row.amount, sum) }),
          })}
          foot={[t("cash.balances.total"), show(sum), count, shareOf(sum, sum)]}
          footRow={{ name: t("cash.balances.total"), amount: show(sum) }}
        />
      </section>
    );
  };
  return <>{report.totals.flatMap((total) => DIRECTIONS.map((direction) => table(total.currency, direction)))}</>;
}

function ByDay({ report, several }: { report: CashSummary; several: boolean }) {
  const { t, language } = useI18n();
  const table = (currency: CashCurrency) => {
    const rows = report.days.filter((row) => row.currency === currency);
    if (rows.length === 0) {
      return null;
    }
    const show = (amount: number) => money(amount, currency, language);
    const caption = several
      ? t("cash.summary.days.of", { currency: t(`cash.currency.${currency}`) })
      : t("cash.summary.days");
    const columns: Column<DayRow>[] = [
      { id: "day", header: t("cash.summary.day"), rowHeader: true, cell: (row) => dayText(row.date, language) },
      { id: "income", header: t("cash.balances.income"), numeric: true, cell: (row) => show(row.income) },
      { id: "expense", header: t("cash.balances.expense"), numeric: true, cell: (row) => show(row.expense) },
    ];
    return (
      <section key={currency} aria-labelledby={`cash-days-${currency}`}>
        <h3 id={`cash-days-${currency}`}>{caption}</h3>
        <Figures
          caption={caption}
          columns={columns}
          items={rows}
          rowKey={(row) => row.date}
          row={(row) => ({
            name: dayText(row.date, language),
            detail: t("cash.summary.dayRow", { income: show(row.income), expense: show(row.expense) }),
          })}
        />
      </section>
    );
  };
  return <>{report.totals.map((total) => table(total.currency))}</>;
}

function PeriodFigures({
  calls,
  period,
  onRefused,
}: {
  calls: CashApi;
  period: Period;
  onRefused: (problems: PeriodProblems) => void;
}) {
  const { t, language } = useI18n();
  const { state, reload } = useLoad(
    (signal) =>
      calls.summary(period.from, period.to, signal).catch((error: ApiError) => {
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
  const several = report.totals.length > 1;
  const title =
    report.from === report.to
      ? t("reports.period.oneDay", { date: dayText(report.from, language) })
      : t("reports.period.title", { from: dayText(report.from, language), to: dayText(report.to, language) });
  return (
    <section aria-labelledby="cash-period">
      <h2 id="cash-period">{title}</h2>
      <Balances lines={report.balances} totals={report.totals} labelKey="cash.balances.openingPeriod" />
      <section aria-labelledby="cash-by-category">
        <h3 id="cash-by-category">{t("cash.summary.categories")}</h3>
        <ByCategory report={report} several={several} />
      </section>
      <ByDay report={report} several={several} />
    </section>
  );
}

/**
 * A period of the cash book: the balances over it, what stands in it by category and by day. Every
 * figure is of one currency; a cancelled entry is in none of them.
 */
export function Summary({ calls }: { calls: CashApi }) {
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
            <label htmlFor="cash-from">{t("reports.from")}</label>
            <input
              id="cash-from"
              type="date"
              className="input"
              value={from}
              max={toIsoDate(today)}
              aria-invalid={shown.from !== null}
              aria-describedby="cash-from-error cash-period-hint"
              onChange={(event) => {
                setFrom(event.target.value);
                setProblems((current) => ({ ...current, from: null }));
              }}
            />
            <FieldError id="cash-from-error" message={shown.from} />
          </div>
          <div className="field">
            <label htmlFor="cash-to">{t("reports.to")}</label>
            <input
              id="cash-to"
              type="date"
              className="input"
              value={to}
              max={toIsoDate(today)}
              aria-invalid={shown.to !== null}
              aria-describedby="cash-to-error cash-period-hint"
              onChange={(event) => {
                setTo(event.target.value);
                setProblems((current) => ({ ...current, to: null }));
              }}
            />
            <FieldError id="cash-to-error" message={shown.to} />
          </div>
          <button type="submit" className="button button--primary">
            {t("reports.show")}
          </button>
        </div>
        <p className="field__hint" id="cash-period-hint">
          {t("reports.period.hint", { days: MAX_PERIOD_DAYS })}
        </p>
      </form>
      <PeriodFigures calls={calls} period={period} onRefused={setProblems} />
    </>
  );
}
