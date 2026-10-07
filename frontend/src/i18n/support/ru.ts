import type { RuSupportCatalog } from "../types";

/** Russian text of the owner's view of support access; the same keys as `support/uz.ts`. */
export const ruSupport: RuSupportCatalog = {
  "support.title": "Доступ поддержки",
  "support.explain":
    "Администратор платформы может временно, только для чтения, видеть клиентов и записи магазина, чтобы решить проблему. Он указывает причину, доступ длится не больше 24 часов, и каждый просмотр записывается в журнал действий. Вы можете завершить доступ в любой момент.",
  "support.none.open": "Сейчас ни один администратор не может видеть данные магазина.",
  "support.open": "Сейчас администратор ({code}) может видеть данные магазина. Доступ закончится {date}, не позже.",
  "support.open.reason": "Указанная причина: {reason}",
  "support.end": "Завершить доступ",
  "support.end.confirm": "Завершить доступ администратора ({code}) сейчас? Он больше не сможет видеть данные магазина.",
  "support.end.yes": "Да, завершить",
  "support.end.no": "Нет",
  "support.ended": "Доступ завершён.",
  "support.history": "История доступов",
  "support.history.none": "Администраторы ещё не получали доступ к этому магазину.",
  "support.col.admin": "Администратор",
  "support.col.reason": "Причина",
  "support.col.from": "Начало",
  "support.col.to": "Время окончания",
  "support.col.outcome": "Как закончился",
  "support.outcome.active": "Открыт сейчас",
  "support.outcome.expired": "Срок истёк",
  "support.outcome.owner": "Завершил владелец · {date}",
  "support.outcome.admin": "Закрыл администратор · {date}",
  "support.outcome.closed": "Закрыт · {date}",
  "support.banner": "Администратор сейчас может видеть данные магазина. Доступ закончится {date}, не позже.",
  "support.banner.reason": "Причина: {reason}",
  "support.banner.open": "Посмотреть и завершить",
};
