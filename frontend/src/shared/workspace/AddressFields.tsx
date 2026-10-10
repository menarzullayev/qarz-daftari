import { useEffect, useState } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { AddressInput, ApiError, CustomerAddress, LastAddress, MahallaOption, Place, PlaceList } from "../api";
import { useLoad } from "../hooks";
import { useWorkspace } from "./context";
import { Failure, FieldError, Loading } from "./parts";

/** How long typing must pause before the territory reference is searched again. */
export const ADDRESS_SEARCH_DELAY_MS = 300;
export const MAX_STREET_LENGTH = 120;
const MAX_QUERY_LENGTH = 80;
const LIST = 20;
const NOTHING: PlaceList<never> = { items: [], more: false };

/**
 * An address while it is being chosen. Everything is optional. The street is either picked from the
 * reference (`street`) or typed (`streetText`); picking one empties the typed text.
 */
export type AddressDraft = {
  region: Place | null;
  district: Place | null;
  mahalla: Place | null;
  street: Place | null;
  streetText: string;
};

export const NO_ADDRESS: AddressDraft = { region: null, district: null, mahalla: null, street: null, streetText: "" };

/** The draft a form starts from: a customer's address, or the place the shop used last (no street). */
export function draftOf(address: CustomerAddress | LastAddress | null | undefined): AddressDraft {
  if (address === null || address === undefined) {
    return NO_ADDRESS;
  }
  return {
    region: address.region,
    district: address.district,
    mahalla: address.mahalla,
    street: "street" in address ? address.street : null,
    streetText: "streetText" in address ? (address.streetText ?? "") : "",
  };
}

/** What is sent for the draft; null when it names no region, which is "no address". */
export function addressInput(draft: AddressDraft): AddressInput | null {
  if (draft.region === null) {
    return null;
  }
  const typed = draft.streetText.split(/\s+/u).filter(Boolean).join(" ");
  return {
    regionId: draft.region.id,
    districtId: draft.district?.id ?? null,
    mahallaId: draft.mahalla?.id ?? null,
    streetId: draft.street?.id ?? null,
    streetText: draft.street !== null || typed === "" ? null : typed,
  };
}

export function sameAddress(one: AddressInput | null, other: AddressInput | null): boolean {
  if (one === null || other === null) {
    return one === other;
  }
  return (
    one.regionId === other.regionId &&
    one.districtId === other.districtId &&
    one.mahallaId === other.mahallaId &&
    one.streetId === other.streetId &&
    one.streetText === other.streetText
  );
}

/** A typed street cannot be kept without a region: an address starts at one. */
export function addressProblem(draft: AddressDraft, t: Translate): string | null {
  return draft.region === null && draft.streetText.trim() !== "" ? t("address.regionRequired") : null;
}

/** The server's refusal of the address, as one message under the fields. */
export function addressRefusal(error: ApiError | null, t: Translate): string | null {
  if (error?.code !== "VALIDATION") {
    return null;
  }
  return Object.keys(error.fields).some((field) => field === "address" || field.startsWith("address."))
    ? t("address.invalid")
    : null;
}

/** The address in one line, widest place first. */
export function addressLine(address: CustomerAddress): string {
  return [address.region.name, address.district?.name, address.mahalla?.name, address.street?.name, address.streetText]
    .filter((part): part is string => typeof part === "string" && part !== "")
    .join(", ");
}

