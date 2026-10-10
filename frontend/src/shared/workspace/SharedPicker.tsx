import { useEffect, useState, type FormEvent } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import type { CatalogItem, SharedItem } from "../api";
import { formatMoney } from "../format";
import { parsePrice } from "../goods";
import { usePagedList, useSubmit } from "../hooks";
import { categoryKey, SHARED_CATEGORIES } from "../sharedCatalog";
import { priceMessage } from "./catalogRules";
import { useWorkspace } from "./context";
import { Badge, errorText, Failure, FieldError, Loading, LoadMore } from "./parts";

/** How long typing must pause before the catalogue is searched again. */
export const SHARED_SEARCH_DELAY_MS = 300;
const MAX_QUERY_LENGTH = 80;
const PAGE = 20;

/** The photo of a catalogue item, served by this host; a plain square when it has none. Decoration only. */
function Photo({ item }: { item: SharedItem }) {
  return item.image === null ? (
    <span className="thumb thumb--none" aria-hidden="true" />
  ) : (
    <img className="thumb" src={item.image} alt="" width={48} height={48} loading="lazy" decoding="async" />
  );
}

/** A catalogue item as one line: its photo, its name with the package, and its category. */
function ItemLine({ item }: { item: SharedItem }) {
  const { t } = useI18n();
  return (
    <>
      <Photo item={item} />
      <span className="shared__text">
        <span className="row__name">{item.amount === null ? item.name : `${item.name}, ${item.amount}`}</span>
        <span className="row__meta">{t(categoryKey(item.category))}</span>
      </span>
    </>
  );
}

/**
 * The one thing a shop types for a catalogue item: its own price. The catalogue's approximate price is
 * shown under the field as advice and is never put into it, so a price is saved only when typed.
 */
export function SharedPickForm({
  item,
  intro,
  onPicked,
  onBack,
}: {
  item: SharedItem;
  /** A line above the item, when the form opens by itself (after a scan). */
  intro?: string | undefined;
  onPicked: (made: CatalogItem) => void;
  onBack?: (() => void) | undefined;
}) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const [price, setPrice] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const { state, submit } = useSubmit((amount: number, key) => api.pickSharedItem(item.id, amount, key).then(onPicked));
  const pending = state.status === "pending";
  const failure = state.status === "error" ? state.error : null;
  const refused =
    failure === null ? null : failure.code === "VALIDATION" && "price" in failure.fields ? t("catalog.price.invalid") : errorText(failure, t);
  const id = `shared-price-${item.id}`;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const parsed = parsePrice(price);
    setProblem(parsed.ok ? null : priceMessage(parsed.problem, t));
    if (parsed.ok) {
      submit(parsed.amount);
    }
  };

  return (
    <form className="form" onSubmit={onSubmit} noValidate aria-label={t("catalog.shared.add")}>
      {intro === undefined ? null : <p>{intro}</p>}
      <p className="shared__chosen">
        <ItemLine item={item} />
      </p>
      <div className="field">
        <label htmlFor={id}>{t("catalog.shared.yourPrice")}</label>
        <input
          id={id}
          className="input"
          inputMode="numeric"
          value={price}
          autoComplete="off"
          aria-invalid={(problem ?? refused) !== null}
          aria-describedby={`${id}-hint ${id}-error`}
          onChange={(event) => {
            setPrice(event.target.value);
            setProblem(null);
          }}
        />
        {item.priceHint === null ? null : (
          <p className="field__hint" id={`${id}-hint`}>
            {t("catalog.shared.hint", { price: formatMoney(item.priceHint, language) })}
          </p>
        )}
        <FieldError id={`${id}-error`} message={problem ?? refused} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("catalog.shared.add")}
        </button>
        {onBack === undefined ? null : (
          <button type="button" className="button" onClick={onBack} disabled={pending}>
            {t("action.back")}
          </button>
        )}
      </p>
    </form>
  );
}

/**
 * The first step of adding an item while the platform's shared catalogue is on: search it, by name in
 * either language and by category, pick an item and type only the shop's own price. What is not there
 * is added by hand exactly as before (`onManual`).
 */
export function SharedPicker({
  onPicked,
  onManual,
  onCancel,
}: {
  onPicked: (made: CatalogItem) => void;
  onManual: () => void;
  onCancel: () => void;
}) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [chosen, setChosen] = useState<SharedItem | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(text.trim()), SHARED_SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) =>
      api.searchSharedCatalog({ q: query, category: category === "" ? null : category, cursor, limit: PAGE }, signal),
    [api, query, category],
  );

  if (chosen !== null) {
    return <SharedPickForm item={chosen} onPicked={onPicked} onBack={() => setChosen(null)} />;
  }

  return (
    <section className="shared" aria-label={t("catalog.shared.title")}>
      <form
        className="search"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(text.trim());
        }}
      >
        <input
          type="search"
          className="input"
          value={text}
          maxLength={MAX_QUERY_LENGTH}
          autoComplete="off"
          aria-label={t("catalog.shared.search")}
          placeholder={t("catalog.shared.search")}
          onChange={(event) => setText(event.target.value)}
        />
        <button type="submit" className="button">
          {t("action.search")}
        </button>
      </form>
      <div className="field">
        <label htmlFor="shared-category">{t("catalog.shared.category")}</label>
        <select id="shared-category" className="input" value={category} onChange={(event) => setCategory(event.target.value)}>
          <option value="">{t("catalog.shared.category.all")}</option>
          {SHARED_CATEGORIES.map((key) => (
            <option key={key} value={key}>
              {t(categoryKey(key))}
            </option>
          ))}
        </select>
      </div>
      {state.status === "loading" ? <Loading /> : null}
      {state.status === "error" ? <Failure error={state.error} onRetry={reload} /> : null}
      {state.status === "ready" && state.items.length === 0 ? <p className="state">{t("catalog.shared.none")}</p> : null}
      {state.status === "ready" && state.items.length > 0 ? (
        <>
          <ul className="picks picks--tall" aria-label={t("catalog.shared.list")}>
            {state.items.map((item) => (
              <li key={item.id}>
                {/* What the shop already holds is shown and cannot be added a second time. */}
                <button type="button" className="pick pick--shared" disabled={item.picked !== null} onClick={() => setChosen(item)}>
                  <ItemLine item={item} />
                  {item.picked === null ? null : <Badge>{t("catalog.shared.have")}</Badge>}
                </button>
              </li>
            ))}
          </ul>
          {state.nextCursor !== null ? <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} /> : null}
        </>
      ) : null}
      <p className="actions">
        <button type="button" className="button" onClick={onManual}>
          {t("catalog.shared.manual")}
        </button>
        <button type="button" className="button" onClick={onCancel}>
          {t("action.cancel")}
        </button>
      </p>
    </section>
  );
}
