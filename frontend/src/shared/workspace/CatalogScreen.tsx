import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { ApiError, CatalogAction, CatalogItem, CatalogItemPatch, NewCatalogItem } from "../api";
import { formatMoney } from "../format";
import { parsePrice } from "../goods";
import { usePagedList, useSubmit } from "../hooks";
import { useDesktop } from "../layout";
import { cleanItemName, itemNameProblem, MAX_ITEM_NAME, MAX_UNIT_INPUT, priceMessage } from "./catalogRules";
import { useMay, useWorkspace } from "./context";
import { Badge, Empty, errorText, Failure, FieldError, Loading, LoadMore } from "./parts";
import { SharedPicker } from "./SharedPicker";

/** How long typing must pause before the list is searched again. */
export const CATALOG_SEARCH_DELAY_MS = 300;
const MAX_QUERY_LENGTH = 80;
const MERGE_PAGE = 10;

/** "review" is the queue of goods sellers typed on a sale that no manager has looked at yet (REQ-040). */
type View = "active" | "hidden" | "review";

type ItemErrors = { name: string | null; unit: string | null; price: string | null };
const NO_ITEM_ERRORS: ItemErrors = { name: null, unit: null, price: null };

/** Places a server refusal next to the field it is about. */
function refusedItemFields(error: ApiError | null, t: Translate): ItemErrors {
  if (error === null) {
    return NO_ITEM_ERRORS;
  }
  if (error.code === "CATALOG_NAME_TAKEN") {
    return { ...NO_ITEM_ERRORS, name: errorText(error, t) };
  }
  if (error.code !== "VALIDATION") {
    return NO_ITEM_ERRORS;
  }
  return {
    name: "name" in error.fields ? t("catalog.name.invalid", { max: MAX_ITEM_NAME }) : null,
    unit: "unit" in error.fields ? t("catalog.unit.invalid") : null,
    price: "price" in error.fields ? t("catalog.price.invalid") : null,
  };
}

type ItemInput = { name: string; unit: string; price: number };

/** The form for a new item and for changing one: name, unit and price. */
function ItemForm({
  item,
  onSend,
  onSaved,
  onCancel,
}: {
  /** The item being changed; null for a new one. */
  item: CatalogItem | null;
  /** Sends what was typed; resolves once the server has saved it. */
  onSend: (input: ItemInput, idempotencyKey: string) => Promise<unknown>;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const [name, setName] = useState(item?.name ?? "");
  const [unit, setUnit] = useState(item?.unit ?? "");
  const [price, setPrice] = useState(item ? String(item.price) : "");
  const [errors, setErrors] = useState<ItemErrors>(NO_ITEM_ERRORS);
  const { state, submit } = useSubmit((input: ItemInput, key) => onSend(input, key).then(onSaved));

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const cleanName = cleanItemName(name);
    const parsed = parsePrice(price);
    const found: ItemErrors = {
      name: itemNameProblem(cleanName, t),
      unit: null,
      price: parsed.ok ? null : priceMessage(parsed.problem, t),
    };
    setErrors(found);
    if (found.name === null && parsed.ok) {
      submit({ name: cleanName, unit: unit.trim(), price: parsed.amount });
    }
  };

  const failure = state.status === "error" ? state.error : null;
  const refused = refusedItemFields(failure, t);
  const shown: ItemErrors = {
    name: errors.name ?? refused.name,
    unit: errors.unit ?? refused.unit,
    price: errors.price ?? refused.price,
  };
  const pending = state.status === "pending";
  const clear = (field: keyof ItemErrors) => setErrors((current) => ({ ...current, [field]: null }));
  const id = item?.id ?? "new";

  return (
    <form className="form" onSubmit={onSubmit} noValidate aria-label={item ? t("action.edit") : t("catalog.add")}>
      {failure && failure.code !== "CATALOG_NAME_TAKEN" ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor={`item-name-${id}`}>{t("catalog.name")}</label>
        <input
          id={`item-name-${id}`}
          className="input"
          value={name}
          autoComplete="off"
          aria-invalid={shown.name !== null}
          aria-describedby={`item-name-error-${id}`}
          onChange={(event) => {
            setName(event.target.value);
            clear("name");
          }}
        />
        <FieldError id={`item-name-error-${id}`} message={shown.name} />
      </div>
      <div className="field">
        <label htmlFor={`item-unit-${id}`}>{t("catalog.unit")}</label>
        <input
          id={`item-unit-${id}`}
          className="input"
          value={unit}
          maxLength={MAX_UNIT_INPUT}
          autoComplete="off"
          aria-invalid={shown.unit !== null}
          aria-describedby={`item-unit-error-${id} item-unit-hint-${id}`}
          onChange={(event) => {
            setUnit(event.target.value);
            clear("unit");
          }}
        />
        <p className="field__hint" id={`item-unit-hint-${id}`}>
          {t("catalog.unit.hint")}
        </p>
        <FieldError id={`item-unit-error-${id}`} message={shown.unit} />
      </div>
      <div className="field">
        <label htmlFor={`item-price-${id}`}>{t("catalog.priceField")}</label>
        <input
          id={`item-price-${id}`}
          className="input"
          inputMode="numeric"
          value={price}
          autoComplete="off"
          aria-invalid={shown.price !== null}
          aria-describedby={`item-price-error-${id}`}
          onChange={(event) => {
            setPrice(event.target.value);
            clear("price");
          }}
        />
        <FieldError id={`item-price-error-${id}`} message={shown.price} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("action.save")}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={pending}>
          {t("action.cancel")}
        </button>
      </p>
    </form>
  );
}

