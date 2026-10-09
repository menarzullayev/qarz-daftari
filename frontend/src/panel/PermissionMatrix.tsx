import { type FormEvent, useState } from "react";

import { useI18n } from "../i18n/I18nProvider";
import { useLoad, useSubmit } from "../shared/hooks";
import { isRole } from "../shared/navigation";
import { Confirm, errorText, Failure, Loading } from "../shared/workspace/parts";
import type { CatalogueGroup, Label, Member, MemberPermissions } from "./backoffice";
import "./messages";
import { useOffice } from "./office";

type Changes = { granted: string[]; denied: string[] };

/** The member's changes for a set of ticked permissions: only what differs from the role is sent. */
export function changesFor(held: MemberPermissions, ticked: ReadonlySet<string>): Changes {
  const movable = held.permissions.filter((permission) => !permission.fixed);
  return {
    granted: movable.filter((permission) => !permission.byRole && ticked.has(permission.key)).map((p) => p.key),
    denied: movable.filter((permission) => permission.byRole && !ticked.has(permission.key)).map((p) => p.key),
  };
}

function tickedOf(held: MemberPermissions): Set<string> {
  return new Set(held.permissions.filter((permission) => permission.allowed).map((permission) => permission.key));
}

function Matrix({
  member,
  memberName,
  catalogue,
  loaded,
  onClose,
}: {
  member: Member;
  memberName: string;
  catalogue: readonly CatalogueGroup[];
  loaded: MemberPermissions;
  onClose: () => void;
}) {
  const { office } = useOffice();
  const { t, language } = useI18n();
  // What the server holds now, and what is ticked on the screen; they differ until "save".
  const [held, setHeld] = useState(loaded);
  const [ticked, setTicked] = useState(() => tickedOf(loaded));
  const [asking, setAsking] = useState(false);
  const [saved, setSaved] = useState(false);
  const save = useSubmit((changes: Changes, key) =>
    office.setMemberPermissions(member.id, changes, key).then((answer) => {
      setHeld(answer);
      setTicked(tickedOf(answer));
      setAsking(false);
      setSaved(true);
    }),
  );
  const pending = save.state.status === "pending";
  const byKey = new Map(held.permissions.map((permission) => [permission.key, permission]));
  const name = (label: Label) => (language === "ru" ? label.ru : label.uz);
  const changes = changesFor(held, ticked);
  const stored = changesFor(held, tickedOf(held));
  const dirty = JSON.stringify(changes) !== JSON.stringify(stored);
  const hasChanges = stored.granted.length + stored.denied.length > 0;
  const roleName = isRole(held.role) ? t(`role.${held.role}`) : held.role;

  const toggle = (key: string, on: boolean) => {
    const next = new Set(ticked);
    if (on) {
      next.add(key);
    } else {
      next.delete(key);
    }
    setTicked(next);
    setSaved(false);
    save.reset();
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    save.submit(changes);
  };

  /** In words, never by colour alone: where the answer for this box comes from. */
  const state = (key: string): string => {
    const permission = byKey.get(key);
    if (!permission || ticked.has(key) === permission.byRole) {
      return t("permissions.state.role");
    }
    return ticked.has(key) ? t("permissions.state.granted") : t("permissions.state.denied");
  };

  return (
    <form className="form matrix" onSubmit={onSubmit} noValidate>
      <p className="hint">{t("permissions.hint", { role: roleName })}</p>
      {catalogue.map((group) => {
        const movable = group.permissions.filter((permission) => !permission.fixed && byKey.has(permission.key));
        if (movable.length === 0) {
          return null;
        }
        return (
          <fieldset key={group.key} className="field matrix__group" disabled={pending}>
            <legend>{name(group.label)}</legend>
            {movable.map((permission) => (
              <label key={permission.key} className="choice">
                <input
                  type="checkbox"
                  checked={ticked.has(permission.key)}
                  onChange={(event) => toggle(permission.key, event.target.checked)}
                />
                <span>{name(permission.label)}</span>
                <span className="matrix__state">{state(permission.key)}</span>
              </label>
            ))}
          </fieldset>
        );
      })}
      <p className="hint">{t("permissions.fixed.hint")}</p>
      {save.state.status === "error" && !asking ? (
        <p className="notice notice--error" role="alert">
          {errorText(save.state.error, t)}
        </p>
      ) : null}
      {saved ? (
        <p className="notice notice--done" role="status">
          {t("permissions.saved")}
        </p>
      ) : null}
      {asking ? (
        <Confirm
          question={t("permissions.reset.confirm", { member: memberName, role: roleName })}
          yes={t("confirm.yes")}
          no={t("confirm.no")}
          pending={pending}
          error={save.state.status === "error" ? save.state.error : null}
          onYes={() => save.submit({ granted: [], denied: [] })}
          onNo={() => {
            setAsking(false);
            save.reset();
          }}
        />
      ) : (
        <p className="actions">
          <button type="submit" className="button button--primary" disabled={pending || !dirty}>
            {pending ? t("state.saving") : t("permissions.save")}
          </button>
          <button
            type="button"
            className="button"
            disabled={pending || (!hasChanges && !dirty)}
            onClick={() => {
              save.reset();
              setSaved(false);
              setAsking(true);
            }}
          >
            {t("permissions.reset")}
          </button>
          <button type="button" className="button" onClick={onClose} disabled={pending}>
            {t("permissions.close")}
          </button>
        </p>
      )}
    </form>
  );
}

/**
 * One member's permissions, one box each, grouped by area (expansion decision 10). The owner's alone:
 * the role gives the defaults, a tick or its absence changes one of them for this member, and "reset"
 * returns to the role. The server decides what each member may do; this screen only edits what it stores.
 */
export function PermissionMatrix({
  member,
  memberName,
  catalogue,
  onClose,
}: {
  member: Member;
  memberName: string;
  catalogue: readonly CatalogueGroup[];
  onClose: () => void;
}) {
  const { office } = useOffice();
  const { state, reload } = useLoad((signal) => office.memberPermissions(member.id, signal), [office, member.id]);
  if (state.status === "loading") {
    return <Loading />;
  }
  if (state.status === "error") {
    return <Failure error={state.error} onRetry={reload} />;
  }
  return <Matrix member={member} memberName={memberName} catalogue={catalogue} loaded={state.data} onClose={onClose} />;
}
