import type { RuCatalog } from "./types";

/**
 * Russian catalog. The annotation makes a missing key, an extra key, or a plain string where a plural
 * entry is required a compile error. Month names are in the genitive case, as dates need them.
 */
export const ru: RuCatalog = {
  "app.name": "Qarz Daftari",
  "entry.app": "Рабочее место сотрудников",
  "entry.panel": "Панель управления",
  "entry.admin": "Управление платформой",

  "nav.overview": "Обзор",
  "nav.customers": "Клиенты",
  "nav.newEntry": "Новая запись",
  "nav.catalog": "Каталог",
  "nav.reminders": "Напоминания",
  "nav.reports": "Отчёты",
  "nav.disputes": "Споры и запросы",
  "nav.importExport": "Импорт и экспорт",
  "nav.staff": "Сотрудники",
  "nav.activityLog": "Журнал действий",
  "nav.subscription": "Подписка",
  "nav.shopSettings": "Настройки магазина",
  "nav.more": "Ещё",

  "admin.nav.shops": "Магазины",
  "admin.nav.receipts": "Чеки об оплате",
  "admin.nav.settings": "Настройки",
  "admin.nav.supportAccess": "Доступ поддержки",
  "admin.nav.audit": "Журнал аудита",

  "shell.mainNav": "Основное меню",
  "shell.skipToContent": "Перейти к содержимому",
  "shell.activeShop": "Активный магазин",
  "shell.noShop": "Магазин не выбран",
  "shell.language": "Язык",
  "lang.uz": "O'zbekcha",
  "lang.ru": "Русский",

  "role.owner": "Владелец магазина",
  "role.manager": "Менеджер",
  "role.seller": "Продавец",

  "screen.placeholder": "Этот раздел скоро будет готов.",
  "screen.signInRequired.title": "Требуется вход",
  "screen.signInRequired.body": "Чтобы продолжить, войдите через свой аккаунт Telegram.",
  "notFound.title": "Страница не найдена",
  "notFound.body": "Такой страницы нет или у вас нет к ней доступа.",
  "notFound.home": "Вернуться на главную",

  "action.save": "Сохранить",
  "action.cancel": "Отмена",
  "action.confirm": "Подтвердить",
  "action.delete": "Удалить",
  "action.back": "Назад",
  "action.close": "Закрыть",
  "action.search": "Найти",
  "action.retry": "Повторить",

  "state.loading": "Загрузка…",
  "state.error": "Произошла ошибка",
  "state.empty": "Пока нет данных",

  "customers.count": { one: "{count} клиент", few: "{count} клиента", many: "{count} клиентов" },
  "overdue.days": {
    one: "Просрочено на {count} день",
    few: "Просрочено на {count} дня",
    many: "Просрочено на {count} дней",
  },
  "due.inDays": {
    one: "Остался {count} день",
    few: "Осталось {count} дня",
    many: "Осталось {count} дней",
  },
  "due.today": "Срок сегодня",

  "money.uzs": "{amount} сум",
  "date.dayMonth": "{day} {month}",
  "date.dayMonthYear": "{day} {month} {year} г.",
  "month.1": "января",
  "month.2": "февраля",
  "month.3": "марта",
  "month.4": "апреля",
  "month.5": "мая",
  "month.6": "июня",
  "month.7": "июля",
  "month.8": "августа",
  "month.9": "сентября",
  "month.10": "октября",
  "month.11": "ноября",
  "month.12": "декабря",
};
