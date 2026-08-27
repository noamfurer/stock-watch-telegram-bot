import { formatAlert, formatReport } from "./format.js";
import { fetchQuotes, quotePrivateId } from "./market.js";
import {
  loadConfig,
  loadHealth,
  loadMonitor,
  loadSubscribers,
  saveHealth,
  saveMonitor,
  saveSubscribers,
} from "./storage.js";
import { sendMessage } from "./telegram.js";
import { hasRecentSuccess, isMonitoringWindow, israelTime, israelTimestamp, reportSlotsDue } from "./time.js";
import type { MonitorState, Quote, SubscriberState } from "./types.js";

type RunKind = "primary" | "backup";

async function broadcast(token: string, subscribers: SubscriberState, text: string): Promise<boolean> {
  let changed = false;
  const active = Object.entries(subscribers.subscribers).filter(([, subscriber]) => subscriber.enabled);
  await Promise.all(active.map(async ([chatId, subscriber]) => {
    try {
      await sendMessage(token, chatId, text);
    } catch (error) {
      const status = Number((error as { status?: number }).status);
      if (status === 400 || status === 403) {
        subscriber.enabled = false;
        changed = true;
      }
    }
  }));
  return changed;
}

function alertDirection(percent: number, threshold: number): "up" | "down" | null {
  if (percent >= threshold) return "up";
  if (percent <= -threshold) return "down";
  return null;
}

async function processAlerts(
  token: string,
  subscribers: SubscriberState,
  quotes: Quote[],
  threshold: number,
  state: MonitorState,
  now: Date,
): Promise<boolean> {
  let subscriberChanged = false;
  const rearmLevel = Math.max(0, threshold - 0.25);
  for (const quote of quotes) {
    const id = quotePrivateId(quote.stock);
    const current = alertDirection(quote.changePercent, threshold);
    const previous = state.alerts[id];
    if (current && current !== previous) {
      subscriberChanged = await broadcast(token, subscribers, formatAlert(quote, threshold, now)) || subscriberChanged;
      state.alerts[id] = current;
    } else if (!current && previous && Math.abs(quote.changePercent) < rearmLevel) {
      delete state.alerts[id];
    }
  }
  return subscriberChanged;
}

export async function runMonitor(kind: RunKind): Promise<void> {
  const now = new Date();
  const localTimestamp = israelTimestamp(now);
  const health = await loadHealth();
  const runField = kind === "primary" ? "last_primary_success" : "last_backup_success";
  try {
    if (!health.initialized) return;
    if (!isMonitoringWindow(now)) {
      console.log(`${kind} monitor skipped outside Israel market window`, {
        israel_time: localTimestamp,
        utc_time: now.toISOString(),
      });
      await saveHealth({ ...health, [runField]: now.toISOString(), last_error: undefined });
      return;
    }

    const local = israelTime(now);
    console.log(`${kind} monitor started`, {
      israel_time: localTimestamp,
      utc_time: now.toISOString(),
    });
    const monitor = await loadMonitor(local.localDate);
    if (kind === "backup" && hasRecentSuccess(monitor.last_successful_market_check, now)) {
      await saveHealth({ ...health, last_backup_success: now.toISOString(), last_error: undefined });
      return;
    }

    const [{ config, token }, subscribers] = await Promise.all([loadConfig(), loadSubscribers()]);
    if (!Object.values(subscribers.subscribers).some((subscriber) => subscriber.enabled)) {
      await saveHealth({ ...health, [runField]: now.toISOString(), last_error: undefined });
      return;
    }

    const quotes = await fetchQuotes(config.watchlist);
    if (quotes.length === 0) throw new Error("No market data was returned");

    let subscriberChanged = false;
    const sent = new Set(monitor.reports_sent);
    for (const slot of reportSlotsDue(now, sent)) {
      subscriberChanged = await broadcast(token, subscribers, formatReport(quotes, now)) || subscriberChanged;
      sent.add(slot);
    }
    monitor.reports_sent = [...sent].sort();
    subscriberChanged = await processAlerts(token, subscribers, quotes, config.threshold, monitor, now) || subscriberChanged;
    monitor.last_successful_market_check = now.toISOString();

    await Promise.all([
      saveMonitor(monitor),
      subscriberChanged ? saveSubscribers(subscribers) : Promise.resolve(),
      saveHealth({
        ...health,
        [runField]: now.toISOString(),
        last_market_check: now.toISOString(),
        last_error: undefined,
      }),
    ]);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    await saveHealth({ ...health, last_error: `${kind}:${message}` });
    throw error;
  }
}
