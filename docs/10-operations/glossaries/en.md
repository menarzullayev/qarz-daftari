# English glossary (`en`)

The terms the English texts of HisoBox (formerly Qarz Daftari) use, and the Uzbek source term each one stands for. Uzbek
(Latin) is the source of truth; one English term per concept, used the same way in the web panel, the
Mini App, the customer page, the bot and the export workbook.

Style: sentence case everywhere (only the first word and proper nouns are capitalised), plain short
words, no exclamation mark unless the source has one, “curly quotes” where Uzbek has «guillemets».

## Terms

| English term | Uzbek source term | Note |
| --- | --- | --- |
| `{brand}` | `{brand}` | The product's name, HisoBox: never translated and never typed. A text writes `{brand}` and the name is filled in from `backend/src/qarz/domain/brand.json`. |
| debt | qarz | What a customer owes. "Total debt" for *jami qarz*; "debt left" for *qolgan qarz*. Not "balance" in visible text. |
| credit sale | nasiya | A sale on credit: the entry that adds to the debt. Verb: "record a credit sale". Not "loan", not "credit" alone. |
| opening debt | boshlang'ich qarz | A debt brought in from before the shop used the product (import). |
| payment | to'lov | Money a customer pays back. Verbs: "take a payment" (*qabul qilish*), "record a payment" (*yozish*). |
| payment notice | to'lov xabari | A customer's "I paid" message that staff accept or decline. |
| customer | mijoz | Never "client". |
| debtor | qarzdor | A customer with a debt. |
| shop | do'kon | Never "store". The file store (*fayl ombori*) is a different thing. |
| ledger | daftar | The shop's book of entries; also the name of the export sheet. |
| entry | yozuv | One line of the ledger (credit sale, payment, reversal, opening debt). Not "record", not "transaction". |
| reverse, reversal | bekor qilish (yozuvni) | Undoing a ledger entry by adding an entry against it. "Reversal entry" for *bekor qilish yozuvi*; "reversed" for *bekor qilingan*. Never "cancel" for this. |
| cancel | bekor qilish (boshqa hollarda) | For a dialog, an invitation, a shop deletion request. |
| undo (an import) | importni bekor qilish | The import as a whole is "undone"; its entries are "reversed". |
| revoke (a link) | havolani bekor qilish | For the customer's read-only link. |
| withdraw | qaytarib olish | A customer withdraws a dispute; an owner withdraws an ownership offer. |
| due date | to'lash muddati, muddat | The day a credit sale should be paid by. "Move the due date" for *muddatni ko'chirish*. |
| promised date | va'da qilingan kun, to'lash va'dasi | The same date, where the source speaks of the promise ("paid by the promised date"). |
| usual due date | odatdagi muddat | The shop's default number of days to pay. |
| due today | bugun to'lanishi kerak, muddati bugun | |
| overdue | muddati o'tgan | "Overdue debt"; "{count} days overdue". |
| delay | kechikish | How many days late a payment was. |
| on time | o'z vaqtida | |
| reminder | eslatma | A message to a customer about a debt that is due or overdue. |
| due date request | muddat so'rovi | A customer's request to move the due date. |
| request | so'rov | |
| dispute | e'tiroz | A customer's objection to an entry. Verbs: "dispute", "decline a dispute". |
| accept / decline | qabul qilish / rad etish | For disputes, due date requests, payment notices, ownership offers. |
| approve / reject | tasdiqlash / rad etish | For subscription receipts in the administrator's panel only. |
| dismiss | rad etish (katalog, kutayotganlar) | For a learned product or a waiting connection request. |
| subscription | obuna | |
| trial | sinov muddati | |
| free plan | bepul tarif | |
| limited mode | cheklangan rejim | The state after a subscription ends: no new credit sales. |
| suspended | to'xtatilgan | A shop or a staff member stopped by an administrator or the owner. |
| paid through, paid until | to'langan muddat | The last day the subscription is paid for. "Paid period" where a noun is needed. |
| receipt | chek | Proof of a payment: a subscription payment receipt or the receipt attached to a payment notice. |
| owner, shop owner | do'kon egasi, ega | Staff role. |
| manager | menejer | Staff role. |
| seller | sotuvchi | Staff role. |
| staff, staff member | xodimlar, xodim | Not "employee". |
| role | rol | |
| permission | ruxsat | |
| ownership | egalik | "Transfer ownership" for *egalikni o'tkazish*. |
| invitation | taklif (xodimga) | An ownership *taklif* is an "offer". |
| credit limit, limit | nasiya limiti, limit, nasiya chegarasi | "Shop-wide limit" for *do'konning umumiy limiti*. |
| catalog | katalog | |
| product | mahsulot | An entry of the catalog. |
| item | tovar | A line of goods inside a ledger entry; "items" for *tovarlar*. |
| unit | o'lchov birligi, birlik | The default unit word *dona* (piece) is data and stays as typed. |
| quantity | miqdor | |
| amount | summa | |
| total | jami | |
| soum | so'm | Currency word, after the amount, never pluralised: "45 000 soum". |
| $ | $ | Dollar sign after the amount, as in the source: "1 250.50 $". |
| tiyin | tiyin | The hundredth of a soum; kept as is. |
| link | havola | A URL given to a person: personal link, invitation link, read-only link. |
| connect (to Telegram) | ulash, ulanish | Tying a customer record to a Telegram account. Not "link" as a verb, so "link" stays the noun. |
| disconnect | uzilish | |
| counter code | peshtaxta kodi, kassa kodi | The shop's one shared QR code. The permission label says *kassa kodi* in Uzbek; it is the same thing. |
| waiting to connect | ulanishni kutayotganlar | People who scanned the counter code. |
| attach | biriktirish | Tying a waiting person to a customer record. |
| archive, unarchive | arxiv, arxivlash, arxivdan chiqarish | |
| export | eksport | |
| import | import | |
| sample file | namuna fayl | The import template. |
| preview | ko'rib chiqish (import) | What an import would record, shown before it is applied. |
| apply (an import) | qo'llash | |
| activity log | amallar jurnali, faoliyat jurnali | The shop's log. |
| audit log | audit jurnali | The administrator's log. |
| support access | yordam uchun kirish | An administrator's time-limited, read-only view of a shop. |
| support | qo'llab-quvvatlash | |
| administrator | administrator, xizmat ma'muri | "Service administration" for *xizmat ma'muriyati*. |
| second factor | ikkinchi omil | |
| authenticator app | autentifikator ilovasi | |
| sign in / sign out | kirish / chiqish | |
| control panel, panel | boshqaruv paneli, panel | |
| report | hisobot | |
| period | davr | |
| status | holat | |
| settings | sozlamalar | |
| note | izoh | |
| reason | sabab | |
| account | hisob | A customer's account at a shop; also a Telegram account. |
| data | ma'lumotlar | "Delete my data" for *ma'lumotlarimni o'chirish*. |
| cash book | kassa | The shop's book of money in and out (`/kassa` stays as typed). "Day book" for *kunlik daftar*. Not the same as the counter code. |
| income | kirim | Money into the cash book. |
| expense | chiqim | Money out of the cash book. |
| category | toifa | A heading that cash book entries are grouped under. |
| balance | qoldiq (kassa) | Money left in the cash book. Used for the cash book only; what a customer owes is "debt". |
| payment method | to'lov usuli | Cash, card or transfer. |
| cash | naqd | Payment method. |
| card | karta | Payment method; also the card a subscription is paid to. |
| transfer | o'tkazma | Payment method (bank transfer). "Transfer ownership" is a different use. |
| direction | yo'nalish | Income or expense. |
| cancel (a cash book entry) | kassa yozuvini bekor qilish | A cash book entry is "cancelled" with a reason and stays in the book; a ledger entry is "reversed". |
| copy earlier payments | avvalgi to'lovlarni ko'chirish | The one-time copy of old customer payments into the cash book. |
| stock | ombor | The goods the shop holds and the section that tracks them (`/ombor` stays as typed). "In stock" for *qoldiq* of an item; "stock on record" for *hisobdagi qoldiq*. |
| goods receipt | kirim (ombor) | Goods coming into stock: the purchase document and its movement. Always two words, so it is not taken for a payment "receipt" (*chek*) or cash book "income" (*kirim*). |
| supplier | ta'minotchi | Who the shop buys goods from. "We owe" / "our debt" for *qarzimiz*; "account" for *hisob-kitob*. |
| bought on credit | qarzga olingan | Goods taken from a supplier without paying in full. A customer's side stays "credit sale". |
| cost price | tannarx | What the shop paid for an item. "Average cost price" for *o'rtacha tannarx*. |
| selling price | sotish narxi | |
| profit / loss | foyda / zarar | |
| return | qaytarish | "Return from customer" and "return to supplier" as document kinds; "returned by customer", "returned to supplier" as movements. |
| write-off | hisobdan chiqarish | Goods taken out of stock as damaged, expired, lost or for own use. Verb: "write off". |
| stocktake | inventarizatsiya | Counting the goods; "stocktake correction" for the movement it makes. |
| barcode | shtrix-kod | One word. |
| stock document | ombor hujjati | A goods receipt, return, write-off or stocktake. A paper delivery note (*nakladnoy*, *yuk xati*) is not in the texts yet; when it appears it is "delivery note", and the record made from it is a "goods receipt". |
| draft / posted / cancelled | qoralama / o'tkazilgan / bekor qilingan | States of a stock document. Verb "post" for *o'tkazish*. A document and a supplier entry are "cancelled"; the movement that undoes another is a "reversal". |
| partner | hamkor | Another shop of the service this shop is linked with. The section is "Partners". This shop is "we", the other "the partner"; nobody of either is named. |
| partnership | hamkorlik, aloqa | The link between two shops: "partnership request", "end partnership". Not "link", which stays a URL. Connecting by a code is "join with a code". |
| buyer | xaridor | A shop's role in a partnership ("We are the buyer"). A customer of the shop stays "customer". |
| order | buyurtma | What the buyer sends to its supplier; an unsent one is a "draft". An order is accepted or declined by the supplier and cancelled by the buyer. |
| delivery note | yuk xati | What the supplier issues for an accepted order. The buyer confirms or rejects it; the supplier corrects it with a new one. "No. {number}" for *№*. |
| confirm | tasdiqlash | Of a delivery note and of a payment the partner recorded. A note is "rejected"; an order, a payment and a request are "declined". |
| invitation code | taklif kodi | The code one shop hands another outside the service. |
| reconciliation | hisob-kitobni solishtirish | "Reconcile accounts" as the heading. "Our own books" for *o'z daftarimiz*; "the agreed debt" for what both sides confirmed. |
| take back | qaytarib olish | Of a payment this shop recorded and of an invitation. |
| units of a counted item | dona, kg, g, litr, ml, metr, quti, paket, juft, qop, blok | The names shown for a counted item's unit: pcs, kg, g, l, ml, m, box, pack, pair, sack, carton. The stored unit is the Uzbek key and stays as typed (see "unit"). |
| default categories of the cash book | Savdo, Qarz qaytdi, Boshlang'ich qoldiq, Boshqa kirim, Tovar xaridi, Ijara, Ish haqi, Transport, Kommunal to'lovlar, Boshqa chiqim | Sales, Debt repaid, Opening balance, Other income, Goods purchase, Rent, Wages, Transport, Utilities, Other expense. Written once, when a shop first uses the cash book, in the shop's language then; afterwards they are the shop's own names. The stock's two: "Stock: goods purchase", "Stock: refund to customer". |
| the note a stock document writes | Kirim № N; Tovar qaytarildi, hujjat № N | "Goods receipt No. N"; "Goods returned, document No. N". |
| movement | harakat | One change of an item's stock. |
| tracked (in stock) | omborda hisoblanadi, hisobdagi | An item whose stock is counted. |
| running low, low stock level | kam qoldi, kam qoldi chegarasi | |
| below zero | minusda | Stock that went negative. |

