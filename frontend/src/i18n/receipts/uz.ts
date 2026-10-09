import type { UzPlural } from "../types";

/**
 * Uzbek text of the owner's subscription receipts only. It is kept apart from the main catalog and
 * added when the subscription screen asks for the section (`addMessages`), so the first load of the
 * Mini App does not carry it (NFR-010). This file defines the keys, Russian mirrors them.
 */
export const uzReceipts = {
  "receipts.title": "Chek yuborish",
  "receipts.explain": "To'lovni yuqoridagi kartaga o'tkazgach, chekni shu yerda yuboring. Administrator ko'rib chiqqach, obuna muddati uzaytiriladi.",
  "receipts.months": "Necha oy uchun to'ladingiz",
  "receipts.months.hint": "1 dan {max} gacha.",
  "receipts.months.invalid": "Oylar soni 1 dan {max} gacha butun son bo'lishi kerak.",
  "receipts.amount": "O'tkazilgan summa",
  "receipts.amount.hint": "Narx bo'yicha hisoblangan: {months} oy uchun {amount}. Boshqa summa o'tkazgan bo'lsangiz, o'zgartiring.",
  "receipts.amount.invalid": "Summa {min} dan {max} gacha, butun so'mda bo'lishi kerak.",
  "receipts.file": "Chek",
  "receipts.file.required": "Chek faylini tanlang.",
  "receipts.send": "Chekni yuborish",
  "receipts.sent": "Chek yuborildi. Administrator ko'rib chiqqach, natijasi shu ro'yxatda ko'rinadi.",
  "receipts.tooManyWaiting": "Ko'rib chiqilmagan cheklaringiz {max} ta: bundan ortiq yuborib bo'lmaydi. Administrator javobini kuting.",
  "receipts.storeDown": "Chekni hozir saqlab bo'lmadi. Birozdan so'ng qayta urinib ko'ring.",
  "receipts.history": "Yuborilgan cheklar",
  "receipts.none": "Hali chek yuborilmagan.",
  "receipts.col.sent": "Yuborilgan",
  "receipts.col.amount": "Summa",
  "receipts.col.months": "Oylar",
  "receipts.col.state": "Holat",
  "receipts.col.card": "Karta",
  "receipts.card": "Karta: {card}",
  "receipts.card.changed": "Kartalar ro'yxati o'zgargan. Sahifani yangilab, kartani qaytadan tanlang.",
  "receipts.state.submitted": "Ko'rib chiqilishini kutmoqda",
  "receipts.state.approved": "Tasdiqlandi. Hisobga olingan oylar: {months}",
  "receipts.state.approved.plain": "Tasdiqlandi",
  "receipts.state.rejected": "Rad etildi. Sabab: {reason}",
  "receipts.state.rejected.plain": "Rad etildi",
  "receipts.stated": { other: "{count} oy uchun" },
} satisfies Record<string, string | UzPlural>;
