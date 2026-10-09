import { type FormEvent, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { tashkentDay } from "../format";
import { useSubmit } from "../hooks";
import { toIsoDate } from "../promise";
import { useMay, useWorkspace } from "../workspace/context";
import { Badge, Confirm, errorText, FieldError } from "../workspace/parts";
import { type CashApi, type CashCategory, type Direction, DIRECTIONS } from "./cashApi";

/** The longest name of a category (backend/src/qarz/domain/cash.py, `MAX_CATEGORY_NAME`). */
export const MAX_CATEGORY_NAME = 60;

/** A name as it is sent: trimmed, single spaces; null when it is empty or too long. */
export function cleanCategoryName(raw: string): string | null {
  const name = raw.split(/\s+/u).filter(Boolean).join(" ");
  return name.length >= 1 && name.length <= MAX_CATEGORY_NAME ? name : null;
}

function NameForm({
  id,
  label,
  initial,
  submitLabel,
  pending,
  failure,
  direction,
  onSubmit,
  onCancel,
}: {
  id: string;
  label: string;
  initial: string;
  submitLabel: string;
  pending: boolean;
  failure: string | null;
  /** Shown as a choice when a new category is added; absent when one is renamed. */
  direction?: { value: Direction; onChange: (direction: Direction) => void };
  onSubmit: (name: string) => void;
  onCancel?: () => void;
}) {
  const { t } = useI18n();
  const [name, setName] = useState(initial);
  const [problem, setProblem] = useState<string | null>(null);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const clean = cleanCategoryName(name);
    if (clean === null) {
      setProblem(t("cash.categories.name.invalid", { max: MAX_CATEGORY_NAME }));
    } else {
      onSubmit(clean);
    }
  };
  return (
    <form className="form" onSubmit={submit} noValidate>
      {direction ? (
        <div className="field">
          <label htmlFor={`${id}-direction`}>{t("cash.categories.direction")}</label>
          <select
            id={`${id}-direction`}
            className="input"
            value={direction.value}
            onChange={(event) => direction.onChange(event.target.value === "income" ? "income" : "expense")}
          >
            {DIRECTIONS.map((way) => (
              <option key={way} value={way}>
                {t(`cash.direction.${way}`)}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <div className="field">
        <label htmlFor={id}>{label}</label>
        <input
          id={id}
          className="input"
          value={name}
          maxLength={200}
          autoComplete="off"
          aria-invalid={(problem ?? failure) !== null}
          aria-describedby={`${id}-error`}
          onChange={(event) => {
            setName(event.target.value);
            setProblem(null);
          }}
        />
        <FieldError id={`${id}-error`} message={problem ?? failure} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : submitLabel}
        </button>
        {onCancel ? (
          <button type="button" className="button" onClick={onCancel} disabled={pending}>
            {t("action.cancel")}
          </button>
        ) : null}
      </p>
    </form>
  );
}

function CategoryRow({ calls, category, onChanged }: { calls: CashApi; category: CashCategory; onChanged: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState<"rename" | "delete" | null>(null);
  const done = () => {
    setOpen(null);
    onChanged();
  };
  const renaming = useSubmit((name: string, key) => calls.updateCategory(category.id, { name }, key).then(done));
  const archiving = useSubmit((archived: boolean, key) =>
    calls.updateCategory(category.id, { archived }, key).then(done),
  );
  const deleting = useSubmit((_: null, key) => calls.deleteCategory(category.id, key).then(done));
  const failed = archiving.state.status === "error" ? archiving.state.error : null;
  const busy = archiving.state.status === "pending";
  return (
    <li className="row">
      <p className="row__link">
        <span className="row__name">{category.name}</span>
        {category.archived ? <Badge>{t("cash.categories.archived")}</Badge> : null}
      </p>
      {category.fixed ? <p className="row__meta">{t("cash.categories.fixed")}</p> : null}
      {failed ? (
        <p className="field__error" role="alert">
          {errorText(failed, t)}
        </p>
      ) : null}
      {open === null ? (
        <p className="actions">
          <button type="button" className="button button--small" onClick={() => setOpen("rename")}>
            {t("cash.categories.rename")}
          </button>
          {category.fixed ? null : (
            <>
              <button
                type="button"
                className="button button--small"
                disabled={busy}
                onClick={() => archiving.submit(!category.archived)}
              >
                {t(category.archived ? "cash.categories.unarchive" : "cash.categories.archive")}
              </button>
              <button type="button" className="button button--small" onClick={() => setOpen("delete")}>
                {t("cash.categories.delete")}
              </button>
            </>
          )}
        </p>
      ) : null}
      {open === "rename" ? (
        <NameForm
          id={`cash-rename-${category.id}`}
          label={t("cash.categories.renameOf", { name: category.name })}
          initial={category.name}
          submitLabel={t("action.save")}
          pending={renaming.state.status === "pending"}
          failure={renaming.state.status === "error" ? errorText(renaming.state.error, t) : null}
          onSubmit={renaming.submit}
          onCancel={() => {
            renaming.reset();
            setOpen(null);
          }}
        />
      ) : null}
      {open === "delete" ? (
        <Confirm
          question={t("cash.categories.delete.question", { name: category.name })}
          yes={t("cash.categories.delete.yes")}
          no={t("action.cancel")}
          pending={deleting.state.status === "pending"}
          error={deleting.state.status === "error" ? deleting.state.error : null}
          onYes={() => deleting.submit(null)}
          onNo={() => {
            deleting.reset();
            setOpen(null);
          }}
        />
      ) : null}
    </li>
  );
}

/**
 * Copying the ledger's past payments into the book: the owner's decision, made once, and never made for
 * them. A second run writes nothing, and says so.
 */
function Backfill({ calls, onChanged }: { calls: CashApi; onChanged: () => void }) {
  const { now } = useWorkspace();
  const { t } = useI18n();
  const [since, setSince] = useState("");
  const [asking, setAsking] = useState(false);
  const copying = useSubmit((from: string | null, key) =>
    calls.backfill(from, key).then((written) => {
      setAsking(false);
      onChanged();
      return written;
    }),
  );
  const written = copying.state.status === "done" ? copying.state.result : null;
  return (
    <section aria-labelledby="cash-backfill">
      <h2 id="cash-backfill">{t("cash.backfill.title")}</h2>
      <p className="hint">{t("cash.backfill.explain")}</p>
      {written === null ? null : (
        <p className="notice notice--done" role="status">
          {written === 0 ? t("cash.backfill.nothing") : t("cash.backfill.done", { count: written })}
        </p>
      )}
      <div className="field">
        <label htmlFor="cash-backfill-since">{t("cash.backfill.since")}</label>
        <input
          id="cash-backfill-since"
          type="date"
          className="input"
          value={since}
          max={toIsoDate(tashkentDay(now()))}
          aria-describedby="cash-backfill-hint"
          onChange={(event) => {
            setSince(event.target.value);
            copying.reset();
          }}
        />
        <p className="field__hint" id="cash-backfill-hint">
          {t("cash.backfill.since.hint")}
        </p>
      </div>
      {asking ? (
        <Confirm
          question={t("cash.backfill.question")}
          yes={t("cash.backfill.yes")}
          no={t("action.cancel")}
          pending={copying.state.status === "pending"}
          error={copying.state.status === "error" ? copying.state.error : null}
          onYes={() => copying.submit(since === "" ? null : since)}
          onNo={() => {
            copying.reset();
            setAsking(false);
          }}
        />
      ) : (
        <p className="actions">
          <button type="button" className="button" onClick={() => setAsking(true)}>
            {t("cash.backfill.submit")}
          </button>
        </p>
      )}
    </section>
  );
}

/**
 * The shop's categories, income and expense apart: add one, rename one, put one away or bring it back,
 * and delete one nothing was written under. The category customers' payments land in is only renamed.
 */
export function Categories({
  calls,
  categories,
  onChanged,
}: {
  calls: CashApi;
  categories: readonly CashCategory[];
  onChanged: () => void;
}) {
  const { t } = useI18n();
  const can = useMay();
  const [direction, setDirection] = useState<Direction>("expense");
  // A new form after each category added: the name field starts empty again.
  const [added, setAdded] = useState(0);
  const adding = useSubmit((input: { direction: Direction; name: string }, key) =>
    calls.createCategory(input.direction, input.name, key).then(() => {
      setAdded((count) => count + 1);
      onChanged();
    }),
  );
  return (
    <>
      <section aria-labelledby="cash-category-add">
        <h2 id="cash-category-add">{t("cash.categories.add")}</h2>
        <NameForm
          key={added}
          id="cash-category-name"
          label={t("cash.categories.name")}
          initial=""
          submitLabel={t("cash.categories.add")}
          pending={adding.state.status === "pending"}
          failure={adding.state.status === "error" ? errorText(adding.state.error, t) : null}
          direction={{ value: direction, onChange: setDirection }}
          onSubmit={(name) => adding.submit({ direction, name })}
        />
      </section>
      {DIRECTIONS.map((way) => (
        <section key={way} aria-labelledby={`cash-categories-${way}`}>
          <h2 id={`cash-categories-${way}`}>{t(`cash.categories.${way}`)}</h2>
          <ul className="rows" aria-label={t(`cash.categories.${way}`)}>
            {categories
              .filter((category) => category.direction === way)
              .map((category) => (
                <CategoryRow key={category.id} calls={calls} category={category} onChanged={onChanged} />
              ))}
          </ul>
        </section>
      ))}
      {can("cash.backfill") ? <Backfill calls={calls} onChanged={onChanged} /> : null}
    </>
  );
}
