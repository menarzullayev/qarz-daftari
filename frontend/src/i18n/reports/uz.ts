import type { UzPlural } from "../types";

/**
 * Uzbek text of the reports screen only. It is kept apart from the main catalog and added by the
 * reports module when that is first opened (`addMessages`), so the first load of the Mini App does not
 * carry it (NFR-010). The rules of the main catalog hold: this file defines the keys, Russian mirrors them.
 */
export const uzReports = {
  "reports.period": "Davr",
  "reports.preset.today": "Bugun",
  "reports.preset.week": "Shu hafta",
  "reports.preset.month": "Shu oy",
  "reports.preset.lastMonth": "O'tgan oy",
  "reports.from": "Boshlanish sanasi",
  "reports.to": "Tugash sanasi",
  "reports.show": "Ko'rsatish",
  "reports.period.hint": "Davr ko'pi bilan {days} kun bo'ladi va bugundan keyinga o'tmaydi.",
  "reports.problem.DATE_INVALID": "Sanani tanlang.",
  "reports.problem.FROM_AFTER_TO": "Boshlanish sanasi tugash sanasidan keyin bo'lmasin.",
  "reports.problem.IN_FUTURE": "Davr bugundan keyin tugashi mumkin emas.",
  "reports.problem.PERIOD_TOO_LONG": "Davr {days} kundan uzun bo'lmasin.",
  "reports.period.title": "{from} — {to}",
  "reports.period.oneDay": "{date}",

  "reports.outstanding.start": "Davr boshidagi qarz",
  "reports.outstanding.end": "Davr oxiridagi qarz",
  "reports.advances.start": "Davr boshidagi avanslar",
  "reports.advances.end": "Davr oxiridagi avanslar",
  "reports.netChange": "Davrdagi o'zgarish",
  "reports.credit": "Berilgan nasiya",
  "reports.payments": "Qaytarilgan (to'lovlar)",
  "reports.opening": "Kiritilgan boshlang'ich qarz",
  "reports.reversals": "Bekor qilingan yozuvlar",
  "reports.entries": { other: "{count} ta yozuv" } satisfies UzPlural,
  "reports.activity": "{entries}, {customers}",
  "reports.newCustomers": "Yangi mijozlar",
  "reports.disputes": "Ochilgan e'tirozlar",

  "reports.equation": "Tekshirish",
  "reports.equation.hint": "Davr boshidagi qarz + berilgan nasiya + boshlang'ich qarz − to'lovlar = davr oxiridagi qarz",
  "reports.equation.line": "{start} + {credit} + {opening} − {payments} = {end}",
  "reports.equation.line.advances":
    "({start} − {advancesStart}) + {credit} + {opening} − {payments} = ({end} − {advancesEnd})",
  "reports.equation.hint.advances": "Mijozlarning avanslari qarzdan ayirib hisoblanadi: (qarz − avanslar).",
  "reports.equation.mismatch":
    "Hisob mos kelmadi: chap tomon {computed}, davr oxiridagi qarz esa {end}. Qo'llab-quvvatlashga xabar bering.",

  "reports.onTime": "Va'da qilingan kungacha qaytarilgan",
  "reports.onTime.value": "{percent}%",
  "reports.onTime.amounts": "Muddati shu davrda kelgan {due} dan {onTime} o'z vaqtida to'langan.",
  "reports.onTime.none": "Bu davrda to'lash muddati kelgan qarz bo'lmagan.",

  "reports.days": "Kunlar bo'yicha",
  "reports.days.day": "Kun",
  "reports.days.credit": "Nasiya",
  "reports.days.payments": "To'lovlar",
  "reports.days.row": "Nasiya: {credit} · To'lov: {payments}",
  "reports.days.none": "Bu davrda nasiya ham, to'lov ham yozilmagan.",
  "reports.days.quiet": { other: "Nasiya ham, to'lov ham yozilmagan {count} kun ko'rsatilmagan." } satisfies UzPlural,

  "reports.debtors": "Eng katta qarzdorlar (davr oxirida)",
  "reports.debtors.none": "Davr oxirida qarzdor bo'lmagan.",
  "reports.debtors.customer": "Mijoz",
  "reports.debtors.balance": "Qarz",

  "reports.staff": "Xodimlar bo'yicha",
  "reports.staff.none": "Bu davrda xodimlar nasiya yoki to'lov yozmagan.",
  "reports.staff.member": "{role} · {code}",
  "reports.staff.you": "{member} (siz)",
  "reports.staff.who": "Xodim",
  "reports.staff.credit": "Nasiya",
  "reports.staff.creditCount": "Nasiya yozuvlari",
  "reports.staff.payments": "To'lovlar",
  "reports.staff.paymentCount": "To'lov yozuvlari",
  "reports.staff.row": "Nasiya: {credit} ({creditCount}) · To'lov: {payments} ({paymentCount})",

  "reports.overdue": "Muddati o'tgan qarz, kechikish bo'yicha",
  "reports.overdue.asOf": "{date} holatiga.",
  "reports.overdue.none": "Muddati o'tgan qarz yo'q.",
  "reports.overdue.band": "Kechikish",
  "reports.overdue.amount": "Summa",
  "reports.overdue.customers": "Mijozlar",
  "reports.overdue.row": "{amount} · {customers}",
  "reports.overdue.total": "Jami",
  "reports.overdue.totalHint": "Bir mijozning qarzi bir nechta qatorda bo'lishi mumkin; jamida u bir marta sanaladi.",
  "reports.band.range": "{from}–{to} kun",
  "reports.band.over": "{from} kun va undan ko'p",
} satisfies Record<string, string | UzPlural>;
