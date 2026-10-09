import type { PartialCatalog } from "../types";
import type { uzShare } from "./uz";

/** Tajik text of a customer's read-only link, as staff see it. A key that is absent here reads Uzbek. */
export const tgShare: PartialCatalog<typeof uzShare> = {
  "share.title": "Пайванд барои мизоҷ (бе Telegram)",
  "share.explain":
    "Бо пайванд ё QR код мизоҷ қарзи худро мебинад: бе даромадан ба система, танҳо барои хондан. Пайванд дар дасти кӣ бошад, ҳамон мебинад — онро танҳо ба худи мизоҷ диҳед.",
  "share.none": "Барои ин мизоҷ пайванд нест.",
  "share.active": "Пайванд амал мекунад: то {date}.",
  "share.expired": "Мӯҳлати пайванд {date} тамом шудааст. Он дигар кушода намешавад.",
  "share.opened": "Бори охир кушода шуд: {date}.",
  "share.neverOpened": "Ҳоло кушода нашудааст.",
  "share.create": "Сохтани пайванд",
  "share.replace": "Сохтани пайванди нав",
  "share.replace.question":
    "Агар пайванди нав сохта шавад, пештара ҳамон лаҳза аз кор мемонад. QR коди чопшуда ҳам. Давом медиҳед?",
  "share.replace.yes": "Ҳа, навашро сохтан",
  "share.revoke": "Бекор кардани пайванд",
  "share.revoke.question":
    "Пайванд ҳамон лаҳза аз кор мемонад. Мизоҷ онро кушода наметавонад. Бекор мекунед?",
  "share.revoke.yes": "Ҳа, бекор кардан",
  "share.revoked": "Пайванд бекор шуд.",
  "share.once":
    "Пайванд танҳо ҳозир нишон дода мешавад: он нигоҳ дошта намешавад. Нусха гиред ё чоп кунед. Агар гум шавад, навашро созед.",
  "share.lost": "Пайванд сохта шуд, вале матни он ба ин экран нарасид. Навашро созед.",
  "share.caption": "Барои дидани қарзатон ҳамин кодро скан кунед",
  "share.hide": "Пинҳон кардани пайванд",
  "share.archived": "Барои мизоҷи дар архив буда пайванд сохта намешавад.",
  "share.contact.title": "Телефон дар пайванди мизоҷ",
  "share.contact.explain":
    "Вақте ки мизоҷ пайвандро мекушояд, дар паҳлуи номи мағоза ҳамин телефон дида мешавад. Агар холӣ монед, телефон нишон дода намешавад.",
  "share.contact.label": "Телефони мағоза",
  "share.contact.none": "Телефон нишон дода нашудааст.",
  "share.contact.saved": "Нигоҳ дошта шуд.",
  "share.contact.invalid": "Рақами телефон нодуруст аст. Намуна: +998 90 123 45 67",
};