/** Only what changed is sent; null when nothing did. An emptied unit is sent as "", the default unit. */
export function itemPatch(item: CatalogItem, input: ItemInput): CatalogItemPatch | null {
  const patch: CatalogItemPatch = {};
  if (input.name !== item.name) {
    patch.name = input.name;
  }
  if (input.unit !== item.unit) {
    patch.unit = input.unit;
  }
  if (input.price !== item.price) {
    patch.price = input.price;
  }
  return Object.keys(patch).length === 0 ? null : patch;
}

/** Chooses the reviewed item a learned one is another spelling of, by search, and merges after a yes. */
function MergePanel({ item, onMerged, onCancel }: { item: CatalogItem; onMerged: () => void; onCancel: () => void }) {
  const { api } = useWorkspace();
  const { t, language } = useI18n();
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [target, setTarget] = useState<CatalogItem | null>(null);
  const merge = useSubmit((into: string, key) => api.mergeCatalogItem(item.id, into, key).then(onMerged));

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(text.trim()), CATALOG_SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  // The server merges only into a shown item that has itself been reviewed, so only those are offered.
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) => api.listCatalog({ q: query, status: "active", learned: false, cursor, limit: MERGE_PAGE }, signal),
    [api, query],
  );

  const pending = merge.state.status === "pending";
  const failure = merge.state.status === "error" ? merge.state.error : null;

  if (target !== null) {
    return (
      <div className="notice">
        {failure ? (
          <p className="field__error" role="alert">
            {errorText(failure, t)}
          </p>
        ) : null}
        <p>{t("catalog.merge.confirm", { from: item.name, into: target.name })}</p>
        <p className="actions">
          <button type="button" className="button button--primary" onClick={() => merge.submit(target.id)} disabled={pending}>
            {pending ? t("state.saving") : t("catalog.merge.yes")}
          </button>
          <button type="button" className="button" onClick={() => setTarget(null)} disabled={pending}>
            {t("action.back")}
          </button>
        </p>
      </div>
    );
  }

  const candidates = state.status === "ready" ? state.items.filter((candidate) => candidate.id !== item.id) : [];
  return (
    <div className="notice">
      <input
        type="search"
        className="input"
        value={text}
        maxLength={MAX_QUERY_LENGTH}
        autoComplete="off"
        aria-label={t("catalog.merge.search")}
        placeholder={t("catalog.merge.search")}
        onChange={(event) => setText(event.target.value)}
      />
      {state.status === "loading" ? <Loading /> : null}
      {state.status === "error" ? <Failure error={state.error} onRetry={reload} /> : null}
      {state.status === "ready" && candidates.length === 0 ? <p className="state">{t("catalog.merge.none")}</p> : null}
      {state.status === "ready" && candidates.length > 0 ? (
        <>
          <ul className="picks" aria-label={t("catalog.merge.list")}>
            {candidates.map((candidate) => (
              <li key={candidate.id}>
                <button type="button" className="pick" onClick={() => setTarget(candidate)}>
                  <span className="row__name">{candidate.name}</span>
                  <span className="row__meta">
                    {t("catalog.price", { price: formatMoney(candidate.price, language), unit: candidate.unit })}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {state.nextCursor !== null ? (
            <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} />
          ) : null}
        </>
      ) : null}
      <p className="actions">
        <button type="button" className="button" onClick={onCancel}>
          {t("action.cancel")}
        </button>
      </p>
    </div>
  );
}

/** `byHand`: the form for typing a new item, which is the whole of "create" while the shared catalogue is off. */
type Panel = { kind: "create"; byHand?: true } | { kind: "edit"; id: string } | { kind: "merge"; id: string } | null;

/**
 * The shop's goods: names, units and current prices (REQ-039). Every member of staff reads it; a
 * manager or an owner adds, changes, hides and shows items and settles the goods sellers typed on a
 * sale (accept, dismiss, or merge into an existing item). A changed price never touches a saved entry
 * (REQ-041): a goods line keeps its own name and price.
 */
export function CatalogScreen() {
  const { api, features } = useWorkspace();
  const can = useMay();
  const { t, language } = useI18n();
  const desktop = useDesktop();
  const mayManage = can("goods.edit");
  // While the platform's shared catalogue is on, adding starts by searching it; typing by hand is one
  // button away and is exactly the form below.
  const pickFirst = features?.catalog === true;
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [view, setView] = useState<View>("active");
  const [panel, setPanel] = useState<Panel>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(text.trim()), CATALOG_SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  // A seller has no review queue: whatever the state says, they read the shown or the hidden list.
  const shownView: View = view === "review" && !mayManage ? "active" : view;
  const { state, reload, loadMore } = usePagedList(
    (cursor, signal) =>
      api.listCatalog(
        shownView === "review"
          ? { q: query, status: "active", learned: true, cursor }
          : { q: query, status: shownView, cursor },
        signal,
      ),
    [api, query, shownView],
  );

  const done = () => {
    setPanel(null);
    reload();
  };
  // One write at a time for the whole list: a second tap, on this row or another, waits for the first.
  const change = useSubmit((payload: { id: string; action: CatalogAction }, key) =>
    api.changeCatalogItem(payload.id, payload.action, key).then(done),
  );
  const busy = change.state.status === "pending";

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setQuery(text.trim());
  };

  // What a manager or an owner can do with one item: its form when one is open, its buttons otherwise.
  const controls = (item: CatalogItem) => {
    const act = (action: CatalogAction, label: string) => (
      <button
        type="button"
        className="button button--small"
        onClick={() => change.submit({ id: item.id, action })}
        disabled={busy}
      >
        {label}
      </button>
    );
    return panel?.kind === "edit" && panel.id === item.id ? (
          <ItemForm
            item={item}
            onSend={(input, key) => {
              const patch = itemPatch(item, input);
              // Nothing changed: nothing to save, and the server refuses an empty change.
              return patch === null ? Promise.resolve() : api.updateCatalogItem(item.id, patch, key);
            }}
            onSaved={done}
            onCancel={() => setPanel(null)}
          />
        ) : panel?.kind === "merge" && panel.id === item.id ? (
          <MergePanel item={item} onMerged={done} onCancel={() => setPanel(null)} />
        ) : (
          <p className="actions">
            <button
              type="button"
              className="button button--small"
              onClick={() => setPanel({ kind: "edit", id: item.id })}
              disabled={busy}
            >
              {t("action.edit")}
            </button>
            {item.status === "active" ? act("hide", t("catalog.hide")) : act("unhide", t("catalog.unhide"))}
            {item.learned ? (
              <>
                {act("accept", t("catalog.accept"))}
                {act("dismiss", t("catalog.dismiss"))}
                <button
                  type="button"
                  className="button button--small"
                  onClick={() => setPanel({ kind: "merge", id: item.id })}
                  disabled={busy}
                >
                  {t("catalog.merge")}
                </button>
              </>
            ) : null}
          </p>
        );
  };

  const row = (item: CatalogItem) => (
    <li key={item.id} className="row">
      <p className="row__link">
        <span className="row__name">{item.name}</span>
        <span className="row__amount">
          {t("catalog.price", { price: formatMoney(item.price, language), unit: item.unit })}
        </span>
      </p>
      {item.learned ? (
        <p className="row__meta">
          <Badge tone="accent">{t("catalog.learned")}</Badge>
        </p>
      ) : null}
      {item.mergedInto !== null ? <p className="row__meta">{t("catalog.merged")}</p> : null}
      {mayManage ? controls(item) : null}
    </li>
  );

  let body: ReactNode;
  if (state.status === "loading") {
    body = <Loading />;
  } else if (state.status === "error") {
    body = <Failure error={state.error} onRetry={reload} />;
  } else if (state.items.length === 0) {
    body = (
      <Empty>
        {query !== ""
          ? t("catalog.noMatch")
          : shownView === "review"
            ? t("catalog.noReview")
            : shownView === "hidden"
              ? t("catalog.noHidden")
              : t("catalog.none")}
      </Empty>
    );
  } else {
    body = (
      <>
        {desktop ? (
          <desktop.CatalogTable
            items={state.items}
            controls={mayManage ? controls : null}
            openId={panel?.kind === "edit" || panel?.kind === "merge" ? panel.id : null}
          />
        ) : (
          <ul className="rows">{state.items.map(row)}</ul>
        )}
        {state.nextCursor !== null ? (
          <LoadMore loading={state.loadingMore} error={state.moreError} onClick={loadMore} />
        ) : null}
      </>
    );
  }

  const views: readonly View[] = mayManage ? ["active", "hidden", "review"] : ["active", "hidden"];
  return (
    <>
      <form className="search" role="search" onSubmit={onSubmit}>
        <input
          type="search"
          className="input"
          value={text}
          maxLength={MAX_QUERY_LENGTH}
          aria-label={t("catalog.search")}
          placeholder={t("catalog.search")}
          onChange={(event) => setText(event.target.value)}
        />
        <button type="submit" className="button">
          {t("action.search")}
        </button>
      </form>
      <div className="bar">
        <div className="toggle" role="group" aria-label={t("catalog.view")}>
          {views.map((option) => (
            <button
              key={option}
              type="button"
              className="toggle__option"
              aria-pressed={shownView === option}
              onClick={() => {
                setView(option);
                setPanel(null);
                change.reset();
              }}
            >
              {t(`catalog.view.${option}`)}
            </button>
          ))}
        </div>
        {mayManage && panel?.kind !== "create" ? (
          <button type="button" className="button" onClick={() => setPanel({ kind: "create" })}>
            {t("catalog.add")}
          </button>
        ) : null}
      </div>
      {mayManage && panel?.kind === "create" && pickFirst && panel.byHand !== true ? (
        <SharedPicker onPicked={done} onManual={() => setPanel({ kind: "create", byHand: true })} onCancel={() => setPanel(null)} />
      ) : null}
      {mayManage && panel?.kind === "create" && (!pickFirst || panel.byHand === true) ? (
        <ItemForm
          item={null}
          onSend={(input, key) => {
            const created: NewCatalogItem = { name: input.name, unit: input.unit === "" ? null : input.unit, price: input.price };
            return api.createCatalogItem(created, key);
          }}
          onSaved={done}
          onCancel={() => setPanel(null)}
        />
      ) : null}
      {shownView === "review" ? <p className="hint">{t("catalog.review.hint")}</p> : null}
      {change.state.status === "error" ? (
        <p className="notice notice--error" role="alert">
          {errorText(change.state.error, t)}
        </p>
      ) : null}
      {body}
    </>
  );
}
