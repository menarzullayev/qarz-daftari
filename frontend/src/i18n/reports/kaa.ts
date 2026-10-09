import type { PartialCatalog } from "../types";
import type { uzReports } from "./uz";

/** Karakalpak text of the reports screen. A key that is absent here reads Uzbek at run time. */
export const kaaReports: PartialCatalog<typeof uzReports> = {
  "reports.period": "Dáwir",
  "reports.preset.today": "Búgin",
  "reports.preset.week": "Usı hápte",
  "reports.preset.month": "Usı ay",
  "reports.preset.lastMonth": "Ótken ay",
  "reports.from": "Baslanıw sánesi",
  "reports.to": "Tamamlanıw sánesi",
  "reports.show": "Kórsetiw",
  "reports.period.hint": "Dáwir kóp degende {days} kún boladı hám búginnen keyinge ótpeydi.",
  "reports.problem.DATE_INVALID": "Sáneni tańlań.",
  "reports.problem.FROM_AFTER_TO": "Baslanıw sánesi tamamlanıw sánesinen keyin bolmasın.",
  "reports.problem.IN_FUTURE": "Dáwir búginnen keyin tamamlanıwı múmkin emes.",
  "reports.problem.PERIOD_TOO_LONG": "Dáwir {days} kúnnen uzın bolmasın.",
  "reports.period.title": "{from} — {to}",
  "reports.period.oneDay": "{date}",

  "reports.outstanding.start": "Dáwir basındaǵı qarız",
  "reports.outstanding.end": "Dáwir aqırındaǵı qarız",
  "reports.netChange": "Dáwirdegi ózgeris",
  "reports.credit": "Berilgen nesiye",
  "reports.payments": "Qaytarılǵan (tólemler)",
  "reports.opening": "Kiritilgen baslanǵısh qarız",
  "reports.reversals": "Biykarlanǵan jazbalar",
  "reports.entries": { other: "{count} jazba" },
  "reports.activity": "{entries}, {customers}",
  "reports.newCustomers": "Jańa qarıydarlar",
  "reports.disputes": "Ashılǵan narazılıqlar",

  "reports.equation": "Tekseriw",
  "reports.equation.hint":
    "Dáwir basındaǵı qarız + berilgen nesiye + baslanǵısh qarız − tólemler = dáwir aqırındaǵı qarız",
  "reports.equation.line": "{start} + {credit} + {opening} − {payments} = {end}",
  "reports.equation.mismatch":
    "Esap sáykes kelmedi: shep tárepi {computed}, al dáwir aqırındaǵı qarız {end}. Qollap-quwatlaw xızmetine xabar beriń.",

  "reports.onTime": "Wáde etilgen kúnge shekem qaytarılǵan",
  "reports.onTime.value": "{percent}%",
  "reports.onTime.amounts": "Múddeti usı dáwirde kelgen {due} summadan {onTime} óz waqtında tólengen.",
  "reports.onTime.none": "Bul dáwirde tólew múddeti kelgen qarız bolmaǵan.",

  "reports.days": "Kúnler boyınsha",
  "reports.days.day": "Kún",
  "reports.days.credit": "Nesiye",
  "reports.days.payments": "Tólemler",
  "reports.days.row": "Nesiye: {credit} · Tólem: {payments}",
  "reports.days.none": "Bul dáwirde nesiye de, tólem de jazılmaǵan.",
  "reports.days.quiet": { other: "Nesiye de, tólem de jazılmaǵan {count} kún kórsetilmegen." },

  "reports.debtors": "Eń úlken qarızdarlar (dáwir aqırında)",
  "reports.debtors.none": "Dáwir aqırında qarızdar bolmaǵan.",
  "reports.debtors.customer": "Qarıydar",
  "reports.debtors.balance": "Qarız",

  "reports.staff": "Xızmetkerler boyınsha",
  "reports.staff.none": "Bul dáwirde xızmetkerler nesiye yamasa tólem jazbaǵan.",
  "reports.staff.member": "{role} · {code}",
  "reports.staff.you": "{member} (siz)",
  "reports.staff.who": "Xızmetker",
  "reports.staff.credit": "Nesiye",
  "reports.staff.creditCount": "Nesiye jazbaları",
  "reports.staff.payments": "Tólemler",
  "reports.staff.paymentCount": "Tólem jazbaları",
  "reports.staff.row": "Nesiye: {credit} ({creditCount}) · Tólem: {payments} ({paymentCount})",

  "reports.overdue": "Múddeti ótken qarız, keshigiw boyınsha",
  "reports.overdue.asOf": "{date} jaǵdayına.",
  "reports.overdue.none": "Múddeti ótken qarız joq.",
  "reports.overdue.band": "Keshigiw",
  "reports.overdue.amount": "Summa",
  "reports.overdue.customers": "Qarıydarlar",
  "reports.overdue.row": "{amount} · {customers}",
  "reports.overdue.total": "Jámi",
  "reports.overdue.totalHint":
    "Bir qarıydardıń qarızı bir neshe qatarda bolıwı múmkin; jámide ol bir ret sanaladı.",
  "reports.band.range": "{from}–{to} kún",
  "reports.band.over": "{from} kún hám onnan kóp",
};
