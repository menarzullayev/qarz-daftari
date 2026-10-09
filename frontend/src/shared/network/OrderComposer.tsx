import { useRef, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { ApiError } from "../api";
import { useLoad, useSubmit } from "../hooks";
import { Link, navigate } from "../router";
import { NotFoundScreen } from "../screens";
import { ItemFinder } from "../stock/ItemFinder";
import { readQty, tidy } from "../stock/quantity";
import type { ItemFilter } from "../stock/stockApi";
import { useMay } from "../workspace/context";
import { Confirm, errorText, Failure, FieldError, Loading } from "../workspace/parts";
import type { Draft, DraftInput, NetLink } from "./networkApi";
import { MAX_LINES, MAX_NAME, MAX_NOTE, partnerText, UNITS, useNetwork, useUnitLabel } from "./parts";

/** A line may name any item of the buyer's catalogue, counted in the stock or not. */
const ANY_ITEM: ItemFilter = "all";

type Row = { key: number; name: string; qty: string; unit: string; itemId: string | null };
type Problem = "link" | "empty" | "lines";
type Job = { draftId: string | null; input: DraftInput; send: boolean };

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Writes an order for a supplier this shop is linked with as the buyer: what is wanted, line by line.
 * A line may name one of the buyer's own catalogue items, which only the buyer sees; the supplier is
 * sent the name, the quantity and the unit. Saved as a draft until it is sent.
 */
function Composer({ links, draft }: { links: readonly NetLink[]; draft: Draft | null }) {
  const { t } = useI18n();
  const can = useMay();
  const network = useNetwork();
  const unitLabel = useUnitLabel();
  const [linkId, setLinkId] = useState(draft?.linkId ?? (links.length === 1 ? (links[0]?.id ?? "") : ""));
  const [wanted, setWanted] = useState(draft?.wantedDate ?? "");
  const [note, setNote] = useState(draft?.note ?? "");
  const [rows, setRows] = useState<Row[]>(() =>
    draft && draft.lines.length > 0
      ? draft.lines.map((line, index) => ({ key: index, name: line.name, qty: line.qty.replace(".", ","), unit: line.unit, itemId: line.itemId }))
      : [{ key: 0, name: "", qty: "", unit: UNITS[0], itemId: null }],
  );
  const [nextKey, setNextKey] = useState(rows.length);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [saved, setSaved] = useState(false);
  const [finding, setFinding] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  // The draft a first attempt made when its sending then failed. The same attempt again repeats its two
  // keys, and the server answers both from what it stored. A CHANGED attempt has a new key: it writes
  // over that draft instead of making a second one beside it, unless the draft is gone (it was sent
  // after all, and the answer was lost), in which case it is a new order like any other.
  const made = useRef<{ key: string; id: string } | null>(null);
  const write = async (job: Job, key: string): Promise<Draft> => {
    if (job.draftId !== null) {
      return network.updateDraft(job.draftId, job.input, key);
    }
    const earlier = made.current;
    if (earlier !== null && earlier.key !== key) {
      try {
        return await network.updateDraft(earlier.id, job.input, key);
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) {
          throw error;
        }
        // No such draft any more. Each write has a key of its own, so the new draft's is a derived one.
        const again = await network.createDraft(job.input, `${key}-new`);
        made.current = { key, id: again.id };
        return again;
      }
    }
    const created = await network.createDraft(job.input, key);
    made.current = { key, id: created.id };
    return created;
  };
  const save = useSubmit(async (job: Job, key) => {
    const kept = await write(job, key);
    if (job.send) {
      // A second write of the same action: its key is made from the first, so a retry repeats both.
      const order = await network.sendDraft(kept.id, `${key}-send`);
      navigate(`/network/orders/${order.id}`);
      return;
    }
    setSaved(true);
    if (job.draftId === null) {
      navigate(`/network/drafts/${kept.id}`);
    }
  });
  const remove = useSubmit((draftId: string, key) => network.deleteDraft(draftId, key).then(() => navigate("/network/orders/out")));
  const pending = save.state.status === "pending" || remove.state.status === "pending";

  const change = (key: number, patch: Partial<Row>) => {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
    setProblem(null);
    setSaved(false);
  };
  const input = (): DraftInput | Problem => {
    if (linkId === "") {
      return "link";
    }
    const filled = rows.filter((row) => row.name.trim() !== "" || row.qty.trim() !== "");
    if (filled.length === 0) {
      return "empty";
    }
    const lines: DraftInput["lines"][number][] = [];
    for (const row of filled) {
      const name = tidy(row.name);
      const qty = readQty(row.qty);
      if (name === null || [...name].length > MAX_NAME || !qty.ok) {
        return "lines";
      }
      lines.push({ name, unit: row.unit, qty: qty.api, itemId: row.itemId });
    }
    const cleanNote = tidy(note);
    return {
      linkId,
      note: cleanNote === null ? null : cleanNote.slice(0, MAX_NOTE),
      wantedDate: DAY.test(wanted) ? wanted : null,
      lines,
    };
  };
  const submit = (send: boolean) => {
    const read = input();
    if (typeof read === "string") {
      setProblem(read);
      return;
    }
    save.submit({ draftId: draft?.id ?? null, input: read, send });
  };
  const rowProblem = (row: Row): { name: boolean; qty: boolean } => {
    if (problem !== "lines" || (row.name.trim() === "" && row.qty.trim() === "")) {
      return { name: false, qty: false };
    }
    const name = tidy(row.name);
    return { name: name === null || [...name].length > MAX_NAME, qty: !readQty(row.qty).ok };
  };

  if (links.length === 0) {
    return (
      <>
        <p className="notice">{t("net.compose.noLink")}</p>
        <p className="actions">
          <Link to="/network" className="button">
            {t("nav.network")}
          </Link>
        </p>
      </>
    );
  }

  return (
    <div className="form">
      <h2 className="subject">{t(draft ? "net.compose.title.draft" : "net.compose.title.new")}</h2>
      {save.state.status === "error" ? (
        <p className="notice notice--error" role="alert">
          {errorText(save.state.error, t)}
        </p>
      ) : null}
      {saved && save.state.status !== "error" ? (
        <p className="notice notice--done" role="status">
          {t("net.compose.saved")}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="net-compose-link">{t("net.compose.supplier")}</label>
        <select
          id="net-compose-link"
          className="input"
          value={linkId}
          disabled={pending}
          aria-invalid={problem === "link"}
          aria-describedby="net-compose-link-error"
          onChange={(event) => {
            setLinkId(event.target.value);
            setProblem(null);
            setSaved(false);
          }}
        >
          <option value="">{t("net.compose.supplier.choose")}</option>
          {links.map((link) => (
            <option key={link.id} value={link.id}>
              {partnerText(link.partner, t)}
            </option>
          ))}
        </select>
        <FieldError id="net-compose-link-error" message={problem === "link" ? t("net.compose.problem.link") : null} />
      </div>

      <h3 className="section-label">{t("net.order.lines")}</h3>
      <ul className="rows" aria-label={t("net.order.lines")}>
        {rows.map((row, index) => {
          const id = `net-compose-${row.key}`;
          const wrong = rowProblem(row);
          return (
            <li key={row.key} className="row">
              <div className="field">
                <label htmlFor={`${id}-name`}>{t("net.compose.name", { number: index + 1 })}</label>
                <input
                  id={`${id}-name`}
                  className="input"
                  value={row.name}
                  maxLength={200}
                  autoComplete="off"
                  disabled={pending}
                  aria-invalid={wrong.name}
                  aria-describedby={`${id}-name-error`}
                  onChange={(event) => change(row.key, { name: event.target.value })}
                />
                <FieldError id={`${id}-name-error`} message={wrong.name ? t("net.compose.problem.name", { max: MAX_NAME }) : null} />
              </div>
              <div className="net-line">
                <div className="field">
                  <label htmlFor={`${id}-qty`}>{t("net.col.qty")}</label>
                  <input
                    id={`${id}-qty`}
                    className="input"
                    inputMode="decimal"
                    autoComplete="off"
                    value={row.qty}
                    disabled={pending}
                    aria-invalid={wrong.qty}
                    aria-describedby={`${id}-qty-error`}
                    onChange={(event) => change(row.key, { qty: event.target.value })}
                  />
                  <FieldError id={`${id}-qty-error`} message={wrong.qty ? t("net.qty.positive") : null} />
                </div>
                <div className="field">
                  <label htmlFor={`${id}-unit`}>{t("net.col.unit")}</label>
                  <select
                    id={`${id}-unit`}
                    className="input"
                    value={row.unit}
                    // The unit of a line taken from the catalogue is the item's own.
                    disabled={pending || row.itemId !== null}
                    onChange={(event) => change(row.key, { unit: event.target.value })}
                  >
                    {(UNITS.some((unit) => unit === row.unit) ? UNITS : [row.unit, ...UNITS]).map((unit) => (
                      <option key={unit} value={unit}>
                        {unitLabel(unit)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              {row.itemId !== null ? (
                <p className="row__meta">{t("net.compose.ownItem")}</p>
              ) : null}
              <p className="actions">
                {row.itemId !== null ? (
                  <button type="button" className="button button--small" disabled={pending} onClick={() => change(row.key, { itemId: null })}>
                    {t("net.line.ownItem.clear")}
                  </button>
                ) : can("stock.view") && finding !== row.key ? (
                  <button type="button" className="button button--small" disabled={pending} onClick={() => setFinding(row.key)}>
                    {t("net.line.ownItem.choose")}
                  </button>
                ) : null}
                {rows.length > 1 ? (
                  <button
                    type="button"
                    className="button button--small"
                    disabled={pending}
                    onClick={() => {
                      setRows((current) => current.filter((other) => other.key !== row.key));
                      setProblem(null);
                      setSaved(false);
                    }}
                  >
                    {t("net.compose.removeLine")}
                  </button>
                ) : null}
              </p>
              {finding === row.key && row.itemId === null ? (
                <ItemFinder
                  id={`${id}-item`}
                  filter={ANY_ITEM}
                  disabled={pending}
                  onPick={(item) => {
                    change(row.key, { itemId: item.id, name: item.name, unit: item.unit });
                    setFinding(null);
                  }}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
      {problem === "empty" || problem === "lines" ? (
        <p className="field__error" role="alert">
          {t(problem === "empty" ? "net.compose.problem.empty" : "net.compose.problem.lines")}
        </p>
      ) : null}
      {rows.length < MAX_LINES ? (
        <p className="actions">
          <button
            type="button"
            className="button"
            disabled={pending}
            onClick={() => {
              setRows((current) => [...current, { key: nextKey, name: "", qty: "", unit: UNITS[0], itemId: null }]);
              setNextKey(nextKey + 1);
            }}
          >
            {t("net.compose.addLine")}
          </button>
        </p>
      ) : null}

      <div className="field">
        <label htmlFor="net-compose-wanted">{t("net.order.wanted")}</label>
        <input
          id="net-compose-wanted"
          className="input"
          type="date"
          value={wanted}
          disabled={pending}
          onChange={(event) => {
            setWanted(event.target.value);
            setSaved(false);
          }}
        />
      </div>
      <div className="field">
        <label htmlFor="net-compose-note">{t("net.compose.note")}</label>
        <input
          id="net-compose-note"
          className="input"
          value={note}
          maxLength={MAX_NOTE}
          autoComplete="off"
          disabled={pending}
          onChange={(event) => {
            setNote(event.target.value);
            setSaved(false);
          }}
        />
      </div>

      {deleting && draft ? (
        <Confirm
          question={t("net.compose.delete.question")}
          yes={t("net.compose.delete")}
          no={t("action.cancel")}
          pending={remove.state.status === "pending"}
          error={remove.state.status === "error" ? remove.state.error : null}
          onYes={() => remove.submit(draft.id)}
          onNo={() => {
            setDeleting(false);
            remove.reset();
          }}
        />
      ) : (
        <p className="actions">
          <button type="button" className="button button--primary" disabled={pending} onClick={() => submit(true)}>
            {pending ? t("state.saving") : t("net.compose.send")}
          </button>
          <button type="button" className="button" disabled={pending} onClick={() => submit(false)}>
            {t("net.compose.save")}
          </button>
          {draft ? (
            <button type="button" className="button" disabled={pending} onClick={() => setDeleting(true)}>
              {t("net.compose.delete")}
            </button>
          ) : null}
          <Link to="/network/orders/out" className="button">
            {t("action.back")}
          </Link>
        </p>
      )}
    </div>
  );
}

/** A new order, or a draft opened again. Offered to a member who may order; the server checks it too. */
export function ComposeScreen({ draftId }: { draftId: string | null }) {
  const can = useMay();
  const network = useNetwork();
  const { state, reload } = useLoad(
    async (signal) => {
      const [overview, draft] = await Promise.all([network.overview(signal), draftId === null ? null : network.draft(draftId, signal)]);
      return { links: overview.links.filter((link) => link.role === "buyer" && link.state === "active"), draft };
    },
    [network, draftId],
  );
  if (!can("network.order")) {
    return <NotFoundScreen />;
  }
  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return state.error.status === 404 ? <NotFoundScreen /> : <Failure error={state.error} onRetry={reload} />;
  }
  return <Composer key={state.data.draft?.id ?? "new"} links={state.data.links} draft={state.data.draft} />;
}
