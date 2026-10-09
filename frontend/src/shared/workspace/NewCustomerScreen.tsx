import { useEffect, useState, type FormEvent } from "react";

import { useI18n, type Translate } from "../../i18n/I18nProvider";
import type { ApiError } from "../api";
import { useSubmit } from "../hooks";
import { Link, navigate } from "../router";
import { NotFoundScreen } from "../screens";
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
  const { api } = useWorkspace();
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const { state, submit } = useSubmit((payload: { displayName: string; phone: string | null }, key) =>
    api.createCustomer(payload, key),
  );

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
    if (problem === null) {
      submit({ displayName, phone: phone.trim() === "" ? null : phone.trim() });
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
