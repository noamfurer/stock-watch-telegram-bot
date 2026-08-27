import type { Config } from "@netlify/functions";

import { getWebhookInfo } from "./_shared/telegram.js";
import { loadConfig, loadHealth, loadSubscribers, requiredNetlifyEnv } from "./_shared/storage.js";

export default async (): Promise<Response> => {
  try {
    const health = await loadHealth();
    if (!health.initialized) return Response.json({ ok: false, initialized: false }, { status: 503 });
    const [{ token }, subscribers] = await Promise.all([loadConfig(), loadSubscribers()]);
    const webhook = await getWebhookInfo(token);
    const expected = `${requiredNetlifyEnv("PUBLIC_BASE_URL").replace(/\/$/, "")}/api/telegram`;
    const webhookOk = webhook.url === expected && !webhook.last_error_message;
    return Response.json({
      ok: webhookOk,
      initialized: true,
      webhook: webhookOk,
      pending_updates: webhook.pending_update_count ?? 0,
      enabled_subscribers: Object.values(subscribers.subscribers).filter((subscriber) => subscriber.enabled).length,
      last_market_check: health.last_market_check ?? null,
      last_primary_success: health.last_primary_success ?? null,
      last_backup_success: health.last_backup_success ?? null,
    }, { status: webhookOk ? 200 : 503 });
  } catch (error) {
    console.error("Health check failed", error instanceof Error ? error.message : "Unknown error");
    return Response.json({ ok: false }, { status: 503 });
  }
};

export const config: Config = { path: "/api/health" };
