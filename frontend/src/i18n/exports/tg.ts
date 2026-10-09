import type { PartialCatalog } from "../types";
import type { uzExports } from "./uz";

/** Tajik text of the export screen. A key that is absent here reads Uzbek at run time. */
export const tgExports: PartialCatalog<typeof uzExports> = {
  "exports.title": "Экспорт",
  "exports.explain":
    "Тамоми дафтари мағоза ба як файли Excel (.xlsx) навишта мешавад. Дар файл панҷ варақ мешавад:",
  "exports.sheet.summary":
    "Хулоса — номи мағоза, шумораи мизоҷон ва қарздорон, ҳамагӣ қарз, ҷамъ аз рӯи моҳҳо.",
  "exports.sheet.customers": "Мизоҷон — ном, телефон, ҳолат ва қарзи ҳар мизоҷ.",
  "exports.sheet.ledger": "Дафтар — ҳамаи сабтҳои насия, пардохт ва бекоркунӣ.",
  "exports.sheet.promises": "Мӯҳлатҳо — таърихи мӯҳлатҳои пардохти ҳар сабт.",
  "exports.sheet.goods": "Молҳо — сатрҳои мол дар сабтҳо.",
  "exports.personal":
    "Диққат: дар файл ном ва рақами телефони мизоҷон ҳаст. Онро танҳо дар ҷойи боэътимод нигоҳ доред ва ба бегонагон нафиристед.",
  "exports.limits":
    "Файл {days} рӯз нигоҳ дошта мешавад, баъд нест мешавад. Дар як рӯз на зиёда аз {limit} бор экспорт дархост кардан мумкин аст.",
  "exports.request": "Дархости экспорт",
  "exports.requested": "Дархост қабул шуд. Вақте ки файл тайёр шавад, дар ҳамин рӯйхат дида мешавад.",
  "exports.refused.in_progress":
    "Экспорти пештара ҳоло тайёр шуда истодааст. Пас аз тамом шуданаш навашро дархост кунед.",
  "exports.refused.daily_limit":
    "Шумораи экспорти имрӯза тамом шуд (дар як рӯз {limit} то). Фардо аз нав дархост кунед.",
  "exports.suspended": "Мағоза боздошта шудааст. Дар ин ҳолат экспортро танҳо соҳиби мағоза мегирад.",
  "exports.list": "Экспортҳои охирин",
  "exports.none": "Ҳоло экспорт дархост нашудааст.",
  "exports.col.asked": "Дархост шуд",
  "exports.col.by": "Кӣ дархост кард",
  "exports.col.state": "Ҳолат",
  "exports.col.file": "Файл",
  "exports.by.you": "Шумо",
  "exports.by.member": "Корманд · {code}",
  "exports.state.queued": "Дар навбат",
  "exports.state.running": "Тайёр шуда истодааст",
  "exports.state.done": "Тайёр",
  "exports.state.failed": "Тайёр нашуд",
  "exports.state.expired": "Мӯҳлати файл гузаштааст",
  "exports.rows": { other: "{count} сатр" },
  "exports.keptUntil": "Файл то {date} нигоҳ дошта мешавад, баъд нест мешавад.",
  "exports.expired.note": "Файл нест карда шудааст. Агар лозим бошад, экспорти нав дархост кунед.",
  "exports.waiting.note": "Ҳолат худаш нав мешавад.",
  "exports.failed.interrupted": "Тайёркунӣ канда шуд. Аз нав дархост кунед.",
  "exports.failed.timeout": "Тайёркунӣ хеле тӯл кашид ва қатъ карда шуд. Аз нав дархост кунед.",
  "exports.failed.file_store": "Файлро нигоҳ дошта нашуд. Каме баъдтар аз нав дархост кунед.",
  "exports.failed.internal": "Ҳангоми тайёркунӣ хато рӯй дод. Аз нав дархост кунед.",
  "exports.failed.other": "Файл тайёр нашуд. Аз нав дархост кунед.",
  "exports.link.get": "Гирифтани пайванди боргирӣ",
  "exports.link.open": "Боргирии файл",
  "exports.link.valid": "Пайванд 5 дақиқа, то {date} амал мекунад. Агар кушода нашавад, навашро гиред.",
  "exports.link.again": "Гирифтани пайванди нав",
  "exports.notReady.queued": "Файл ҳоло дар навбат аст. Тайёр шуданашро интизор шавед.",
  "exports.notReady.running": "Файл ҳоло тайёр шуда истодааст. Тайёр шуданашро интизор шавед.",
  "exports.notReady.failed": "Ин экспорт тайёр нашуд. Навашро дархост кунед.",
  "exports.notReady.expired": "Мӯҳлати файл гузаштааст ва он нест шудааст. Экспорти нав дархост кунед.",
  "exports.stale": "Ҳолатро нав карда нашуд.",
  "exports.refresh": "Нав кардан",
};
