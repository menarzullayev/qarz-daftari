import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { type ApiError, isAbort, toApiError } from "../api";
import { useLatest } from "../hooks";
import { errorText } from "../workspace/parts";

/** One thing to choose: a supplier, a customer. `detail` is a second, quieter word about it. */
export type PickOption = { id: string; name: string; detail?: string | undefined };
export type PickPage = { options: PickOption[]; nextCursor: string | null };
/** One page of the choices that match what was typed: the first for a null cursor, then the server's own. */
export type PickLoader = (query: string, cursor: string | null, signal: AbortSignal) => Promise<PickPage>;

/** How long the typing rests before the server is asked: a name is typed in less. */
export const PICK_DEBOUNCE_MS = 250;
/** The longest text the lists search by (the server's own limit for `q`). */
const MAX_QUERY = 80;

type Found =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "ready"; options: PickOption[]; nextCursor: string | null; more: "idle" | "loading" | ApiError };

type Row = { kind: "none" } | { kind: "option"; option: PickOption } | { kind: "more" };

/**
 * A choice out of a list too long to be sent whole: a field that is typed in, and under it the server's
 * matches, read a page at a time. It replaces a `<select>` filled with the first page of the list, in
 * which whatever came after that page could not be chosen at all.
 *
 * It is the combobox of the ARIA practices with a list popup: the focus stays in the field, the arrow
 * keys move through the options (`aria-activedescendant`), Enter chooses, Escape closes and puts back
 * what was chosen. The last row asks for the next page, and is reached like any option. Emptying the
 * field chooses nothing, where `noneLabel` says that nothing is a choice. The list opens under the
 * field, in the flow: on a phone nothing is drawn over the screen and nothing has to be dismissed.
 */
