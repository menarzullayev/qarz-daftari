import type { PartialCatalog } from "../types";
import type { uzReceipts } from "./uz";

/** Tajik text of the owner's subscription receipts. A key that is absent here reads Uzbek at run time. */
export const tgReceipts: PartialCatalog<typeof uzReceipts> = {
  "receipts.title": "Фиристодани чек",
  "receipts.explain":
    "Пас аз гузаронидани пардохт ба корти боло чекро дар ҳамин ҷо фиристед. Пас аз он ки администратор дида барояд, мӯҳлати обуна дароз карда мешавад.",
  "receipts.months": "Барои чанд моҳ пардохт кардед",
  "receipts.months.hint": "Аз 1 то {max}.",
  "receipts.months.invalid": "Шумораи моҳҳо бояд адади бутун аз 1 то {max} бошад.",
  "receipts.amount": "Маблағи гузаронидашуда",
  "receipts.amount.hint":
    "Аз рӯи нарх ҳисоб шудааст: барои {months} моҳ {amount}. Агар маблағи дигар гузаронида бошед, тағйир диҳед.",
  "receipts.amount.invalid": "Маблағ бояд аз {min} то {max}, бо сӯми бутун бошад.",
  "receipts.file": "Чек",
  "receipts.file.required": "Файли чекро интихоб кунед.",
  "receipts.send": "Фиристодани чек",
  "receipts.sent":
    "Чек фиристода шуд. Пас аз он ки администратор дида барояд, натиҷа дар ҳамин рӯйхат дида мешавад.",
  "receipts.tooManyWaiting":
    "Чекҳои дида баромаданашудаи Шумо {max} то мебошанд: аз ин зиёд фиристода намешавад. Ҷавоби администраторро интизор шавед.",
  "receipts.storeDown": "Чекро ҳозир нигоҳ дошта нашуд. Каме баъдтар аз нав кӯшиш кунед.",
  "receipts.history": "Чекҳои фиристодашуда",
  "receipts.none": "Ҳоло чек фиристода нашудааст.",
  "receipts.col.sent": "Фиристода шуд",
  "receipts.col.amount": "Маблағ",
  "receipts.col.months": "Моҳҳо",
  "receipts.col.state": "Ҳолат",
  "receipts.col.card": "Корт",
  "receipts.card": "Корт: {card}",
  "receipts.card.changed": "Рӯйхати кортҳо тағйир ёфтааст. Саҳифаро нав карда, кортро аз нав интихоб кунед.",
  "receipts.state.submitted": "Дида баромаданро интизор аст",
  "receipts.state.approved": "Тасдиқ шуд. Моҳҳои ба ҳисоб гирифташуда: {months}",
  "receipts.state.approved.plain": "Тасдиқ шуд",
  "receipts.state.rejected": "Рад шуд. Сабаб: {reason}",
  "receipts.state.rejected.plain": "Рад шуд",
  "receipts.stated": { other: "барои {count} моҳ" },
};
