import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { enPanel } from "../i18n/panel/en";
import { kaaPanel } from "../i18n/panel/kaa";
import { ruPanel } from "../i18n/panel/ru";
import { tgPanel } from "../i18n/panel/tg";
import { uzPanel } from "../i18n/panel/uz";
import type { Message } from "../i18n/types";
import { ACTION_GROUPS } from "./ActivityScreen";

/**
 * The activity log names every step of the network between shops. The steps are the server's: its own
 * test (`backend/tests/test_network_actions.py`) holds the list in this file to the code that writes
 * them, and this test holds the panel's names to the same list, so a step added on the server without a
 * name here fails one of the two. A step without a name would be shown as the server's raw word
 * (`actionText`), which is what this guards against.
 */
const listed = JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../backend/tests/data/network_actions.json"), "utf8")) as {
  actions: string[];
  subjects: string[];
};

const PANELS: Readonly<Record<string, Readonly<Record<string, Message | undefined>>>> = {
  uz: uzPanel,
  ru: ruPanel,
  tg: tgPanel,
  kaa: kaaPanel,
  en: enPanel,
};
const LANGUAGES = Object.keys(PANELS);

function nameIn(language: string, key: string): Message | undefined {
  return PANELS[language]?.[key];
}

describe("the network's steps in the activity log", () => {
  it("reads the server's list", () => {
    expect(listed.actions.length).toBeGreaterThanOrEqual(20);
    expect(listed.actions.every((action) => action.startsWith("network."))).toBe(true);
    expect(listed.subjects).toEqual(expect.arrayContaining(["network_link", "network_order", "network_note", "network_payment"]));
  });

  it.each(listed.actions)("%s has a name in every language, and it is not the server's word", (action) => {
    for (const language of LANGUAGES) {
      const name = nameIn(language, `activity.action.${action}`);
      expect(typeof name, `${language}: ${action}`).toBe("string");
      expect(name).not.toBe(action);
      expect(String(name).length, `${language}: ${action}`).toBeGreaterThan(5);
    }
  });

  it.each(listed.subjects)("%s, what a step is about, has a name in every language", (subject) => {
    for (const language of LANGUAGES) {
      const name = nameIn(language, `activity.subject.${subject}`);
      expect(typeof name, `${language}: ${subject}`).toBe("string");
      expect(name).not.toBe(subject);
    }
  });

  it("names no step the server does not write: a name left behind is noticed too", () => {
    const named = Object.keys(uzPanel)
      .filter((key) => key.startsWith("activity.action.network."))
      .map((key) => key.replace("activity.action.", ""));
    expect(named.sort()).toEqual([...listed.actions].sort());
  });

  it("can be filtered to the network's steps, by a group that has a name in every language", () => {
    expect(ACTION_GROUPS).toContain("network");
    // The filter is the start of the action's name, as the API matches it.
    expect(listed.actions.every((action) => action.startsWith("network"))).toBe(true);
    for (const language of LANGUAGES) {
      expect(typeof nameIn(language, "activity.group.network"), language).toBe("string");
    }
  });

  it("would fail for a step without a name", () => {
    expect(nameIn("uz", "activity.action.network.note_returned")).toBeUndefined();
    expect(nameIn("tg", "activity.action.network.note_returned")).toBeUndefined();
  });
});
