import type { Quote, Subscriber } from "./types.js";
import { hebrewWeekday, israelTime } from "./time.js";

const HEBREW_MONTHS = [
  "", "בינואר", "בפברואר", "במרץ", "באפריל", "במאי", "ביוני", "ביולי",
  "באוגוסט", "בספטמבר", "באוקטובר", "בנובמבר", "בדצמבר",
];

export function escapeHtml(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function signed(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

function directionEmoji(value: number): string {
  return value >= 0 ? "🟢" : "🔴";
}

export function formatReport(quotes: Quote[], now: Date): string {
  const local = israelTime(now);
  const time = `${local.hour.toString().padStart(2, "0")}:${local.minute.toString().padStart(2, "0")}`;
  const lines = [
    `📈 <b>עדכון מניות הלקוחות</b> (${hebrewWeekday(now)}, ${local.day} ${HEBREW_MONTHS[local.month]} ${local.year}, ${time})`,
    "",
  ];
  quotes.forEach((quote, index) => {
    const stock = quote.stock;
    lines.push(
      `${index + 1}. <b>${escapeHtml(stock.name)}</b> (${escapeHtml(stock.display_symbol)}): ` +
      `${quote.price.toFixed(2)} ${escapeHtml(stock.currency_symbol)} | ` +
      `${directionEmoji(quote.change)} ${signed(quote.change)} (${signed(quote.changePercent)}%)`,
    );
  });
  return lines.join("\n");
}

export function formatAlert(quote: Quote, threshold: number, now: Date): string {
  const direction = quote.changePercent >= 0 ? "עולה" : "יורדת";
  const local = israelTime(now);
  const time = `${local.hour.toString().padStart(2, "0")}:${local.minute.toString().padStart(2, "0")}`;
  return [
    `🛎️ <b>${escapeHtml(quote.stock.name)} - התראת מניה</b>`,
    "",
    `מניית <b>${escapeHtml(quote.stock.name)}</b> (${escapeHtml(quote.stock.display_symbol)}) ${direction} ביותר מ-${threshold.toFixed(2)}%.`,
    `מחיר עדכני: ${quote.price.toFixed(2)} ${escapeHtml(quote.stock.currency_symbol)}`,
    `שינוי יומי: ${directionEmoji(quote.change)} ${signed(quote.change)} (${signed(quote.changePercent)}%)`,
    `עודכן בשעה ${time}`,
  ].join("\n");
}

export function subscriberLabel(subscriber: Subscriber): string {
  const fullName = [subscriber.first_name.trim(), subscriber.last_name.trim()].filter(Boolean).join(" ");
  return subscriber.username ? `${fullName || "ללא שם"} (@${subscriber.username})` : fullName || "משתמש ללא שם";
}

export function permissionKeyboard(chatId: string) {
  return {
    inline_keyboard: [[
      { text: "✅ YES", callback_data: `subscriber:yes:${chatId}` },
      { text: "🚫 NO", callback_data: `subscriber:no:${chatId}` },
    ]],
  };
}
