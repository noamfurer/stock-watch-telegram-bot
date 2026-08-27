export async function telegramCall<T>(token: string, method: string, payload: Record<string, unknown>): Promise<T> {
  const response = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(15_000),
  });
  const body = await response.json() as { ok?: boolean; description?: string; result?: T };
  if (!response.ok || !body.ok) {
    const error = new Error(body.description || `Telegram ${method} failed`);
    Object.assign(error, { status: response.status });
    throw error;
  }
  return body.result as T;
}

export async function sendMessage(
  token: string,
  chatId: string,
  text: string,
  replyMarkup?: Record<string, unknown>,
): Promise<void> {
  await telegramCall(token, "sendMessage", {
    chat_id: chatId,
    text,
    parse_mode: "HTML",
    disable_web_page_preview: true,
    ...(replyMarkup ? { reply_markup: replyMarkup } : {}),
  });
}

export async function answerCallback(token: string, callbackId: string, text: string): Promise<void> {
  await telegramCall(token, "answerCallbackQuery", { callback_query_id: callbackId, text });
}

export async function setWebhook(token: string, url: string, secretToken: string): Promise<void> {
  await telegramCall(token, "setWebhook", {
    url,
    secret_token: secretToken,
    allowed_updates: ["message", "callback_query"],
    drop_pending_updates: false,
  });
}

export async function getWebhookInfo(token: string): Promise<{ url?: string; pending_update_count?: number; last_error_message?: string }> {
  return telegramCall(token, "getWebhookInfo", {});
}
