import { type FormEvent, useMemo, useState } from "react";

import { useI18n } from "../../i18n/I18nProvider";
import { useLoad, useSubmit } from "../hooks";
import { useWorkspace } from "../workspace/context";
import { errorText, FieldError } from "../workspace/parts";
import "./messages";
import { type ShareContact, sharesOf } from "./shareApi";

const MAX_PHONE_INPUT = 40;

function ContactForm({ contact }: { contact: ShareContact }) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const shares = useMemo(() => sharesOf(api), [api]);
  const [phone, setPhone] = useState(contact.phone ?? "");
  // The field then shows the number as the server keeps it: "90 123 45 67" becomes "+998901234567".
  const { state, submit, reset } = useSubmit((value: string | null, key) =>
    shares.setContact(value, key).then((stored) => setPhone(stored.phone ?? "")),
  );

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const clean = phone.trim();
    submit(clean === "" ? null : clean);
  };

  const failure = state.status === "error" ? state.error : null;
  // The server is the one that knows what a phone number is; it names the field it refused.
  const invalid = failure !== null && failure.code === "VALIDATION" && "phone" in failure.fields;
  return (
    <form className="form" onSubmit={onSubmit} noValidate aria-label={t("share.contact.title")}>
      {failure && !invalid ? (
        <p className="notice notice--error" role="alert">
          {errorText(failure, t)}
        </p>
      ) : null}
      {state.status === "done" ? (
        <p className="notice notice--done" role="status">
          {t("share.contact.saved")}
        </p>
      ) : null}
      <div className="field">
        <label htmlFor="share-phone">{t("share.contact.label")}</label>
        <input
          id="share-phone"
          className="input"
          type="tel"
          inputMode="tel"
          value={phone}
          maxLength={MAX_PHONE_INPUT}
          autoComplete="off"
          aria-invalid={invalid}
          aria-describedby="share-phone-error"
          onChange={(event) => {
            setPhone(event.target.value);
            if (state.status !== "pending") {
              reset();
            }
          }}
        />
        <FieldError id="share-phone-error" message={invalid ? t("share.contact.invalid") : null} />
      </div>
      <p className="actions">
        <button type="submit" className="button button--primary" disabled={state.status === "pending"}>
          {state.status === "pending" ? t("state.saving") : t("action.save")}
        </button>
      </p>
    </form>
  );
}

/**
 * The phone a shop shows on the page behind a customer's read-only link, under the shop's settings: the
 * owner changes it, a manager reads it. Behind the same platform switch as the links themselves: while
 * the server says it is off, and until it has answered, this renders nothing.
 */
export default function ShareContactSection({ editable }: { editable: boolean }) {
  const { api } = useWorkspace();
  const { t } = useI18n();
  const shares = useMemo(() => sharesOf(api), [api]);
  const { state } = useLoad((signal) => shares.readContact(signal), [shares]);

  // Nothing is shown until the server has answered in this feature's own words: not while it is asked,
  // not when it says there is no such route (the switch is off), and not for any other failure. The
  // section is an addition to a screen that works without it.
  if (state.status !== "ready") {
    return null;
  }
  return (
    <section aria-labelledby="share-contact-title">
      <h2 id="share-contact-title">{t("share.contact.title")}</h2>
      <p className="hint">{t("share.contact.explain")}</p>
      {editable ? (
        <ContactForm contact={state.data} />
      ) : (
        <p>{state.data.phone ?? t("share.contact.none")}</p>
      )}
    </section>
  );
}
