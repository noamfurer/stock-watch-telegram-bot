import type { Config } from "@netlify/functions";

import { getWebhookInfo } from "./_shared/telegram.js";
import { loadConfig, loadHealth, loadSubscribers } from "./_shared/storage.js";
import { israelTimestamp } from "./_shared/time.js";

function israelTimestampOrNull(value: string | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : israelTimestamp(date);
}

export default async (request: Request): Promise<Response> => {
  try {
    const health = await loadHealth();
    if (!health.initialized) return Response.json({ ok: false, initialized: false }, { status: 503 });
    const [{ token }, subscribers] = await Promise.all([loadConfig(), loadSubscribers()]);
    const webhook = await getWebhookInfo(token);
    const expected = `${new URL(request.url).origin}/api/telegram`;
    const webhookOk = webhook.url === expected && !webhook.last_error_message;
    return Response.json({
      ok: webhookOk,
      initialized: true,
      webhook: webhookOk,
      pending_updates: webhook.pending_update_count ?? 0,
      enabled_subscribers: Object.values(subscribers.subscribers).filter((subscriber) => subscriber.enabled).length,
      last_market_check: health.last_market_check ?? null,
      last_market_check_israel: israelTimestampOrNull(health.last_market_check),
      last_primary_success: health.last_primary_success ?? null,
      last_primary_success_israel: israelTimestampOrNull(health.last_primary_success),
      last_backup_success: health.last_backup_success ?? null,
      last_backup_success_israel: israelTimestampOrNull(health.last_backup_success),
    }, { status: webhookOk ? 200 : 503 });
  } catch (error) {
    console.error("Health check failed", error instanceof Error ? error.message : "Unknown error");
    return Response.json({ ok: false }, { status: 503 });
  }
};

export const config: Config = { path: "/api/health" };
