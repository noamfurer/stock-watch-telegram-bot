import type { Stock } from "./types.js";

export function loadWatchlist(raw: string): Stock[] {
  const parsed = JSON.parse(raw) as unknown;
  if (!Array.isArray(parsed) || parsed.length === 0) throw new Error("WATCHLIST_JSON must be a non-empty array");
  const seen = new Set<string>();
  return parsed.map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("Invalid watchlist item");
    const value = item as Record<string, unknown>;
    const name = String(value.name ?? "").trim();
    const symbol = String(value.symbol ?? "").trim().toUpperCase();
    const exchange = String(value.exchange ?? "TASE").trim().toUpperCase();
    if (!name || !symbol || !["TASE", "NASDAQ", "NYSE"].includes(exchange)) throw new Error("Invalid watchlist item");
    const provider = `${exchange}:${symbol}`;
    if (seen.has(provider)) throw new Error("Duplicate watchlist symbol");
    seen.add(provider);
    return {
      name,
      symbol,
      exchange: exchange as Stock["exchange"],
      currency_symbol: String(value.currency_symbol ?? (exchange === "TASE" ? "₪" : "$")),
      display_symbol: String(value.display_symbol ?? symbol),
    };
  });
}
