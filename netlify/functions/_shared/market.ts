import { createHash } from "node:crypto";

import type { Quote, Stock } from "./types.js";

function privateId(stock: Stock): string {
  return createHash("sha256")
    .update(`${stock.exchange}:${stock.symbol}`, "utf8")
    .digest("hex")
    .slice(0, 20);
}

function providerSymbol(stock: Stock): string {
  return `${stock.exchange}:${stock.symbol}`;
}

async function tradingViewQuotes(stocks: Stock[]): Promise<Map<string, Quote>> {
  const found = new Map<string, Quote>();
  const groups: Array<[string, Stock[]]> = [
    ["israel", stocks.filter((stock) => stock.exchange === "TASE")],
    ["america", stocks.filter((stock) => stock.exchange !== "TASE")],
  ];

  await Promise.all(groups.map(async ([market, group]) => {
    if (group.length === 0) return;
    try {
      const response = await fetch(`https://scanner.tradingview.com/${market}/scan`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "user-agent": "Mozilla/5.0 stock-monitor/2.0",
        },
        body: JSON.stringify({
          symbols: { tickers: group.map(providerSymbol), query: { types: [] } },
          columns: ["close", "change_abs", "change"],
        }),
        signal: AbortSignal.timeout(8_000),
      });
      if (!response.ok) return;
      const payload = await response.json() as { data?: Array<{ s?: string; d?: unknown[] }> };
      const byProvider = new Map(group.map((stock) => [providerSymbol(stock), stock]));
      for (const row of payload.data ?? []) {
        const stock = row.s ? byProvider.get(row.s) : undefined;
        const values = row.d ?? [];
        const price = Number(values[0]);
        const change = Number(values[1]);
        const changePercent = Number(values[2]);
        if (!stock || !Number.isFinite(price) || !Number.isFinite(change) || !Number.isFinite(changePercent)) continue;
        found.set(privateId(stock), { stock, price, change, changePercent });
      }
    } catch {
      // Yahoo is used below for any missing symbol.
    }
  }));
  return found;
}

async function yahooQuote(stock: Stock): Promise<Quote | null> {
  const yahooSymbol = stock.exchange === "TASE" ? `${stock.symbol}.TA` : stock.symbol;
  try {
    const url = new URL(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(yahooSymbol)}`);
    url.searchParams.set("interval", "1m");
    url.searchParams.set("range", "1d");
    const response = await fetch(url, {
      headers: { "user-agent": "Mozilla/5.0 stock-monitor/2.0" },
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok) return null;
    const payload = await response.json() as {
      chart?: { result?: Array<{ meta?: Record<string, unknown> }> };
    };
    const meta = payload.chart?.result?.[0]?.meta;
    if (!meta) return null;
    const price = Number(meta.regularMarketPrice);
    const previous = Number(meta.chartPreviousClose ?? meta.previousClose);
    if (!Number.isFinite(price) || !Number.isFinite(previous) || previous === 0) return null;
    const change = price - previous;
    return { stock, price, change, changePercent: change / previous * 100 };
  } catch {
    return null;
  }
}

export async function fetchQuotes(stocks: Stock[]): Promise<Quote[]> {
  const found = await tradingViewQuotes(stocks);
  const missing = stocks.filter((stock) => !found.has(privateId(stock)));
  const fallbacks = await Promise.all(missing.map(yahooQuote));
  for (const quote of fallbacks) {
    if (quote) found.set(privateId(quote.stock), quote);
  }
  return stocks.flatMap((stock) => {
    const quote = found.get(privateId(stock));
    return quote ? [quote] : [];
  });
}

export function quotePrivateId(stock: Stock): string {
  return privateId(stock);
}