/** The typed text once typing has paused. */
function usePaused(text: string): string {
  const [paused, setPaused] = useState(text.trim());
  useEffect(() => {
    const timer = window.setTimeout(() => setPaused(text.trim()), ADDRESS_SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);
  return paused;
}

/** The options of a short list, with the chosen place among them even when the list no longer offers it. */
function withChosen(items: readonly Place[], chosen: Place | null): readonly Place[] {
  return chosen === null || items.some((item) => item.id === chosen.id) ? items : [chosen, ...items];
}

function Chosen({ id, label, place, onChange }: { id: string; label: string; place: Place; onChange: () => void }) {
  const { t } = useI18n();
  return (
    <div className="field">
      <span className="field__label" id={id}>
        {label}
      </span>
      <p className="address__chosen" aria-labelledby={id}>
        <span className="row__name">{place.name}</span>
        <button type="button" className="button" onClick={onChange}>
          {t("address.change")}
        </button>
      </p>
    </div>
  );
}

function MahallaSearch({
  id,
  region,
  district,
  onPick,
}: {
  id: string;
  region: Place | null;
  district: Place | null;
  onPick: (mahalla: MahallaOption) => void;
}) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const [text, setText] = useState("");
  const query = usePaused(text);
  const regionId = region?.id ?? null;
  const districtId = district?.id ?? null;
  const { state, reload } = useLoad<PlaceList<MahallaOption>>(
    (signal) =>
      regionId === null
        ? Promise.resolve(NOTHING)
        : api.searchMahallas({ regionId, districtId, q: query, limit: LIST }, signal),
    [api, regionId, districtId, query],
  );
  const found = state.status === "ready" ? state.data : NOTHING;
  // Two mahallas of one name are told apart by the group the reference lists them in, where it has one.
  const repeated = new Set(
    found.items.filter((item) => found.items.some((other) => other.id !== item.id && other.name === item.name)).map((item) => item.id),
  );

  return (
    <div className="field">
      <label htmlFor={id}>{t("address.mahalla")}</label>
      <input
        id={id}
        type="search"
        className="input"
        value={text}
        maxLength={MAX_QUERY_LENGTH}
        autoComplete="off"
        disabled={regionId === null}
        placeholder={t("address.mahalla.search")}
        aria-describedby={`${id}-hint`}
        onChange={(event) => setText(event.target.value)}
      />
      {regionId === null ? (
        <p className="field__hint" id={`${id}-hint`}>
          {t("address.mahalla.needRegion")}
        </p>
      ) : null}
      {regionId !== null && state.status === "loading" ? <Loading /> : null}
      {state.status === "error" ? <Failure error={state.error} onRetry={reload} /> : null}
      {regionId !== null && state.status === "ready" && found.items.length === 0 ? (
        <p className="field__hint">{t("address.mahalla.none")}</p>
      ) : null}
      {found.items.length > 0 ? (
        <ul className="picks picks--address" aria-label={t("address.mahalla.list")}>
          {found.items.map((item) => (
            <li key={item.id}>
              <button type="button" className="pick pick--place" onClick={() => onPick(item)}>
                <span className="row__name">{item.name}</span>
                {repeated.has(item.id) && item.group !== null ? (
                  <span className="row__meta">{t("address.mahalla.group", { group: item.group })}</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {found.more ? <p className="field__hint">{t("address.more")}</p> : null}
    </div>
  );
}

function StreetField({
  id,
  mahalla,
  text,
  onText,
  onPick,
}: {
  id: string;
  mahalla: Place | null;
  text: string;
  onText: (text: string) => void;
  onPick: (street: Place) => void;
}) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const query = usePaused(text);
  const mahallaId = mahalla?.id ?? null;
  // The reference has streets for a small part of the country: where it has none, nothing is listed
  // and what is typed is the street. A list that cannot be read is no reason to stop the typing.
  const { state } = useLoad<PlaceList<Place>>(
    (signal) =>
      mahallaId === null ? Promise.resolve(NOTHING) : api.searchStreets({ mahallaId, q: query, limit: LIST }, signal),
    [api, mahallaId, query],
  );
  const found = state.status === "ready" ? state.data : NOTHING;

  return (
    <div className="field">
      <label htmlFor={id}>{t("address.street")}</label>
      <input
        id={id}
        className="input"
        value={text}
        maxLength={MAX_STREET_LENGTH}
        autoComplete="off"
        aria-describedby={`${id}-hint`}
        onChange={(event) => onText(event.target.value)}
      />
      {found.items.length > 0 ? (
        <>
          <p className="field__hint" id={`${id}-hint`}>
            {t("address.street.hint")}
          </p>
          <ul className="picks picks--address" aria-label={t("address.street.list")}>
            {found.items.map((item) => (
              <li key={item.id}>
                <button type="button" className="pick pick--place" onClick={() => onPick(item)}>
                  <span className="row__name">{item.name}</span>
                </button>
              </li>
            ))}
          </ul>
          {found.more ? <p className="field__hint">{t("address.more")}</p> : null}
        </>
      ) : null}
    </div>
  );
}

/**
 * Where a customer lives: region, then district, then a mahalla found by typing, then a street picked
 * from the reference or typed. Each step is optional, and so is the whole. Where the reference does not
 * know which district a mahalla is in, the mahalla is offered under the region and no district is
 * filled in for it.
 */
export function AddressFields({
  value,
  onChange,
  id: idPrefix,
  note,
  error,
}: {
  value: AddressDraft;
  onChange: (next: AddressDraft) => void;
  id: string;
  /** A line under the heading, for a draft that was filled in for the person. */
  note?: string | null | undefined;
  error?: string | null | undefined;
}) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const regions = useLoad((signal) => api.listRegions(signal), [api]);
  const regionId = value.region?.id ?? null;
  const districts = useLoad<PlaceList<Place>>(
    (signal) => (regionId === null ? Promise.resolve(NOTHING) : api.listDistricts(regionId, signal)),
    [api, regionId],
  );
  const regionOptions = withChosen(regions.state.status === "ready" ? regions.state.data.items : [], value.region);
  const districtOptions = withChosen(districts.state.status === "ready" ? districts.state.data.items : [], value.district);
  const touched = value.region !== null || value.streetText !== "";

  return (
    <fieldset className="field address" aria-describedby={`${idPrefix}-error`}>
      <legend>{t("address.title")}</legend>
      {note ? <p className="field__hint">{note}</p> : null}
      {regions.state.status === "error" ? <Failure error={regions.state.error} onRetry={regions.reload} /> : null}

      <div className="field">
        <label htmlFor={`${idPrefix}-region`}>{t("address.region")}</label>
        <select
          id={`${idPrefix}-region`}
          className="input"
          value={regionId ?? ""}
          onChange={(event) => {
            const region = regionOptions.find((option) => option.id === event.target.value) ?? null;
            // Another region: nothing below it still holds. What was typed for the street stays typed.
            onChange({ ...NO_ADDRESS, region, streetText: value.street === null ? value.streetText : "" });
          }}
        >
          <option value="">{t("address.none")}</option>
          {regionOptions.map((option) => (
            <option key={option.id} value={option.id}>
              {option.name}
            </option>
          ))}
        </select>
      </div>

      {regionId === null ? null : (
        <div className="field">
          <label htmlFor={`${idPrefix}-district`}>{t("address.district")}</label>
          <select
            id={`${idPrefix}-district`}
            className="input"
            value={value.district?.id ?? ""}
            onChange={(event) => {
              const district = districtOptions.find((option) => option.id === event.target.value) ?? null;
              onChange({
                ...value,
                district,
                mahalla: null,
                street: null,
              });
            }}
          >
            <option value="">{t("address.none")}</option>
            {districtOptions.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
        </div>
      )}

      {value.mahalla !== null ? (
        <Chosen
          id={`${idPrefix}-mahalla`}
          label={t("address.mahalla")}
          place={value.mahalla}
          onChange={() => onChange({ ...value, mahalla: null, street: null })}
        />
      ) : (
        <MahallaSearch
          id={`${idPrefix}-mahalla`}
          region={value.region}
          district={value.district}
          onPick={(mahalla) => {
            // The district comes with the mahalla only where the reference knows it; otherwise the
            // district stays as the person left it, chosen or not.
            const known = mahalla.districtId === null ? null : districtOptions.find((option) => option.id === mahalla.districtId);
            onChange({
              ...value,
              district: known ?? value.district,
              mahalla: { id: mahalla.id, name: mahalla.name },
              street: null,
            });
          }}
        />
      )}

      {value.street !== null ? (
        <Chosen
          id={`${idPrefix}-street`}
          label={t("address.street")}
          place={value.street}
          onChange={() => onChange({ ...value, street: null })}
        />
      ) : (
        <StreetField
          id={`${idPrefix}-street`}
          mahalla={value.mahalla}
          text={value.streetText}
          onText={(streetText) => onChange({ ...value, streetText })}
          onPick={(street) => onChange({ ...value, street, streetText: "" })}
        />
      )}

      <FieldError id={`${idPrefix}-error`} message={error ?? null} />
      {touched ? (
        <p className="actions">
          <button type="button" className="button" onClick={() => onChange(NO_ADDRESS)}>
            {t("address.clear")}
          </button>
        </p>
      ) : null}
    </fieldset>
  );
}
