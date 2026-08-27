import type { Config } from "@netlify/functions";

import { verifyGitHubMigrationRequest } from "./_shared/github-oidc.js";
import { loadWatchlist } from "./_shared/watchlist.js";
import {
  decryptLegacyFernet,
  encryptSecret,
  hasConfig,
  loadHealth,
  loadConfig,
  requiredNetlifyEnv,
  saveConfig,
  saveHealth,
  saveMonitor,
  saveSubscribers,
} from "./_shared/storage.js";
import { setWebhook } from "./_shared/telegram.js";
import type { BotConfig, MonitorState, Subscriber, SubscriberState } from "./_shared/types.js";

interface MigrationRequest {
  token?: string;
  watchlistJson?: string;
  threshold?: string;
  legacyState?: string;
}

function migrateSubscribers(legacy: Record<string, unknown>): SubscriberState {
  const admin = String(legacy.admin_chat_id ?? legacy.chat_id ?? "").trim();
  const rawSubscribers = legacy.subscribers;
  if (!admin || !rawSubscribers || typeof rawSubscribers !== "object" || Array.isArray(rawSubscribers)) {
    throw new Error("Legacy subscriber state is incomplete");
  }
  const subscribers: Record<string, Subscriber> = {};
  for (const [chatId, raw] of Object.entries(rawSubscribers as Record<string, unknown>)) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
    const value = raw as Record<string, unknown>;
    subscribers[chatId] = {
      chat_id: chatId,
      username: String(value.username ?? ""),
      first_name: String(value.first_name ?? ""),
      last_name: String(value.last_name ?? ""),
      enabled: value.enabled !== false,
      joined_at: String(value.joined_at ?? new Date().toISOString()),
    };
  }
  if (!subscribers[admin]) {
    subscribers[admin] = {
      chat_id: admin,
      username: "",
      first_name: "מנהל המערכת",
      last_name: "",
      enabled: true,
      joined_at: new Date().toISOString(),
    };
  }
  return { admin_chat_id: admin, subscribers };
}

function migrateMonitor(legacy: Record<string, unknown>): MonitorState {
  const alerts: Record<string, "up" | "down"> = {};
  if (legacy.alerts && typeof legacy.alerts === "object" && !Array.isArray(legacy.alerts)) {
    for (const [key, value] of Object.entries(legacy.alerts as Record<string, unknown>)) {
      if (value === "up" || value === "down") alerts[key] = value;
    }
  }
  return {
    local_date: String(legacy.local_date ?? ""),
    reports_sent: Array.isArray(legacy.reports_sent) ? legacy.reports_sent.map(String) : [],
    alerts,
    ...(legacy.last_successful_market_check ? { last_successful_market_check: String(legacy.last_successful_market_check) } : {}),
  };
}

export default async (request: Request): Promise<Response> => {
  if (request.method !== "POST") return new Response("Method not allowed", { status: 405 });
  if (!await verifyGitHubMigrationRequest(request)) {
    return Response.json({ ok: false, reason: "unauthorized" }, { status: 401 });
  }
  try {
    const health = await loadHealth();
    if (health.initialized) return Response.json({ ok: false, reason: "already_initialized" }, { status: 409 });
    const body = await request.json() as MigrationRequest;
    const token = body.token?.trim() ?? "";
    const legacyState = body.legacyState?.trim() ?? "";
    if (!token || !legacyState || !body.watchlistJson) throw new Error("Migration payload is incomplete");

    const legacy = decryptLegacyFernet(token, legacyState);
    const threshold = Number(body.threshold);
    if (!Number.isFinite(threshold) || threshold <= 0) throw new Error("Invalid alert threshold");
    const watchlist = loadWatchlist(body.watchlistJson);
    const subscribers = migrateSubscribers(legacy);
    const monitor = migrateMonitor(legacy);

    if (!await hasConfig()) {
      const config: BotConfig = {
        version: 1,
        encrypted_token: encryptSecret(token),
        watchlist,
        threshold,
        migrated_at: new Date().toISOString(),
      };
      await Promise.all([saveConfig(config), saveSubscribers(subscribers), saveMonitor(monitor)]);
    } else {
      const existing = await loadConfig();
      if (existing.token !== token) throw new Error("Migration token does not match stored configuration");
    }

    const baseUrl = requiredNetlifyEnv("PUBLIC_BASE_URL").replace(/\/$/, "");
    await setWebhook(token, `${baseUrl}/api/telegram`, requiredNetlifyEnv("TELEGRAM_WEBHOOK_SECRET"));
    const now = new Date().toISOString();
    await saveHealth({ ...health, initialized: true, webhook_configured_at: now, last_error: undefined });
    return Response.json({ ok: true, subscribers: Object.keys(subscribers.subscribers).length, symbols: watchlist.length });
  } catch (error) {
    console.error("Migration failed", error instanceof Error ? error.message : "Unknown error");
    return Response.json({ ok: false, reason: "migration_failed" }, { status: 400 });
  }
};

export const config: Config = { path: "/api/migrate" };
