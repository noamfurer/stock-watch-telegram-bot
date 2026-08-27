export interface Stock {
  name: string;
  symbol: string;
  exchange: "TASE" | "NASDAQ" | "NYSE";
  currency_symbol: string;
  display_symbol: string;
}

export interface Quote {
  stock: Stock;
  price: number;
  change: number;
  changePercent: number;
}

export interface Subscriber {
  chat_id: string;
  username: string;
  first_name: string;
  last_name: string;
  enabled: boolean;
  joined_at: string;
}

export interface SubscriberState {
  admin_chat_id: string;
  subscribers: Record<string, Subscriber>;
}

export interface MonitorState {
  local_date: string;
  reports_sent: string[];
  alerts: Record<string, "up" | "down">;
  last_successful_market_check?: string;
}

export interface EncryptedValue {
  iv: string;
  ciphertext: string;
  tag: string;
}

export interface BotConfig {
  version: 1;
  encrypted_token: EncryptedValue;
  watchlist: Stock[];
  threshold: number;
  migrated_at: string;
}

export interface HealthState {
  initialized: boolean;
  webhook_configured_at?: string;
  last_primary_success?: string;
  last_backup_success?: string;
  last_market_check?: string;
  last_error?: string;
}
