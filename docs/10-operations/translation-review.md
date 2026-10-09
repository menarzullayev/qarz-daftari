# Translation review

The product speaks six languages (technical specification, "Languages"). Two were written and read by
people who speak them. Four were added by expansion module E on 2026-10-09, and of those **Tajik and
Karakalpak were written by a model and have not been read by a native speaker.** They must be reviewed
before they are offered to shops as finished. Nothing in the interface says so: this page is where it is
tracked.

## Status

| Language | Tag | How the text was made | Reviewed by a native speaker | May be released |
|---|---|---|---|---|
| Uzbek, Latin | `uz` | Written by the team; the source of every other language | Yes | Yes |
| Russian | `ru` | Written by the team, key for key with Uzbek | Yes | Yes |
| Uzbek, Cyrillic | `uz-Cyrl` | Generated from `uz` by rule; no text is typed | **Rules not reviewed** | After a reader of Cyrillic Uzbek has gone through the list below |
| English | `en` | Written by a model from the Uzbek and Russian texts | Not by a native speaker; read by the team | Yes, with ordinary proofreading |
| Tajik | `tg` | Written by a model | **No** | **No** |
| Karakalpak (Latin, 2016 alphabet) | `kaa` | Written by a model | **No** | **No** |

"May be released" is about announcing a language to shops. The languages are in the build either way
(decision 11: no switch); until a language is reviewed, do not advertise it, and treat a complaint about
its wording as expected.

## What there is to review

About 2 700 texts for each of Tajik, Karakalpak and English: every module of the expansion, the network
between shops (module J) included since the final pass of 2026-10-09. **Completeness is enforced:** CI
runs `npm run i18n:missing -- --strict` in `frontend/` and `python scripts/i18n_missing.py --strict` in
`backend/`, and the tests ask the same (`frontend/src/i18n/newLanguages.test.ts`,
`backend/tests/test_languages.py`), so a text added in Uzbek without its Tajik, Karakalpak and English
fails the pull request that adds it. Complete is not reviewed: **the Tajik and Karakalpak texts, the
network's among them, were written by a model and no native speaker has read them.**

| Where a person sees it | Files (`xx` is `tg`, `kaa` or `en`) | Texts |
|---|---|---|
| Mini App and web panel, every screen | `frontend/src/i18n/xx.ts` | 605 |
| Web panel's own screens (staff, activity, deletion, permissions) | `frontend/src/i18n/panel/xx.ts` | 254 |
| Import, export, reports, subscription receipts, customer link, support access | `frontend/src/i18n/{imports,exports,reports,receipts,share,support}/xx.ts` | 127, 46, 66, 29, 26, 25 |
| Cash book; stock, its documents and suppliers | `frontend/src/i18n/{cash,stock}/xx.ts` | 100, 235 |
| **The network between shops: partners, orders, delivery notes, payments, reconciliation** | `frontend/src/i18n/network/xx.ts` | 290 |
| The network's steps in the activity log (group, 20 actions, 5 subjects) | `frontend/src/i18n/panel/xx.ts`, keys `activity.*.network*` | 26 (counted above) |
| Administrator's panel | `frontend/src/i18n/admin/xx.ts` | 299 |
| The customer's page behind a read-only link | `frontend/src/k/xx.ts` | 52 |
| The bot: replies, notifications, reminders, operations alerts | `backend/src/qarz/application/texts_xx.py`, `CHAT` | 298 |
| of which the network: 16 notices to the partner's staff (`net_link_*`, `net_order_*`, `net_note_*`, `net_payment_*`) and 5 texts it writes into a shop's own books (`net_sale_note`, `net_paid_note`, `net_withdrawn_reason`, `net_partner_name`, `net_partner_suffix`) | same | 21 |
| Export workbook | same file, `EXPORT` | 92 |
| of which the network's four sheets (`sheet_net_*`, `net_*`) | same | 13 |
| Refusals of the API | same file, `ERRORS` | 90 |
| of which the network's (`NETWORK_*`) | same | 9 |
| Names of permissions | same file, `PERMISSIONS` | 58 |
| of which the network's (`group.network`, `network.*`) | same | 6 |

SMS reminders are not in the list: they exist in Uzbek and Russian only, because each wording must be
registered with the provider (runbook 12). A customer of any other language gets the Uzbek SMS.

## Glossaries

One per language. Each fixes the word for debt and sale on credit, payment, customer, shop, due date,
overdue, reminder, subscription, receipt, the staff roles, cash book and stock, the network's terms
(partner, partnership, buyer, order, delivery note, confirmation, reconciliation), and the other terms
that recur. A reviewer reads the glossary first: changing a term there means changing it everywhere.

- [Tajik](glossaries/tg.md)
- [Karakalpak](glossaries/kaa.md)
- [English](glossaries/en.md)

Uzbek Cyrillic has no glossary: it has the words Uzbek has.

## Checklist for a reviewer

Do these in order, for one language at a time. Tick a line only when it is done for every file above.

### Tajik (`tg`)

