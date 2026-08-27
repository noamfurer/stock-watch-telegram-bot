import type { Config } from "@netlify/functions";
import { timingSafeEqual } from "node:crypto";

import { escapeHtml, formatReport, permissionKeyboard, subscriberLabel } from "./_shared/format.js";
import { fetchQuotes } from "./_shared/market.js";
import { loadConfig, loadSubscribers, saveSubscribers } from "./_shared/storage.js";
import { answerCallback, sendMessage } from "./_shared/telegram.js";
import type { Subscriber, SubscriberState } from "./_shared/types.js";

interface TelegramUser {
  id?: number;
  username?: string;
  first_name?: string;
  last_name?: string;
}

interface TelegramMessage {
  text?: string;
  chat?: { id?: number; type?: string };
  from?: TelegramUser;
}

interface TelegramUpdate {
  update_id?: number;
  message?: TelegramMessage;
  callback_query?: {
    id?: string;
    from?: TelegramUser;
    data?: string;
  };
}

function authorized(request: Request, expected: string): boolean {
  const actual = request.headers.get("x-telegram-bot-api-secret-token") ?? "";
  const left = Buffer.from(actual);
  const right = Buffer.from(expected);
  return left.length === right.length && timingSafeEqual(left, right);
}

function subscriberFromMessage(message: TelegramMessage, chatId: string, now: Date): Subscriber {
  const user = message.from ?? {};
  return {
    chat_id: chatId,
    username: String(user.username ?? ""),
    first_name: String(user.first_name ?? ""),
    last_name: String(user.last_name ?? ""),
    enabled: true,
    joined_at: now.toISOString(),
  };
}

function updateSubscriberProfile(subscriber: Subscriber, user: TelegramUser): boolean {
  let changed = false;
  for (const key of ["username", "first_name", "last_name"] as const) {
    const value = String(user[key] ?? "");
    if (subscriber[key] !== value) {
      subscriber[key] = value;
      changed = true;
    }
  }
  return changed;
}

function usersMessage(state: SubscriberState): { text: string; keyboard: Record<string, unknown> } {
  const ordered = Object.entries(state.subscribers)
    .sort(([, left], [, right]) => left.joined_at.localeCompare(right.joined_at));
  const lines = ["👥 <b>רשימת מנויים</b>", ""];
  const keyboard: Array<Array<Record<string, string>>> = [];
  ordered.forEach(([chatId, subscriber], index) => {
    const status = subscriber.enabled ? "✅ YES" : "🚫 NO";
    lines.push(`${index + 1}. ${escapeHtml(subscriberLabel(subscriber))} | <b>${status}</b> | <code>${escapeHtml(chatId)}</code>`);
    keyboard.push([
      { text: `${index + 1} ✅ YES`, callback_data: `subscriber:yes:${chatId}` },
      { text: `${index + 1} 🚫 NO`, callback_data: `subscriber:no:${chatId}` },
    ]);
  });
  if (ordered.length === 0) lines.push("אין עדיין מנויים.");
  return { text: lines.join("\n"), keyboard: { inline_keyboard: keyboard } };
}

