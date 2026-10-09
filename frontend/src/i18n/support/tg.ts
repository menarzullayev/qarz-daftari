import type { PartialCatalog } from "../types";
import type { uzSupport } from "./uz";

/** Tajik text of what the owner sees about support access. A key that is absent here reads Uzbek. */
export const tgSupport: PartialCatalog<typeof uzSupport> = {
  "support.title": "Дастрасӣ барои кумак",
  "support.explain":
    "Администратори платформа барои ҳалли мушкил мизоҷон ва сабтҳои мағозаро муваққатан, танҳо барои хондан дида метавонад. Ӯ сабабро нишон медиҳад, дастрасӣ на зиёда аз 24 соат давом мекунад ва ҳар диданаш ба журнали амалҳо навишта мешавад. Дастрасиро ҳар вақт тамом карда метавонед.",
  "support.none.open": "Ҳоло ягон администратор маълумоти мағозаро дида наметавонад.",
  "support.open":
    "Ҳоло администратор ({code}) маълумоти мағозаро дида метавонад. Дастрасӣ {date} тамом мешавад.",
  "support.open.reason": "Сабаби нишондодашуда: {reason}",
  "support.end": "Тамом кардани дастрасӣ",
  "support.end.confirm":
    "Дастрасии администратор ({code}) ҳозир тамом карда шавад? Ӯ дигар маълумоти мағозаро дида наметавонад.",
  "support.end.yes": "Ҳа, тамом шавад",
  "support.end.no": "Не",
  "support.ended": "Дастрасӣ тамом карда шуд.",
  "support.history": "Таърихи дастрасиҳо",
  "support.history.none": "Администраторон ҳоло ба ин мағоза надаромадаанд.",
  "support.col.admin": "Администратор",
  "support.col.reason": "Сабаб",
  "support.col.from": "Оғоз шуд",
  "support.col.to": "Вақти анҷом",
  "support.col.outcome": "Чӣ тавр тамом шуд",
  "support.outcome.active": "Ҳоло кушода",
  "support.outcome.expired": "Мӯҳлаташ тамом шудааст",
  "support.outcome.owner": "Соҳиб тамом кардааст · {date}",
  "support.outcome.admin": "Администратор пӯшидааст · {date}",
  "support.outcome.closed": "Пӯшида шудааст · {date}",
  "support.banner": "Администратор ҳоло маълумоти мағозаро дида метавонад. Дастрасӣ {date} тамом мешавад.",
  "support.banner.reason": "Сабаб: {reason}",
  "support.banner.open": "Дидан ва тамом кардан",
};