## Kept as in the source

- Bot commands: `/start`, `/obuna`, `/til`, `/dokon`, `/yordam`, `/qarzim`, `/uzish`, `/ochirish`.
- Anything the user types for the bot to parse, since the parser reads Uzbek and Russian only:
  `Ali 45000`, `Ali -20000`, `Ali 20000 berdi`, `Ali 45000 non, sut`, `Ali 45000 izoh`, `45k`, `45 ming`,
  `25.10`, `50$`.
- Codes and names: UZS, USD, SMS, QR, PDF, Excel, CSV, UTF-8, Telegram, Payme, Eskiz, Humo, file
  extensions, the sample names in the reminder preview (Ali Valiyev, Baraka savdo).
- Language names (`lang.*`) are autonyms and are copied, not translated.
- SMS templates (`sms_due_today`, `sms_overdue`) exist in Uzbek and Russian only and have no English text.

## Numbers and dates

- Money: thousands separated by spaces, the unit after the amount: "45 000 soum", "1 250.50 $".
- Dates: day first, month name capitalised: "6 October 2026"; day and month alone: "6 October".
- Frontend texts with a count have two forms (`one`, `other`). Backend texts have no plural forms, so a
  count is worded to read for any number: "Days left: {days}", "Months: {months}", "… and {count} more".