- [ ] The glossary: each term is the word a shopkeeper would use. In particular `мағоза` or `дӯкон` for
      a shop, `ёдоварӣ` for a reminder, `сабт` for an entry, `пайванд` for a link, `нигоҳ доштан` for
      "save", `дида баромадан` for "review".
- [ ] The texts a customer reads without having chosen the product: the three reminder wordings
      (`r1_…`, `r2_…`, `r3_…`), the consent text (`consent_v2`, which has legal weight), the customer's
      page (`frontend/src/k/tg.ts`).
- [ ] Dates: `6 октябри 2026` (`{day} {month}и {year}`) reads right for every month.
- [ ] Counts without a counter word: `5 мизоҷ`, `3 рӯз дер шудааст`.
- [ ] The currency word `сӯм`.
- [ ] Buttons and navigation fit a phone: nothing is cut off in the bottom bar or in a button.
- [ ] The rest of the Mini App, screen by screen, in the product itself.
- [ ] The bot: `/yordam`, a sale, a payment, a reminder, a refusal.
- [ ] The web panel, the export workbook and the import template.
- [ ] The administrator's panel and the operations alerts (read by the team, lowest priority).

### Karakalpak (`kaa`)

- [ ] The alphabet: the 2016 Latin alphabet throughout (`á ǵ ı ń ó ú`, `sh`, `ch`, `w`); no apostrophe
      letters of the older alphabet, no Cyrillic.
- [ ] The glossary. In particular `qarıydar` or `klient` for a customer, `jazılıw` for a subscription
      (close to `jazıw`, "to write", and `jazba`, "entry"), `nesiye`, `narazılıq` for a dispute,
      `silteme` for a link, `túsindirme` for a note, `mirát` for an invitation.
- [ ] **The currency word.** The texts and the money module write `swm`. If shops in Karakalpakstan
      write `sum` or `som`, it is changed in three places (see "Where a correction goes").
- [ ] The texts a customer reads: the three reminder wordings, the consent text, the customer's page.
- [ ] Dates: `2026-jıl 6-oktyabr`, and the month names.
- [ ] Endings after a number, an abbreviation or a name that the product fills in: `{date} kúnine
      shekem`, `{size} MB qa shekem`, `2% ten kóbi`.
- [ ] Buttons and navigation fit a phone.
- [ ] The rest of the Mini App, the bot, the web panel, the export workbook and the import template.
- [ ] The administrator's panel and the operations alerts (lowest priority).
- [ ] Whether a person whose Telegram is in Kazakh should start in Karakalpak, as they do now
      (`kk` → `kaa`), or in Uzbek.

### English (`en`)

- [ ] Sentence case everywhere; one word for one thing (`shop`, `customer`, `reverse` for a ledger
      reversal, `decline` for a refused request).
- [ ] The currency word `soum` after the amount (`45 000 soum`), and the amount's own format, which is
      the product's and not English usage: thousands separated by spaces, `1 250.50 $`.
- [ ] Bot texts with a number in them: the bot has no plural forms, so these are worded to read with any
      number ("Days left: 3"). Say where one reads badly.
- [ ] What a person types to the bot is left in Uzbek on purpose (`Ali 20000 berdi`, `45 ming`): the
      parser reads Uzbek and Russian words only. Check that the sentence around it explains it.

### Uzbek Cyrillic (`uz-Cyrl`)

Nobody typed these texts, so there is no file to read. Read the product itself in "Ўзбекча", and the
list the two scripts print of Latin words left in the Cyrillic text.

- [ ] Words the rules get wrong. The usual kinds: a soft sign Latin does not write (`панель`, `роль`,
      `июнь`), `ц` written `s` or `ts` in Latin (`цирк`, `квитанция`, `лицей`), `ъ` after `ў`
      (`мўъжиза`), a borrowed word spelt the Russian way (`компьютер`, `район`).
- [ ] An Uzbek word written in capitals that was left in Latin (the rules take capitals for a code).
- [ ] Brands left in Latin with an Uzbek ending: `Telegramда`. Say if `Telegram'да` or `Телеграмда` is
      wanted instead; it is one decision for every brand.
- [ ] Whether the examples of what to type to the bot should stay as they come out (`Али 20000 берди`
      is understood by the bot; `45 минг` is not checked).

## How a reviewer reports corrections

A reviewer does not need the code. Any of these is enough; the first is the least work for everyone.

