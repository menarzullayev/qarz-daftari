import { useMemo, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { tashkentDay } from "../format";
import { useLoad } from "../hooks";
import { toIsoDate } from "../promise";
import "../reports/reports.css";
import { NotFoundScreen } from "../screens";
import { useMay, useWorkspace } from "../workspace/context";
import { Failure, Loading } from "../workspace/parts";
import { money } from "./amounts";
import { type CashCategories, type CashEntry, cashOf, type Direction } from "./cashApi";
import { CashEntryForm } from "./CashEntryForm";
import { Categories } from "./Categories";
import { DayBook } from "./DayBook";
import "./messages";
import { Summary } from "./Summary";

const VIEWS = ["day", "summary", "categories"] as const;
type View = (typeof VIEWS)[number];

function Cash() {
  const { api, now } = useWorkspace();
  const { t, language } = useI18n();
  const can = useMay();
  const calls = useMemo(() => cashOf(api), [api]);
  const today = toIsoDate(tashkentDay(now()));
  const [view, setView] = useState<View>("day");
  const [day, setDay] = useState(today);
  const [form, setForm] = useState<Direction | null>(null);
  const [saved, setSaved] = useState<CashEntry | null>(null);
  // Counts what was written here, so that the day's book is read again after each write.
  const [version, setVersion] = useState(0);
  const changed = () => setVersion((count) => count + 1);
  // The categories to choose from and the currencies an entry may be in; the first read of a shop's
  // cash book is also what writes its default categories. What was read stays on the screen while it
  // is read again after a category changes, so the screen does not blink.
  const [listsVersion, setListsVersion] = useState(0);
  const [known, setKnown] = useState<CashCategories | null>(null);
  const lists = useLoad(
    (signal) =>
      calls.categories(signal).then((answer) => {
        setKnown(answer);
        return answer;
      }),
    [calls, listsVersion],
  );

  const mayView = can("cash.view");
  const directions: Direction[] = [
    ...(can("cash.record_income") ? (["income"] as const) : []),
    ...(can("cash.record_expense") ? (["expense"] as const) : []),
  ];
  const offered = VIEWS.filter((candidate) => (candidate === "categories" ? can("cash.categories") : mayView));
  const shown: View | null = offered.includes(view) ? view : (offered[0] ?? null);

  if (known === null) {
    return lists.state.status === "error" ? (
      <Failure error={lists.state.error} onRetry={lists.reload} />
    ) : (
      <Loading />
    );
  }
  const { items, currencies } = known;

  return (
    <>
      {offered.length > 1 ? (
        <div className="toggle" role="group" aria-label={t("cash.view")}>
          {offered.map((candidate) => (
            <button
              key={candidate}
              type="button"
              className="toggle__option"
              aria-pressed={shown === candidate}
              onClick={() => setView(candidate)}
            >
              {t(`cash.view.${candidate}`)}
            </button>
          ))}
        </div>
      ) : null}

      {/* Recording is offered beside the day's book, and to a member who may record but not read. */}
      {shown !== "summary" && shown !== "categories" && directions.length > 0 ? (
        <>
          {saved ? (
            <p className="notice notice--done" role="status">
              {t(`cash.form.saved.${saved.direction}`, { amount: money(saved.amount, saved.currency, language) })}
            </p>
          ) : null}
          {form === null ? (
            <p className="actions">
              {directions.map((direction) => (
                <button
                  key={direction}
                  type="button"
                  className={direction === "income" ? "button button--primary" : "button"}
                  onClick={() => {
                    setSaved(null);
                    setForm(direction);
                  }}
                >
                  {t(`cash.add.${direction}`)}
                </button>
              ))}
            </p>
          ) : (
            <CashEntryForm
              key={form}
              calls={calls}
              direction={form}
              categories={items}
              currencies={currencies}
              day={day}
              onSaved={(entry) => {
                setSaved(entry);
                setForm(null);
                changed();
              }}
              onCancel={() => setForm(null)}
            />
          )}
        </>
      ) : null}

      {shown === "day" ? (
        <DayBook calls={calls} day={day} onDay={setDay} version={version} onChanged={changed} />
      ) : null}
      {shown === "summary" ? <Summary calls={calls} /> : null}
      {shown === "categories" ? (
        <Categories
          calls={calls}
          categories={items}
          onChanged={() => {
            setListsVersion((count) => count + 1);
            changed();
          }}
        />
      ) : null}
    </>
  );
}

/**
 * The cash book (expansion module H): the day's book with what came in and went out by cash, card and
 * transfer, a period's summary by category, and the shop's categories. Offered only while the platform
 * switch is on, to a member who may read the book or record in it; this screen calls nothing for
 * anyone else.
 */
export default function CashScreen() {
  const can = useMay();
  const allowed = can("cash.view") || can("cash.record_income") || can("cash.record_expense") || can("cash.categories");
  return allowed ? <Cash /> : <NotFoundScreen />;
}
