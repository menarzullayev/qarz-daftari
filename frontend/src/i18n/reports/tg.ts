import type { PartialCatalog } from "../types";
import type { uzReports } from "./uz";

/** Tajik text of the reports screen. A key that is absent here reads Uzbek at run time. */
export const tgReports: PartialCatalog<typeof uzReports> = {
  "reports.period": "Давра",
  "reports.preset.today": "Имрӯз",
  "reports.preset.week": "Ҳамин ҳафта",
  "reports.preset.month": "Ҳамин моҳ",
  "reports.preset.lastMonth": "Моҳи гузашта",
  "reports.from": "Санаи оғоз",
  "reports.to": "Санаи анҷом",
  "reports.show": "Нишон додан",
  "reports.period.hint": "Давра на зиёда аз {days} рӯз мешавад ва аз имрӯз намегузарад.",
  "reports.problem.DATE_INVALID": "Санаро интихоб кунед.",
  "reports.problem.FROM_AFTER_TO": "Санаи оғоз набояд пас аз санаи анҷом бошад.",
  "reports.problem.IN_FUTURE": "Давра пас аз имрӯз тамом шуда наметавонад.",
  "reports.problem.PERIOD_TOO_LONG": "Давра набояд аз {days} рӯз дарозтар бошад.",
  "reports.period.title": "{from} — {to}",
  "reports.period.oneDay": "{date}",

  "reports.outstanding.start": "Қарз дар аввали давра",
  "reports.outstanding.end": "Қарз дар охири давра",
  "reports.netChange": "Тағйирот дар давра",
  "reports.credit": "Насияи додашуда",
  "reports.payments": "Баргардонда шуд (пардохтҳо)",
  "reports.opening": "Қарзи аввалаи воридшуда",
  "reports.reversals": "Сабтҳои бекоршуда",
  "reports.entries": { other: "{count} сабт" },
  "reports.activity": "{entries}, {customers}",
  "reports.newCustomers": "Мизоҷони нав",
  "reports.disputes": "Эътирозҳои кушодашуда",

  "reports.equation": "Санҷиш",
  "reports.equation.hint":
    "Қарз дар аввали давра + насияи додашуда + қарзи аввала − пардохтҳо = қарз дар охири давра",
  "reports.equation.line": "{start} + {credit} + {opening} − {payments} = {end}",
  "reports.equation.mismatch":
    "Ҳисоб мувофиқ наомад: тарафи чап {computed}, қарз дар охири давра бошад {end}. Ба хадамоти дастгирӣ хабар диҳед.",

  "reports.onTime": "То рӯзи ваъдашуда баргардонда шуд",
  "reports.onTime.value": "{percent}%",
  "reports.onTime.amounts":
    "Аз {due}, ки мӯҳлаташ дар ҳамин давра расидааст, {onTime} сари вақт пардохт шудааст.",
  "reports.onTime.none": "Дар ин давра қарзе, ки мӯҳлати пардохташ расида бошад, набуд.",

  "reports.days": "Аз рӯи рӯзҳо",
  "reports.days.day": "Рӯз",
  "reports.days.credit": "Насия",
  "reports.days.payments": "Пардохтҳо",
  "reports.days.row": "Насия: {credit} · Пардохт: {payments}",
  "reports.days.none": "Дар ин давра на насия навишта шудааст ва на пардохт.",
  "reports.days.quiet": { other: "{count} рӯзе, ки на насия ва на пардохт навишта шудааст, нишон дода нашудааст." },

  "reports.debtors": "Калонтарин қарздорон (дар охири давра)",
  "reports.debtors.none": "Дар охири давра қарздор набуд.",
  "reports.debtors.customer": "Мизоҷ",
  "reports.debtors.balance": "Қарз",

  "reports.staff": "Аз рӯи кормандон",
  "reports.staff.none": "Дар ин давра кормандон насия ё пардохт нанавиштаанд.",
  "reports.staff.member": "{role} · {code}",
  "reports.staff.you": "{member} (Шумо)",
  "reports.staff.who": "Корманд",
  "reports.staff.credit": "Насия",
  "reports.staff.creditCount": "Сабтҳои насия",
  "reports.staff.payments": "Пардохтҳо",
  "reports.staff.paymentCount": "Сабтҳои пардохт",
  "reports.staff.row": "Насия: {credit} ({creditCount}) · Пардохт: {payments} ({paymentCount})",

  "reports.overdue": "Қарзи мӯҳлаташ гузашта, аз рӯи таъхир",
  "reports.overdue.asOf": "Ба ҳолати {date}.",
  "reports.overdue.none": "Қарзи мӯҳлаташ гузашта нест.",
  "reports.overdue.band": "Таъхир",
  "reports.overdue.amount": "Маблағ",
  "reports.overdue.customers": "Мизоҷон",
  "reports.overdue.row": "{amount} · {customers}",
  "reports.overdue.total": "Ҳамагӣ",
  "reports.overdue.totalHint":
    "Қарзи як мизоҷ метавонад дар якчанд сатр бошад; дар ҳамагӣ он як бор шумурда мешавад.",
  "reports.band.range": "{from}–{to} рӯз",
  "reports.band.over": "{from} рӯз ва зиёда",
};
