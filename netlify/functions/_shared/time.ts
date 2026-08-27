export const ISRAEL_TZ = "Asia/Jerusalem";

export interface IsraelTime {
  localDate: string;
  hour: number;
  minute: number;
  weekday: string;
  day: number;
  month: number;
  year: number;
}

export function israelTime(now = new Date()): IsraelTime {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: ISRAEL_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    weekday: "short",
    hourCycle: "h23",
  }).formatToParts(now);
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  const year = Number(value("year"));
  const month = Number(value("month"));
  const day = Number(value("day"));
  return {
    localDate: `${year.toString().padStart(4, "0")}-${month.toString().padStart(2, "0")}-${day.toString().padStart(2, "0")}`,
    hour: Number(value("hour")),
    minute: Number(value("minute")),
    weekday: value("weekday"),
    day,
    month,
    year,
  };
}

export function isMonitoringWindow(now = new Date()): boolean {
  const local = israelTime(now);
  const weekday = ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(local.weekday);
  const minutes = local.hour * 60 + local.minute;
  return weekday && minutes >= 10 * 60 && minutes < 18 * 60;
}

export function israelTimestamp(now = new Date()): string {
  const local = israelTime(now);
  return `${local.localDate}T${local.hour.toString().padStart(2, "0")}:${local.minute.toString().padStart(2, "0")}:00[${ISRAEL_TZ}]`;
}

export function reportSlotsDue(now: Date, alreadySent: Set<string>): string[] {
  const local = israelTime(now);
  const currentMinutes = local.hour * 60 + local.minute;
  return ["11:00", "14:00", "16:30"].filter((slot) => {
    if (alreadySent.has(slot)) return false;
    const [hour, minute] = slot.split(":").map(Number) as [number, number];
    const elapsed = currentMinutes - (hour * 60 + minute);
    return elapsed >= 0 && elapsed <= 90;
  });
}

export function hasRecentSuccess(iso: string | undefined, now: Date, minutes = 10): boolean {
  if (!iso) return false;
  const timestamp = Date.parse(iso);
  if (!Number.isFinite(timestamp)) return false;
  const elapsed = now.getTime() - timestamp;
  return elapsed >= 0 && elapsed < minutes * 60_000;
}

export function hebrewWeekday(now: Date): string {
  return new Intl.DateTimeFormat("he-IL", { timeZone: ISRAEL_TZ, weekday: "long" }).format(now);
}