async function processMessage(
  token: string,
  state: SubscriberState,
  message: TelegramMessage,
  now: Date,
): Promise<boolean> {
  const chat = message.chat ?? {};
  const chatId = chat.id === undefined ? "" : String(chat.id);
  if (!chatId || chat.type !== "private") return false;
  const text = String(message.text ?? "").trim();
  const command = text ? text.split(/\s+/, 1)[0]!.split("@", 1)[0]! : "";

  if (command === "/start") {
    const existing = state.subscribers[chatId];
    if (existing) {
      const changed = updateSubscriberProfile(existing, message.from ?? {});
      const status = existing.enabled ? "פעילה" : "חסומה";
      await sendMessage(token, chatId, `ℹ️ ההרשמה שלך כבר קיימת. קבלת ההודעות כרגע <b>${status}</b>.`);
      return changed;
    }

    const subscriber = subscriberFromMessage(message, chatId, now);
    state.subscribers[chatId] = subscriber;
    await saveSubscribers(state);
    await sendMessage(
      token,
      chatId,
      "✅ <b>הצטרפת לעדכוני המניות</b>\n\nתקבל כאן את הדוחות וההתראות. לקבלת תמונת מצב מלאה ומיידית, אפשר לכתוב <b>עכשיו</b>. מנהל המערכת רשאי לאשר או לחסום את קבלת ההודעות.",
    );
    if (state.admin_chat_id && chatId !== state.admin_chat_id) {
      await sendMessage(
        token,
        state.admin_chat_id,
        `👤 <b>מצטרף חדש לבוט</b>\n\n${escapeHtml(subscriberLabel(subscriber))}\nמזהה: <code>${escapeHtml(chatId)}</code>\nסטטוס התחלתי: <b>YES</b>`,
        permissionKeyboard(chatId),
      );
    }
    return false;
  }

  if (command === "/users" && chatId === state.admin_chat_id) {
    const response = usersMessage(state);
    await sendMessage(token, chatId, response.text, response.keyboard);
    return false;
  }

  if (text === "עכשיו" || command === "/now") {
    const subscriber = state.subscribers[chatId];
    if (!subscriber) {
      await sendMessage(token, chatId, "כדי לקבל תמונת מצב, יש ללחוץ תחילה על <b>Start</b>.");
      return false;
    }
    if (!subscriber.enabled) {
      await sendMessage(token, chatId, "קבלת עדכוני המניות שלך חסומה כרגע.");
      return false;
    }
    const { config } = await loadConfig();
    const quotes = await fetchQuotes(config.watchlist);
    if (quotes.length === 0) {
      await sendMessage(token, chatId, "לא הצלחתי לקבל כרגע נתוני מסחר. אפשר לנסות שוב בעוד דקה.");
      return false;
    }
    await sendMessage(token, chatId, formatReport(quotes, now));
  }
  return false;
}

async function processCallback(token: string, state: SubscriberState, update: TelegramUpdate): Promise<boolean> {
  const callback = update.callback_query;
  if (!callback?.id) return false;
  const actorId = callback.from?.id === undefined ? "" : String(callback.from.id);
  const parts = String(callback.data ?? "").split(":", 3);
  if (actorId !== state.admin_chat_id || parts.length !== 3 || parts[0] !== "subscriber") {
    await answerCallback(token, callback.id, "אין הרשאה לפעולה");
    return false;
  }
  const targetChatId = parts[2]!;
  const subscriber = state.subscribers[targetChatId];
  if (!subscriber || !["yes", "no"].includes(parts[1]!)) {
    await answerCallback(token, callback.id, "המשתמש לא נמצא");
    return false;
  }
  const enabled = parts[1] === "yes";
  const changed = subscriber.enabled !== enabled;
  subscriber.enabled = enabled;
  if (changed) await saveSubscribers(state);
  await answerCallback(token, callback.id, "ההרשאה עודכנה");
  await sendMessage(
    token,
    state.admin_chat_id,
    `עודכן: ${escapeHtml(subscriberLabel(subscriber))} מסומן כעת <b>${enabled ? "YES" : "NO"}</b>.`,
  );
  if (targetChatId !== state.admin_chat_id) {
    try {
      await sendMessage(token, targetChatId, `ℹ️ קבלת עדכוני המניות ${enabled ? "הופעלה" : "הושהתה"} על ידי מנהל המערכת.`);
    } catch {
      // The permission change remains saved even if the user has blocked the bot.
    }
  }
  return changed;
}

export default async (request: Request): Promise<Response> => {
  if (request.method !== "POST") return new Response("Method not allowed", { status: 405 });
  try {
    const { config, token } = await loadConfig();
    if (!authorized(request, config.webhook_secret)) return new Response("Unauthorized", { status: 401 });
    const update = await request.json() as TelegramUpdate;
    const state = await loadSubscribers();
    let changed = false;
    if (update.message) changed = await processMessage(token, state, update.message, new Date()) || changed;
    if (update.callback_query) changed = await processCallback(token, state, update) || changed;
    if (changed) await saveSubscribers(state);
    return new Response("ok", { status: 200 });
  } catch (error) {
    console.error("Telegram webhook failed", error instanceof Error ? error.message : "Unknown error");
    return new Response("Temporary failure", { status: 500 });
  }
};

export const config: Config = { path: "/api/telegram" };
