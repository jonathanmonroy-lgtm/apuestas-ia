import "server-only";

import { connection } from "next/server";

/**
 * Server-side client for our FastAPI backend. Only Server Components / Server Actions
 * call it: the browser never talks to the backend or to any data provider directly,
 * and the backend itself only serves what its worker stored (no provider calls).
 */

export const API_BASE_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  await connection(); // always request-time: these are live user/market data
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(503, `No se puede conectar con la API en ${API_BASE_URL}. ¿Está arrancada?`);
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail.map((d: { msg: string }) => d.msg).join("; ")
          : `HTTP ${res.status}`;
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

// --- types (mirror the FastAPI response models; money/odds are decimal strings) ---

export type Team = { id: number; name: string };

export type EventOut = {
  id: number;
  competition_id: number;
  competition: string;
  country: string | null;
  country_code: string | null;
  round: string | null;
  starts_at: string;
  kickoff_confirmed: boolean;
  status: "scheduled" | "live" | "finished" | "postponed" | "cancelled" | "unknown";
  home: Team;
  away: Team;
  result: { home: number; away: number } | null;
  odds: {
    kind: "analysis";
    method: string;
    market: "h2h";
    source: string;
    median_price: { home: string; draw: string; away: string };
    n_bookmakers: number;
    as_of: string;
    oldest_quote_at: string;
    is_stale: boolean;
  } | null;
  implied_probability: {
    kind: "analysis";
    method: string;
    home: string;
    draw: string;
    away: string;
    overround: string;
  } | null;
  can_fetch_odds: boolean;
};

export type EventsResponse = {
  data_as_of: { fixtures: string | null; odds: string | null };
  events: EventOut[];
};

export type Country = {
  name: string;
  code: string | null;
  n_events: number;
  n_with_odds: number;
  leagues: string[];
};

export type CountriesResponse = {
  day: string;
  timezone: string;
  source: "espn" | "api_football";
  browse_days: string[]; // local (Colombia) days the source can have data for

  data_as_of: { fixtures: string | null; odds: string | null };
  countries: Country[];
};

export type FetchOddsResult = { status: string; snapshots_inserted: number; message: string };

export type NewsItem = {
  id: number;
  kind: "news";
  title: string;
  summary: string | null;
  url: string;
  publisher: string | null;
  published_at: string;
  teams: Team[];
  teams_assignment: "inferred_by_rules";
};

export type NewsResponse = {
  data_as_of: { checked_at: string | null; newest_article_at: string | null; provider_delay_hours: number };
  notice: string;
  items: NewsItem[];
};

export type BetStatus = "pending" | "won" | "lost" | "void";
export type Selection = "home" | "draw" | "away";

export type Bet = {
  id: number;
  event_id: number | null;
  event_label: string;
  event_starts_at: string | null;
  market: "h2h";
  selection: Selection;
  odds_taken: string;
  stake: string;
  currency: string;
  status: BetStatus;
  payout: string | null;
  profit: string | null;
  placed_at: string;
  settled_at: string | null;
  notes: string | null;
};

export type BetList = { total: number; items: Bet[] };

export type Metrics = {
  kind: "user_records";
  currency: string;
  definitions: Record<string, string>;
  n_bets: number;
  n_pending: number;
  n_won: number;
  n_lost: number;
  n_void: number;
  staked: string;
  returned: string;
  profit: string;
  roi: string | null;
  win_rate: string | null;
  avg_stake: string | null;
  pending_stake: string;
  starting_bankroll: string | null;
  bankroll: string | null;
  available: string | null;
  series: {
    bet_id: number;
    settled_at: string;
    profit: string;
    cumulative_profit: string;
    bankroll: string | null;
  }[];
};

export type Bankroll = { starting_bankroll: string | null; currency: string | null };

export type LimitPeriod = "daily" | "weekly" | "monthly";

export type Limits = {
  currency: string;
  cooling_off_hours: number;
  note: string;
  limits: {
    period: LimitPeriod;
    amount: string;
    currency: string;
    used: string;
    remaining: string;
    period_ends_at: string;
    pending_amount: string | null;
    pending_effective_from: string | null;
  }[];
};

export type SelfExclusion = { active: boolean; started_at: string | null; ends_at: string | null };

// --- calls ---

const qs = (params: Record<string, string | number | undefined | null>) => {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return entries.length ? `?${new URLSearchParams(entries.map(([k, v]) => [k, String(v)]))}` : "";
};

export const api = {
  events: (
    p: { date_from?: string; date_to?: string; matchday?: number; day?: string; country?: string } = {},
  ) => request<EventsResponse>(`/api/events${qs(p)}`),
  countries: (day?: string) => request<CountriesResponse>(`/api/countries${qs({ day })}`),
  fetchOdds: (eventId: number) =>
    request<FetchOddsResult>(`/api/events/${eventId}/fetch-odds`, { method: "POST" }),
  news: (p: { participant_id?: number; event_id?: number; limit?: number } = {}) =>
    request<NewsResponse>(`/api/news${qs(p)}`),
  bets: (p: { status?: string; date_from?: string; date_to?: string; limit?: number } = {}) =>
    request<BetList>(`/api/bets${qs(p)}`),
  metrics: (currency?: string) => request<Metrics>(`/api/bets-metrics${qs({ currency })}`),
  bankroll: () => request<Bankroll>("/api/me/bankroll"),
  createBet: (body: object) =>
    request<Bet>("/api/bets", { method: "POST", body: JSON.stringify(body) }),
  updateBet: (id: number, body: object) =>
    request<Bet>(`/api/bets/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteBet: (id: number) => request<void>(`/api/bets/${id}`, { method: "DELETE" }),
  setBankroll: (body: { amount: string; currency: string }) =>
    request<Bankroll>("/api/me/bankroll", { method: "PUT", body: JSON.stringify(body) }),
  limits: (currency?: string) => request<Limits>(`/api/me/limits${qs({ currency })}`),
  setLimit: (body: { period: LimitPeriod; amount: string; currency: string }) =>
    request<Limits>("/api/me/limits", { method: "PUT", body: JSON.stringify(body) }),
  selfExclusion: () => request<SelfExclusion>("/api/me/self-exclusion"),
  startSelfExclusion: (days: number | null) =>
    request<SelfExclusion>("/api/me/self-exclusion", {
      method: "POST",
      body: JSON.stringify({ days }),
    }),
};
