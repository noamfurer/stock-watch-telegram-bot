import { getStore } from "@netlify/blobs";
import { createCipheriv, createDecipheriv, createHmac, createHash, timingSafeEqual } from "node:crypto";

import type {
  BotConfig,
  EncryptedValue,
  HealthState,
  MonitorState,
  SubscriberState,
} from "./types.js";

declare const Netlify: { env: { get(name: string): string | undefined } };

const STORE_NAME = "stock-watch-bot";
const CONFIG_KEY = "config.json";
const SUBSCRIBERS_KEY = "subscribers.json";
const MONITOR_KEY = "monitor.json";
const HEALTH_KEY = "health.json";

function store() {
  return getStore(STORE_NAME, { consistency: "strong" });
}

export function requiredNetlifyEnv(name: string): string {
  const value = Netlify.env.get(name)?.trim();
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}

function appKey(): Buffer {
  const key = Buffer.from(requiredNetlifyEnv("APP_ENCRYPTION_KEY"), "base64");
  if (key.length !== 32) throw new Error("APP_ENCRYPTION_KEY must contain 32 bytes");
  return key;
}

export function encryptSecret(value: string): EncryptedValue {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const cipher = createCipheriv("aes-256-gcm", appKey(), iv);
  const ciphertext = Buffer.concat([cipher.update(value, "utf8"), cipher.final()]);
  return {
    iv: Buffer.from(iv).toString("base64"),
    ciphertext: ciphertext.toString("base64"),
    tag: cipher.getAuthTag().toString("base64"),
  };
}

export function decryptSecret(value: EncryptedValue): string {
  const decipher = createDecipheriv("aes-256-gcm", appKey(), Buffer.from(value.iv, "base64"));
  decipher.setAuthTag(Buffer.from(value.tag, "base64"));
  return Buffer.concat([
    decipher.update(Buffer.from(value.ciphertext, "base64")),
    decipher.final(),
  ]).toString("utf8");
}

export function decryptLegacyFernet(token: string, encodedState: string): Record<string, unknown> {
  const key = createHash("sha256").update(token, "utf8").digest();
  const signingKey = key.subarray(0, 16);
  const encryptionKey = key.subarray(16, 32);
  const payload = Buffer.from(encodedState.trim(), "base64url");
  if (payload.length < 57 || payload[0] !== 0x80) throw new Error("Invalid legacy state");

  const signed = payload.subarray(0, payload.length - 32);
  const actualMac = payload.subarray(payload.length - 32);
  const expectedMac = createHmac("sha256", signingKey).update(signed).digest();
  if (!timingSafeEqual(actualMac, expectedMac)) throw new Error("Legacy state authentication failed");

  const iv = payload.subarray(9, 25);
  const ciphertext = payload.subarray(25, payload.length - 32);
  const decipher = createDecipheriv("aes-128-cbc", encryptionKey, iv);
  const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString("utf8");
  const state = JSON.parse(plaintext) as Record<string, unknown>;
  if (!state || typeof state !== "object") throw new Error("Invalid legacy state payload");
  return state;
}

export async function loadConfig(): Promise<{ config: BotConfig; token: string }> {
  const config = await store().get(CONFIG_KEY, { type: "json" }) as BotConfig | null;
  if (!config) throw new Error("Bot configuration has not been initialized");
  return { config, token: decryptSecret(config.encrypted_token) };
}

export async function saveConfig(config: BotConfig): Promise<void> {
  await store().setJSON(CONFIG_KEY, config);
}

export async function hasConfig(): Promise<boolean> {
  return (await store().get(CONFIG_KEY, { type: "json" })) !== null;
}

export async function loadSubscribers(): Promise<SubscriberState> {
  return (await store().get(SUBSCRIBERS_KEY, { type: "json" }) as SubscriberState | null) ?? {
    admin_chat_id: "",
    subscribers: {},
  };
}

export async function saveSubscribers(state: SubscriberState): Promise<void> {
  await store().setJSON(SUBSCRIBERS_KEY, state);
}

export async function loadMonitor(localDate: string): Promise<MonitorState> {
  const existing = await store().get(MONITOR_KEY, { type: "json" }) as MonitorState | null;
  if (!existing || existing.local_date !== localDate) {
    return {
      local_date: localDate,
      reports_sent: [],
      alerts: {},
      last_successful_market_check: existing?.last_successful_market_check,
    };
  }
  return existing;
}

export async function saveMonitor(state: MonitorState): Promise<void> {
  await store().setJSON(MONITOR_KEY, state);
}

export async function loadHealth(): Promise<HealthState> {
  return (await store().get(HEALTH_KEY, { type: "json" }) as HealthState | null) ?? {
    initialized: false,
  };
}

export async function saveHealth(state: HealthState): Promise<void> {
  await store().setJSON(HEALTH_KEY, state);
}
