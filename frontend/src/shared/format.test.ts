import { describe, expect, it } from "vitest";

import {
  formatCalendarDay,
  formatCustomerCount,
  formatDateTime,
  formatDayMonth,
  formatDueStatus,
  formatFullDate,
  formatMoney,
  tashkentDay,
} from "./format";

describe("formatDayMonth", () => {
  const noon = new Date("2026-10-06T07:00:00Z");

  it("formats in Uzbek and Russian", () => {
    expect(formatDayMonth(noon, "uz")).toBe("6-oktabr");
    expect(formatDayMonth(noon, "ru")).toBe("6 октября");
  });

  it("uses the Tashkent calendar day, not the UTC one", () => {
    // 19:30 UTC on 5 October is 00:30 on 6 October in Tashkent.
    const lateEvening = new Date("2026-10-05T19:30:00Z");
    expect(formatDayMonth(lateEvening, "uz")).toBe("6-oktabr");
    expect(formatDayMonth(lateEvening, "uz")).not.toBe("5-oktabr");
    // 18:59 UTC is still 23:59 on 5 October in Tashkent.
    expect(formatDayMonth(new Date("2026-10-05T18:59:00Z"), "ru")).toBe("5 октября");
  });

  it("crosses the year boundary at Tashkent midnight", () => {
    expect(tashkentDay(new Date("2026-12-31T19:00:00Z"))).toEqual({ year: 2027, month: 1, day: 1 });
    expect(formatFullDate(new Date("2026-12-31T19:00:00Z"), "uz")).toBe("2027-yil 1-yanvar");
    expect(formatFullDate(new Date("2026-12-31T18:59:59Z"), "ru")).toBe("31 декабря 2026 г.");
  });

  it("names every month in both languages", () => {
    const uz = Array.from({ length: 12 }, (_, month) => formatDayMonth(new Date(Date.UTC(2026, month, 15)), "uz"));
    expect(uz).toEqual([
      "15-yanvar",
      "15-fevral",
      "15-mart",
      "15-aprel",
      "15-may",
      "15-iyun",
      "15-iyul",
      "15-avgust",
      "15-sentabr",
      "15-oktabr",
      "15-noyabr",
      "15-dekabr",
    ]);
    const ru = Array.from({ length: 12 }, (_, month) => formatDayMonth(new Date(Date.UTC(2026, month, 15)), "ru"));
    expect(ru).toEqual([
      "15 января",
      "15 февраля",
      "15 марта",
      "15 апреля",
      "15 мая",
      "15 июня",
      "15 июля",
      "15 августа",
      "15 сентября",
      "15 октября",
      "15 ноября",
      "15 декабря",
    ]);
  });

  it("rejects an invalid date", () => {
    expect(() => formatDayMonth(new Date("not a date"), "uz")).toThrow(RangeError);
  });
});

describe("formatDueStatus", () => {
  const now = new Date("2026-10-06T07:00:00Z");

  it("counts overdue days", () => {
    expect(formatDueStatus(new Date("2026-10-03T07:00:00Z"), now, "uz")).toBe("3 kun kechikkan");
    expect(formatDueStatus(new Date("2026-10-03T07:00:00Z"), now, "ru")).toBe("Просрочено на 3 дня");
    expect(formatDueStatus(new Date("2026-10-05T07:00:00Z"), now, "ru")).toBe("Просрочено на 1 день");
    expect(formatDueStatus(new Date("2026-09-25T07:00:00Z"), now, "ru")).toBe("Просрочено на 11 дней");
  });

  it("says today on the due day and counts down before it", () => {
    expect(formatDueStatus(new Date("2026-10-06T18:00:00Z"), now, "uz")).toBe("Muddati bugun");
    expect(formatDueStatus(new Date("2026-10-06T00:00:00Z"), now, "ru")).toBe("Срок сегодня");
    expect(formatDueStatus(new Date("2026-10-08T07:00:00Z"), now, "uz")).toBe("2 kun qoldi");
    expect(formatDueStatus(new Date("2026-10-07T07:00:00Z"), now, "ru")).toBe("Остался 1 день");
    expect(formatDueStatus(new Date("2026-10-11T07:00:00Z"), now, "ru")).toBe("Осталось 5 дней");
  });

  it("counts calendar days in Tashkent, not 24-hour periods", () => {
    // Due at 23:00 Tashkent on 5 October, checked at 00:30 Tashkent on 6 October: one day late.
    const due = new Date("2026-10-05T18:00:00Z");
    const justAfterMidnight = new Date("2026-10-05T19:30:00Z");
    expect(formatDueStatus(due, justAfterMidnight, "uz")).toBe("1 kun kechikkan");
    expect(formatDueStatus(due, justAfterMidnight, "uz")).not.toBe("Muddati bugun");
  });
});

describe("formatMoney", () => {
  it("adds the currency word of the language", () => {
    expect(formatMoney(45000, "uz")).toBe("45 000 so'm");
    expect(formatMoney(45000, "ru")).toBe("45 000 сум");
    expect(formatMoney(0, "uz")).toBe("0 so'm");
  });

  it("rejects an amount that is not whole UZS", () => {
    expect(() => formatMoney(45000.5, "uz")).toThrow(RangeError);
  });
});

describe("formatCustomerCount", () => {
  it("uses the plural form of the language", () => {
    expect(formatCustomerCount(22, "uz")).toBe("22 ta mijoz");
    expect(formatCustomerCount(22, "ru")).toBe("22 клиента");
  });
});

describe("formatCalendarDay", () => {
  it("formats a calendar date, such as a promised date, without shifting it", () => {
    expect(formatCalendarDay({ year: 2026, month: 11, day: 5 }, "uz")).toBe("2026-yil 5-noyabr");
    expect(formatCalendarDay({ year: 2027, month: 1, day: 1 }, "ru")).toBe("1 января 2027 г.");
  });
});

describe("formatDateTime", () => {
  it("shows the Tashkent date and time of an instant", () => {
    expect(formatDateTime(new Date("2026-10-06T06:05:00Z"), "uz")).toBe("2026-yil 6-oktabr, 11:05");
    expect(formatDateTime(new Date("2026-10-06T06:05:00Z"), "ru")).toBe("6 октября 2026 г., 11:05");
  });

  it("moves to the next day at Tashkent midnight, not at UTC midnight", () => {
    expect(formatDateTime(new Date("2026-10-05T18:59:00Z"), "uz")).toBe("2026-yil 5-oktabr, 23:59");
    expect(formatDateTime(new Date("2026-10-05T19:00:00Z"), "uz")).toBe("2026-yil 6-oktabr, 00:00");
  });

  it("gives the same answer whatever offset the timestamp was written with", () => {
    expect(formatDateTime(new Date("2026-10-06T11:05:00+05:00"), "uz")).toBe("2026-yil 6-oktabr, 11:05");
    expect(formatDateTime(new Date("2026-10-05T22:05:00-08:00"), "uz")).toBe("2026-yil 6-oktabr, 11:05");
  });

  it("rejects an invalid date", () => {
    expect(() => formatDateTime(new Date("nonsense"), "uz")).toThrow(RangeError);
  });
});
