import type { PartialCatalog } from "../types";
import type { uzReceipts } from "./uz";

/** Karakalpak text of the owner's subscription receipts. A key that is absent here reads Uzbek at run time. */
export const kaaReceipts: PartialCatalog<typeof uzReceipts> = {
  "receipts.title": "Chek jiberiw",
  "receipts.explain":
    "Tólemdi joqarıdaǵı kartaǵa ótkergennen keyin chekti usı jerde jiberiń. Administrator kórip shıqqannan keyin jazılıw múddeti uzaytıladı.",
  "receipts.months": "Neshe ay ushın tóledińiz",
  "receipts.months.hint": "1 – {max} aralıǵında.",
  "receipts.months.invalid": "Aylar sanı 1 – {max} aralıǵındaǵı pútin san bolıwı kerek.",
  "receipts.amount": "Ótkerilgen summa",
  "receipts.amount.hint":
    "Baha boyınsha esaplanǵan: {months} ay ushın {amount}. Basqa summa ótkergen bolsańız, ózgertiń.",
  "receipts.amount.invalid": "Summa {min} – {max} aralıǵında, pútin swmda bolıwı kerek.",
  "receipts.file": "Chek",
  "receipts.file.required": "Chek faylın tańlań.",
  "receipts.send": "Chekti jiberiw",
  "receipts.sent": "Chek jiberildi. Administrator kórip shıqqannan keyin nátiyjesi usı dizimde kórinedi.",
  "receipts.tooManyWaiting":
    "Kórip shıǵılmaǵan cheklerińiz {max}: bunnan artıq jiberip bolmaydı. Administrator juwabın kútiń.",
  "receipts.storeDown": "Chekti házir saqlap bolmadı. Birazdan keyin qayta urınıp kóriń.",
  "receipts.history": "Jiberilgen chekler",
  "receipts.none": "Ele chek jiberilmegen.",
  "receipts.col.sent": "Jiberilgen",
  "receipts.col.amount": "Summa",
  "receipts.col.months": "Aylar",
  "receipts.col.state": "Jaǵdayı",
  "receipts.col.card": "Karta",
  "receipts.card": "Karta: {card}",
  "receipts.card.changed": "Kartalar dizimi ózgergen. Betti jańalap, kartanı qaytadan tańlań.",
  "receipts.state.submitted": "Kórip shıǵılıwın kútpekte",
  "receipts.state.approved": "Tastıyıqlandı. Esapqa alınǵan aylar: {months}",
  "receipts.state.approved.plain": "Tastıyıqlandı",
  "receipts.state.rejected": "Ret etildi. Sebep: {reason}",
  "receipts.state.rejected.plain": "Ret etildi",
  "receipts.stated": { other: "{count} ay ushın" },
};
