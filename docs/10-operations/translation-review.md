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

About 2 300 texts for each of Tajik, Karakalpak and English, the cash book and the stock included, all
complete on 2026-10-09 (`npm run i18n:missing` in `frontend/`, `python scripts/i18n_missing.py` in
`backend/`: nothing missing).

| Where a person sees it | Files (`xx` is `tg`, `kaa` or `en`) | Texts |
|---|---|---|
| Mini App and web panel, every screen | `frontend/src/i18n/xx.ts` | 598 |
| Web panel's own screens (staff, activity, deletion, permissions) | `frontend/src/i18n/panel/xx.ts` | 225 |
| Import, export, reports, subscription receipts, customer link, support access | `frontend/src/i18n/{imports,exports,reports,receipts,share,support}/xx.ts` | 127, 46, 66, 29, 26, 25 |
| Cash book; stock, its documents and suppliers | `frontend/src/i18n/{cash,stock}/xx.ts` | 100, 235 |
| Administrator's panel | `frontend/src/i18n/admin/xx.ts` | 298 |
| The customer's page behind a read-only link | `frontend/src/k/xx.ts` | 52 |
| The bot: replies, notifications, reminders, operations alerts | `backend/src/qarz/application/texts_xx.py`, `CHAT` | 277 |
| Export workbook | same file, `EXPORT` | 79 |
| Refusals of the API | same file, `ERRORS` | 81 |
| Names of permissions | same file, `PERMISSIONS` | 52 |

SMS reminders are not in the list: they exist in Uzbek and Russian only, because each wording must be
registered with the provider (runbook 12). A customer of any other language gets the Uzbek SMS.

## Glossaries

One per language. Each fixes the word for debt and sale on credit, payment, customer, shop, due date,
overdue, reminder, subscription, receipt, the staff roles, cash book and stock, and the other terms that
recur. A reviewer reads the glossary first: changing a term there means changing it everywhere.

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

After corrections: `npm test` in `frontend/` and `pytest` in `backend/`; both scripts should still list
nothing missing.

## When the remaining modules are merged

The cash book (module H) and the stock with its suppliers (module I) were merged while this module was
built and are translated. The network between shops (module J) adds its texts in Uzbek and Russian
only: until the final pass its screens read Uzbek in the four new languages (Cyrillic Uzbek for
`uz-Cyrl`, which needs no pass). The names the server gives to stock units and write-off reasons are in
Uzbek and Russian only and read Uzbek elsewhere. The final pass:

1. Run `npm run i18n:missing -- --keys` and `python scripts/i18n_missing.py --keys`; translate every
   key listed, with the glossaries.
2. Add the new brands, codes and wrongly written words that the scripts print for `uz-Cyrl` to the
   rules' tables.
3. If the customer's page gained messages: `npm run i18n:k`.
4. Make completeness strict: run both scripts with `--strict` in CI, and let the tests of the partial
   languages ask for every key.
5. Send the new texts to the reviewers with this page.
