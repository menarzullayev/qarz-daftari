import { useEffect, useState, type FormEvent } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { AddressInput, ApiError } from "../api";
import { useSubmit } from "../hooks";
import { Link, navigate } from "../router";
import { NotFoundScreen } from "../screens";
import {
  AddressFields,
  addressInput,
  addressProblem,
  addressRefusal,
  draftOf,
  NO_ADDRESS,
  type AddressDraft,
} from "./AddressFields";
import { useMay, useWorkspace } from "./context";
import { errorText, FieldError } from "./parts";

export const MAX_NAME_LENGTH = 80;
const MAX_PHONE_LENGTH = 40;

/** A display name as the server stores it: trimmed, inner white space collapsed. */
export function cleanName(raw: string): string {
  return raw.split(/\s+/u).filter(Boolean).join(" ");
}

export function nameProblem(name: string, t: Translate): string | null {
  if (name === "") {
    return t("customer.name.required");
  }
  // The server counts characters, not UTF-16 units.
  if ([...name].length > MAX_NAME_LENGTH) {
    return t("customer.name.tooLong", { max: MAX_NAME_LENGTH });
  }
  return null;
}

/** Which field a refusal belongs to, so the message can sit next to it. */
export function customerFieldErrors(error: ApiError | null, t: Translate): { name: string | null; phone: string | null } {
  if (error?.code !== "VALIDATION") {
    return { name: null, phone: null };
  }
  return {
    name: "display_name" in error.fields ? t("customer.name.invalid", { max: MAX_NAME_LENGTH }) : null,
    phone: "phone" in error.fields ? t("customer.phone.invalid") : null,
  };
}

function NewCustomer() {
  const { api, features } = useWorkspace();
  const { t } = useI18n();
  const withAddress = features?.address === true;
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  // `filled` while the draft is still the place the shop used last, untouched by the person.
  const [address, setAddress] = useState<{ draft: AddressDraft; filled: boolean; touched: boolean }>({
    draft: NO_ADDRESS,
    filled: false,
    touched: false,
  });
  const [addressError, setAddressError] = useState<string | null>(null);
  const { state, submit } = useSubmit(
    (payload: { displayName: string; phone: string | null; address?: AddressInput | null }, key) =>
      api.createCustomer(payload, key),
  );

  // A village shop's customers live in one place: the form starts from the region, district and mahalla
  // the shop used last. It is only a start, never the street, and never over what the person chose.
  useEffect(() => {
    if (!withAddress) {
      return;
    }
    const controller = new AbortController();
    api.lastAddress(controller.signal).then(
      (last) => {
        if (last !== null && !controller.signal.aborted) {
          setAddress((current) => (current.touched ? current : { draft: draftOf(last), filled: true, touched: false }));
        }
      },
      () => undefined, // without it the form is simply empty
    );
    return () => controller.abort();
  }, [api, withAddress]);

  const created = state.status === "done" ? state.result.id : null;
  useEffect(() => {
    if (created !== null) {
      navigate(`/customers/${created}`);
    }
  }, [created]);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const displayName = cleanName(name);
    const problem = nameProblem(displayName, t);
    setNameError(problem);
    const placeProblem = withAddress ? addressProblem(address.draft, t) : null;
    setAddressError(placeProblem);
    if (problem === null && placeProblem === null) {
      submit({
        displayName,
        phone: phone.trim() === "" ? null : phone.trim(),
        // Named only while addresses are on and one was chosen: otherwise the request is what it always was.
        ...(withAddress ? { address: addressInput(address.draft) } : {}),
      });
    }
  };

  const failure = state.status === "error" ? state.error : null;
  const refused = customerFieldErrors(failure, t);
  const shownNameError = nameError ?? refused.name;
  const pending = state.status === "pending" || state.status === "done";

  return (
    <form className="form" onSubmit={onSubmit} noValidate>
      {failure ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="customer-name">{t("customer.name")}</label>
        <input
          id="customer-name"
          className="input"
          value={name}
          autoComplete="off"
          aria-invalid={shownNameError !== null}
          aria-describedby="customer-name-error"
          onChange={(event) => {
            setName(event.target.value);
            setNameError(null);
          }}
        />
        <FieldError id="customer-name-error" message={shownNameError} />
      </div>
      <div className="field">
        <label htmlFor="customer-phone">{t("customer.phone.optional")}</label>
        <input
          id="customer-phone"
          className="input"
          type="tel"
          inputMode="tel"
          value={phone}
          maxLength={MAX_PHONE_LENGTH}
          autoComplete="off"
          aria-invalid={refused.phone !== null}
          aria-describedby="customer-phone-error"
          onChange={(event) => setPhone(event.target.value)}
        />
        <FieldError id="customer-phone-error" message={refused.phone} />
      </div>
      {withAddress ? (
        <AddressFields
          id="customer-address"
          value={address.draft}
          note={address.filled ? t("address.last") : null}
          error={addressError ?? addressRefusal(failure, t)}
          onChange={(draft) => {
            setAddress({ draft, filled: false, touched: true });
            setAddressError(null);
          }}
        />
      ) : null}
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={pending}>
          {pending ? t("state.saving") : t("customers.add")}
        </button>
        <Link to="/customers" className="button">
          {t("action.cancel")}
        </Link>
      </p>
    </form>
  );
}

/**
 * Adds a customer to the book and opens their page, where a sale or a payment can be recorded. A member
 * who may not add customers is shown no form and sends nothing.
 */
export function NewCustomerScreen() {
  const can = useMay();
  return can("customers.create") ? <NewCustomer /> : <NotFoundScreen />;
}