1. **A table.** For each correction: where it was seen (the screen, or the bot's message), the text as
   it is now, the text as it should be, and one line of why if it is not obvious. A spreadsheet, a
   document or a message to the founder will do.
2. **A change to the glossary.** "For *subscription* use X, not Y": one line changes every text that uses
   the term.
3. **A pull request**, for a reviewer who is at home in the repository: edit the files in the table
   above and nothing else. The tests refuse a renamed or dropped `{placeholder}`, an empty text and a
   changed bot command, so a correction cannot break a screen.

A screenshot helps when a text does not fit its place. A correction that changes meaning (the consent
text, a refusal, a reminder) is read by the founder before it is merged.

## Where a correction goes

| What is wrong | Where it is changed |
|---|---|
| A Tajik, Karakalpak or English text | The file it is in (table above). Keep `{placeholders}`, bot commands and the product's name as they are |
| A term, everywhere | The glossary first, then every text that uses it (search the old word in the language's files) |
| The so'm word of a language | `money.uzs` in `frontend/src/i18n/xx.ts`, `money` in `frontend/src/k/xx.ts`, `currency` in `texts_xx.py`, and `units` in `backend/src/qarz/domain/money.py` |
| A date pattern or a month name | `date.*` and `month.*` in `frontend/src/i18n/xx.ts`; `date` and `month.*` in `frontend/src/k/xx.ts` |
| An Uzbek Cyrillic word | The tables of the rules, in three places that the tests hold together: `backend/src/qarz/domain/uz_cyrillic.py`, `frontend/src/i18n/uzCyrillic.ts`, `backend/tests/data/uz_cyrillic_cases.json` (add the word as a case too). Then `npm run i18n:k` |
| A whole Uzbek Cyrillic message that must differ from the rule | `frontend/src/i18n/uzCyrlOverrides.ts`, or `UZ_CYRILLIC` in `chat_texts.py` / `export_texts.py` |
| An Uzbek or Russian text | Not this review: those are the product's source texts and are changed like any other |

## The final pass (2026-10-09), and what is left

Every module of the expansion is merged. The final pass translated the network between shops (module J,
the last one) into Tajik, Karakalpak and English, named its steps in the activity log, and made
completeness strict (above). What it did, for a reviewer to know where the newest text is:

1. `frontend/src/i18n/network/{tg,kaa,en}.ts` (290 texts each), `nav.network`, the administrator's switch
   `admin.setting.network_on`, the activity log's names; the server's notices, refusals, export sheets
   and the texts the network writes into a shop's books. The network's export sheets had their own
   two-language table: they are in `export_texts` now, like every other sheet.
2. The six permission names of the network that module J's author wrote for Tajik and Karakalpak were
   read against the glossaries. Karakalpak said "biykarlaw" (cancel) where the glossary keeps "ret etiw"
   for declining and rejecting, and "usınıs" (an ownership offer) for an invitation ("mirát"): corrected.
   Tajik's "анҷом" for ending a partnership became «қатъ кардан», the word the screens use.
3. Uzbek Cyrillic: the network's Uzbek text was run through the rules. No Latin word was left in it and
   no word came out wrong on a reading of the output (`npm run i18n:missing` lists the same 25 Latin
   words as before, all brands and codes), so the three override tables did not change.

**Tables of two languages in the code: closed after the expansion (2026-10-09).** The final pass left
four of them, unmeasured by the two scripts, so a reader of another language was shown the Uzbek word.
They are catalog entries now, in every language, and a reviewer reads them with the rest:

- names of stock units (`unit_*`) and write-off reasons (`reason_*`) in `EXPORT`: `GET …/stock/settings`
  sends each name in all six languages and the screens show the reader's;
- the cash book's default categories and the stock's own two (`cash_category_*` in `EXPORT`), and the
  notes a stock document writes into a supplier's or a customer's account and the cash book
  (`stock_purchase_note`, `stock_return_note` in `CHAT`). These are written in the shop's language at the
  moment they are made and are the shop's data from then on: a shop that already has its categories keeps
  their names (nothing renames them, and a shop may have renamed them itself); a shop that first uses the
  cash book after this gets them in its own language, Uzbek Cyrillic included;
- the states of a partnership, an order, a delivery note and a payment in the network's export sheets
  (`net_link_*`, `net_order_*`, `net_note_*`, `net_payment_*` in `EXPORT`), with the words the screens use.

`backend/tests/test_text_tables.py` holds each list of the domain (units, reasons, categories, states) to
a name in the catalog, and fails on any table in the source that names some languages and not all (a
dictionary with `uz` and `ru`, a class with such fields, a choice on `lang == "ru"`) outside the catalogs
and a short list of exceptions, each with its reason.

**Still English, on purpose:** the remark beside a refused field (`error.fields`, for example
`"between 1 and 100 lines"`). About 310 of them; they are identifiers for a client and no screen shows
one: every screen says in its own words what is wrong with the field it names, and the sentence a person
reads is `error.message`, which is in all six languages. Translating them would put 1 500 unread texts
before the reviewers.

**For the reviewers of Tajik and Karakalpak, the network first.** Its terms were chosen by a model with
no earlier text to lean on, and several have a plausible rival. Read the glossary rows added for it, then
these in particular: «шарик» / "sherik" for the other shop; «борхат» / "júk xatı" for a delivery note;
«қатъ кардан» / "tamamlaw" for ending a partnership; "satıp alıwshı" for a shop's role as buyer (kept
apart from "qarıydar", a customer); «расонидан» for delivering and «бақияи мувофиқашуда» for the
balance both sides confirmed; "jalǵanıw" for connecting by a code and "tarmaq" / «шабака» for the
network itself.

After corrections: `npm test` in `frontend/` and `pytest` in `backend/`; both scripts must still list
nothing missing, or CI fails.