export function OptionPicker({
  id,
  label,
  value,
  load,
  onChange,
  noneLabel,
  placeholder,
  disabled = false,
  invalid = false,
  describedBy,
}: {
  id: string;
  label: string;
  value: PickOption | null;
  load: PickLoader;
  onChange: (option: PickOption | null) => void;
  /** The name of choosing nothing ("All suppliers"): the first row of the list. Absent: one must be chosen. */
  noneLabel?: string | undefined;
  /** What the empty field says; `noneLabel` when absent. */
  placeholder?: string | undefined;
  disabled?: boolean | undefined;
  invalid?: boolean | undefined;
  /** The id of the field's own error text, read with the hint. */
  describedBy?: string | undefined;
}) {
  const { t } = useI18n();
  const listId = useId();
  const hintId = `${id}-hint`;
  const [text, setText] = useState(value?.name ?? "");
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  // False while a name is being typed: the server is asked once the typing rests, not for every letter.
  const [rested, setRested] = useState(true);
  const [found, setFound] = useState<Found>({ status: "loading" });
  const [active, setActive] = useState(-1);
  const latest = useLatest(load);
  const request = useRef<AbortController | null>(null);
  const waiting = useRef<ReturnType<typeof setTimeout> | null>(null);
  const field = useRef<HTMLInputElement>(null);

  // The chosen one changed from outside (a form reset, a draft loaded): the closed field shows it.
  const chosenId = value?.id ?? null;
  const chosenName = value?.name ?? "";
  useEffect(() => {
    setText(chosenName);
  }, [chosenId, chosenName]);

  // The first page of what matches, read when the list opens and again whenever the search changes.
  useEffect(() => {
    if (!open || !rested) {
      return;
    }
    const controller = new AbortController();
    request.current = controller;
    setFound({ status: "loading" });
    latest.current(query, null, controller.signal).then(
      (page) => {
        if (!controller.signal.aborted) {
          setFound({ status: "ready", options: page.options, nextCursor: page.nextCursor, more: "idle" });
        }
      },
      (error: unknown) => {
        if (!controller.signal.aborted && !isAbort(error)) {
          setFound({ status: "error", error: toApiError(error) });
        }
      },
    );
    return () => controller.abort();
  }, [open, rested, query, latest]);

  useEffect(
    () => () => {
      if (waiting.current !== null) {
        clearTimeout(waiting.current);
      }
    },
    [],
  );

  const rows: Row[] = [];
  // "None" is a choice of the whole list, not a match of a name that is being typed.
  if (noneLabel !== undefined && query === "" && rested) {
    rows.push({ kind: "none" });
  }
  if (found.status === "ready") {
    rows.push(...found.options.map((option): Row => ({ kind: "option", option })));
    if (found.nextCursor !== null) {
      rows.push({ kind: "more" });
    }
  }
  const rowId = (index: number) => `${listId}-${index}`;
  const current = open && active >= 0 && active < rows.length ? active : -1;

  // The option the keys moved to is kept in sight inside the list's own scroll.
  useEffect(() => {
    if (current >= 0) {
      document.getElementById(`${listId}-${current}`)?.scrollIntoView?.({ block: "nearest" });
    }
  }, [current, listId]);

  const stopWaiting = () => {
    if (waiting.current !== null) {
      clearTimeout(waiting.current);
      waiting.current = null;
    }
  };
  const show = () => {
    if (!open && !disabled) {
      setQuery("");
      setRested(true);
      setActive(-1);
      setOpen(true);
    }
  };
  /** Closes the list and puts back the name of what is chosen. */
  const close = (shown: string = chosenName) => {
    stopWaiting();
    setOpen(false);
    setQuery("");
    setRested(true);
    setActive(-1);
    setText(shown);
  };
  const choose = (option: PickOption | null) => {
    close(option?.name ?? "");
    if ((option?.id ?? null) !== chosenId) {
      onChange(option);
    }
  };
  const loadMore = () => {
    const controller = request.current;
    if (found.status !== "ready" || found.nextCursor === null || found.more === "loading" || !controller) {
      return;
    }
    const before = found;
    setFound({ ...before, more: "loading" });
    latest.current(query, before.nextCursor, controller.signal).then(
      (page) => {
        if (!controller.signal.aborted) {
          // The first of the new ones takes the place of the row that asked for them: the keys go on from there.
          setFound({ status: "ready", options: [...before.options, ...page.options], nextCursor: page.nextCursor, more: "idle" });
        }
      },
      (error: unknown) => {
        if (!controller.signal.aborted && !isAbort(error)) {
          setFound({ ...before, more: toApiError(error) });
        }
      },
    );
  };
  const take = (row: Row | undefined) => {
    if (row === undefined) {
      return;
    }
    if (row.kind === "more") {
      loadMore();
    } else {
      choose(row.kind === "none" ? null : row.option);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        show();
        return;
      }
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((index) => Math.max(0, Math.min(rows.length - 1, (index < 0 && step < 0 ? rows.length : index) + step)));
    } else if (event.key === "Enter" && open) {
      // Never the form's own Enter while the list is open.
      event.preventDefault();
      if (current >= 0) {
        take(rows[current]);
      } else if (text.trim() === "" && noneLabel !== undefined) {
        choose(null);
      }
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  };

  const failed = found.status === "error" ? found.error : found.status === "ready" && typeof found.more === "object" ? found.more : null;
  return (
    <div
      className="field combo"
      onBlur={(event) => {
        // Leaving the whole of it, not moving inside it.
        if (open && !event.currentTarget.contains(event.relatedTarget)) {
          // An emptied field is "nothing chosen", where nothing is a choice.
          if (text.trim() === "" && noneLabel !== undefined) {
            choose(null);
          } else {
            close();
          }
        }
      }}
    >
      <label htmlFor={id}>{label}</label>
      <input
        ref={field}
        id={id}
        className="input"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-haspopup="listbox"
        aria-autocomplete="list"
        aria-activedescendant={current >= 0 ? rowId(current) : undefined}
        aria-invalid={invalid}
        aria-describedby={describedBy === undefined ? hintId : `${describedBy} ${hintId}`}
        autoComplete="off"
        spellCheck={false}
        maxLength={MAX_QUERY}
        placeholder={placeholder ?? noneLabel}
        value={text}
        disabled={disabled}
        onChange={(event) => {
          const typed = event.target.value;
          setText(typed);
          setActive(-1);
          setOpen(true);
          // What was found for the letters before is not what matches now: nothing is offered meanwhile.
          request.current?.abort();
          setRested(false);
          setFound({ status: "loading" });
          stopWaiting();
          waiting.current = setTimeout(() => {
            waiting.current = null;
            setQuery(typed.trim());
            setRested(true);
          }, PICK_DEBOUNCE_MS);
        }}
        onFocus={(event) => event.target.select()}
        onClick={show}
        onKeyDown={onKeyDown}
      />
      <p className="field__hint" id={hintId}>
        {t("stock.pick.hint")}
      </p>
      {open ? (
        <div className="combo__list">
          {/* Pressing inside the list must not take the focus out of the field: a choice is made on the click. */}
          <ul
            id={listId}
            className="picks"
            role="listbox"
            tabIndex={-1}
            aria-label={label}
            aria-busy={found.status === "loading"}
            onMouseDown={(event) => event.preventDefault()}
          >
            {rows.map((row, index) => (
              <li
                key={row.kind === "option" ? row.option.id : row.kind}
                id={rowId(index)}
                className={row.kind === "more" ? "pick pick--more" : "pick"}
                role="option"
                tabIndex={-1}
                aria-selected={index === current}
                onClick={() => {
                  take(row);
                  field.current?.focus();
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    take(row);
                    field.current?.focus();
                  }
                }}
              >
                {row.kind === "option" ? (
                  <>
                    <span>{row.option.name}</span>
                    {row.option.detail === undefined ? null : <span className="row__meta">{row.option.detail}</span>}
                  </>
                ) : (
                  <span>{row.kind === "none" ? noneLabel : found.status === "ready" && found.more === "loading" ? t("state.loading") : t("list.more")}</span>
                )}
              </li>
            ))}
          </ul>
          {found.status === "loading" ? (
            <p className="state" role="status">
              {t("state.loading")}
            </p>
          ) : null}
          {found.status === "ready" && found.options.length === 0 ? (
            <p className="state" role="status">
              {t("customers.noMatch")}
            </p>
          ) : null}
          {failed ? (
            <p className="field__error" role="alert">
              {errorText(failed, t)}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
