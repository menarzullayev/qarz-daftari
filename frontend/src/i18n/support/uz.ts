import type { UzPlural } from "../types";

/**
 * Uzbek text of what the owner sees about support access (REQ-059): the section under the shop's
 * settings and the notice above every screen of the web panel. It is kept apart from the main catalog
 * and added by the support module (`addMessages`): the web panel loads it when it starts, the Mini App
 * when the owner opens the settings. This file defines the keys, Russian mirrors them.
 */
export const uzSupport = {
  "support.title": "Yordam uchun kirish",
  "support.explain":
    "Platforma administratori muammoni hal qilish uchun do'kon mijozlari va yozuvlarini vaqtincha, faqat o'qish uchun ko'ra oladi. U sabab ko'rsatadi, kirish ko'pi bilan 24 soat davom etadi va har bir ko'rishi faoliyat jurnaliga yoziladi. Kirishni istalgan payt tugatishingiz mumkin.",
  "support.none.open": "Hozir hech bir administrator do'kon ma'lumotlarini ko'ra olmaydi.",
  "support.open": "Hozir administrator ({code}) do'kon ma'lumotlarini ko'ra oladi. Kirish {date} da tugaydi.",
  "support.open.reason": "Ko'rsatilgan sabab: {reason}",
  "support.end": "Kirishni tugatish",
  "support.end.confirm": "Administratorning ({code}) kirishi hozir tugatilsinmi? U do'kon ma'lumotlarini boshqa ko'ra olmaydi.",
  "support.end.yes": "Ha, tugatilsin",
  "support.end.no": "Yo'q",
  "support.ended": "Kirish tugatildi.",
  "support.history": "Kirishlar tarixi",
  "support.history.none": "Administratorlar bu do'konga hali kirmagan.",
  "support.col.admin": "Administrator",
  "support.col.reason": "Sabab",
  "support.col.from": "Boshlangan",
  "support.col.to": "Tugash vaqti",
  "support.col.outcome": "Qanday tugagan",
  "support.outcome.active": "Hozir ochiq",
  "support.outcome.expired": "Muddati tugagan",
  "support.outcome.owner": "Egasi tugatgan · {date}",
  "support.outcome.admin": "Administrator yopgan · {date}",
  "support.outcome.closed": "Yopilgan · {date}",
  "support.banner": "Administrator hozir do'kon ma'lumotlarini ko'ra oladi. Kirish {date} da tugaydi.",
  "support.banner.reason": "Sabab: {reason}",
  "support.banner.open": "Ko'rish va tugatish",
} satisfies Record<string, string | UzPlural>;
