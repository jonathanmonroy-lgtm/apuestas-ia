import type { Metadata } from "next";
import Link from "next/link";

import { FetchOddsButton } from "@/components/FetchOddsButton";
import { Badge, Freshness, PageHeader } from "@/components/ui";
import { api, type CountriesResponse, type EventOut, type Team } from "@/lib/api";
import { countryFlagUrl, countryName } from "@/lib/countries";
import { dayKey, formatTime, odds, percent } from "@/lib/format";

export const metadata: Metadata = { title: "Partidos del día" };

// Old links (?dia=ayer) keep working; new ones carry the date (?dia=2026-10-18).
const LEGACY_DAYS: Record<string, number> = { ayer: -1, hoy: 0, manana: 1 };
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

const SOURCE_NOTE: Record<CountriesResponse["source"], string> = {
  espn: "Fuente: ESPN (no oficial), actualizada cada 30 min (ayer–mañana) y cada 3 h el resto",
  api_football: "Fuente: API-Football (plan gratuito: sólo ayer, hoy y mañana), cada 6 h",
};

const weekdayFmt = new Intl.DateTimeFormat("es-ES", { timeZone: "UTC", weekday: "short", day: "numeric" });

const STATUS_ES: Record<EventOut["status"], string> = {
  scheduled: "",
  live: "En vivo",
  finished: "Final",
  postponed: "Aplazado",
  cancelled: "Cancelado",
  unknown: "Estado desconocido",
};

// ?ver= filters over the selected country's matches.
const FILTERS = {
  todos: { label: "Todos", keep: () => true },
  vivo: { label: "En vivo", keep: (e: EventOut) => e.status === "live" },
  porjugar: { label: "Por jugar", keep: (e: EventOut) => e.status === "scheduled" },
  cuotas: { label: "Con cuotas", keep: (e: EventOut) => e.odds !== null },
} as const;
type FilterKey = keyof typeof FILTERS;

/** Colombia's calendar day `offset` days from today, as YYYY-MM-DD. */
function colombiaDay(offset: number): string {
  return dayKey(new Date(Date.now() + offset * 86_400_000).toISOString());
}

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

/** A real calendar date (2026-02-30 is not: Date rolls it to March) within a year of today:
 * anything else falls back to today instead of a 422/500 from the API. */
function isUsableDay(day: string, today: string): boolean {
  if (!ISO_DAY.test(day)) return false;
  const t = Date.parse(`${day}T00:00:00Z`);
  if (Number.isNaN(t) || new Date(t).toISOString().slice(0, 10) !== day) return false;
  return Math.abs(t - Date.parse(`${today}T00:00:00Z`)) <= 366 * 86_400_000;
}

/** "Hoy", "Ayer", "Mañana" or "sáb 18" for a YYYY-MM-DD Colombian day. */
function dayLabel(day: string, today: string): string {
  const diff = Math.round((Date.parse(day) - Date.parse(today)) / 86_400_000);
  if (diff === 0) return "Hoy";
  if (diff === -1) return "Ayer";
  if (diff === 1) return "Mañana";
  return weekdayFmt.format(new Date(`${day}T12:00:00Z`));
}

export default async function PartidosDelDiaPage({ searchParams }: PageProps<"/">) {
  const params = await searchParams;
  const today = colombiaDay(0);
  const dia = one(params.dia) ?? "";
  // hasOwn: "?dia=constructor" must not reach Object.prototype.
  const legacy = Object.hasOwn(LEGACY_DAYS, dia) ? LEGACY_DAYS[dia] : 0;
  const day = isUsableDay(dia, today) ? dia : colombiaDay(legacy);
  const verParam = one(params.ver) ?? "";
  const ver: FilterKey = Object.hasOwn(FILTERS, verParam) ? (verParam as FilterKey) : "todos";

  const menu = await api.countries(day);
  // The API sorts by the provider's English name; the menu reads in Spanish.
  const countries = [...menu.countries].sort(
    (a, b) =>
      Number(a.name === "World") - Number(b.name === "World") ||
      countryName(a.name, a.code).localeCompare(countryName(b.name, b.code), "es"),
  );
  const days = menu.browse_days.includes(day) ? menu.browse_days : [...menu.browse_days, day].sort();
  const totals = countries.reduce(
    (t, c) => ({ n: t.n + c.n_events, live: t.live + c.n_live, odds: t.odds + c.n_with_odds }),
    { n: 0, live: 0, odds: 0 },
  );

  const requested = one(params.pais);
  const selected =
    countries.find((c) => c.name === requested) ??
    countries.find((c) => c.name === "Colombia") ??
    countries[0];
  const data = selected ? await api.events({ day, country: selected.name }) : null;
  const shown = (data?.events ?? []).filter(FILTERS[ver].keep);

  const byLeague = new Map<string, EventOut[]>();
  for (const e of shown) byLeague.set(e.competition, [...(byLeague.get(e.competition) ?? []), e]);
  const href = (p: { pais?: string; dia?: string; ver?: FilterKey }) => {
    const q = new URLSearchParams({ dia: p.dia ?? day });
    const pais = p.pais ?? selected?.name;
    if (pais) q.set("pais", pais);
    const v = p.ver ?? ver;
    if (v !== "todos") q.set("ver", v);
    return `/?${q}`;
  };

  return (
    <main className="page">
      <PageHeader title="Partidos del día">
        <Freshness label="Partidos" iso={menu.data_as_of.fixtures} />
        <Freshness label="Cuotas" iso={menu.data_as_of.odds} />
      </PageHeader>

      <nav className="day-strip" aria-label="Día">
        {days.map((d) => (
          <Link key={d} href={href({ dia: d })} className="chip"
                aria-current={d === day ? "true" : undefined}>
            {dayLabel(d, today)}
          </Link>
        ))}
      </nav>

      <div className="stat-cards" aria-label="Resumen del día">
        <StatCard value={totals.n} label="partidos" />
        <StatCard value={countries.length} label="países" />
        <StatCard value={totals.live} label="en vivo" live={totals.live > 0} />
        <StatCard value={totals.odds} label="con cuotas" />
      </div>

      {countries.length === 0 ? (
        <p className="muted">
          No hay partidos guardados para este día. Arranca el worker (
          <code>uv run python -m app.worker</code>) o sincroniza a mano:{" "}
          <code>{menu.source === "espn" ? "uv run python -m app.jobs.sync_espn --wide" : "uv run python -m app.jobs.sync_day"}</code>.
        </p>
      ) : (
        <div className="day-layout">
          <aside className="country-list" aria-label="Países">
            <ul>
              {countries.map((c) => (
                <li key={c.name}>
                  <Link href={href({ pais: c.name })}
                        aria-current={c.name === selected?.name ? "true" : undefined}>
                    <Flag name={c.name} code={c.code} />
                    <span className="country-name">{countryName(c.name, c.code)}</span>
                    {c.n_live > 0 && <span className="live-dot" title={`${c.n_live} en vivo`} />}
                    <span className="country-count" title={`${c.n_with_odds} con cuotas`}>
                      {c.n_events}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </aside>

          <section className="day-matches" aria-label="Partidos">
            {selected && (
              <h2 className="country-title">
                <Flag name={selected.name} code={selected.code} />{" "}
                {countryName(selected.name, selected.code)}
                <span className="muted small">
                  {" "}· {selected.n_events} partidos · {selected.n_with_odds} con cuotas
                </span>
              </h2>
            )}
            <nav className="filter-chips" aria-label="Filtrar partidos">
              {(Object.keys(FILTERS) as FilterKey[]).map((k) => (
                <Link key={k} href={href({ ver: k })} className="chip"
                      aria-current={k === ver ? "true" : undefined}>
                  {FILTERS[k].label}
                </Link>
              ))}
            </nav>
            <p className="explainer">
              <Badge kind="dato" /> hora, estado y resultado · <Badge kind="analisis" /> cuota
              prepartido y probabilidad implícita (con «1 casa», la de esa casa sin su margen):
              no es un pronóstico.
            </p>
            {shown.length === 0 && (
              <p className="muted">Ningún partido con este filtro.</p>
            )}
            {[...byLeague.entries()].map(([league, events]) => (
              <div key={league} className="league-block">
                <h3>{league}</h3>
                <ul className="match-list">
                  {events.map((e) => (
                    <MatchRow key={e.id} e={e} />
                  ))}
                </ul>
              </div>
            ))}
            <p className="muted small source-note">
              {day} · hora de Colombia ({menu.timezone}) · {SOURCE_NOTE[menu.source]}.
            </p>
          </section>
        </div>
      )}
    </main>
  );
}

function StatCard({ value, label, live = false }: { value: number; label: string; live?: boolean }) {
  return (
    <div className="stat-card">
      <strong>
        {live && <span className="live-dot" aria-hidden />}
        {value}
      </strong>
      <span>{label}</span>
    </div>
  );
}

function Crest({ team }: { team: Team }) {
  return team.crest_id ? (
    // eslint-disable-next-line @next/next/no-img-element -- tiny cached PNG from our /logos route
    <img className="crest" src={`/logos/${team.crest_id}.png`} alt="" width={28} height={28}
         loading="lazy" />
  ) : (
    <span className="crest crest-initial" aria-hidden>{team.name.slice(0, 1)}</span>
  );
}

function MatchRow({ e }: { e: EventOut }) {
  const status = STATUS_ES[e.status];
  const p = e.implied_probability;
  return (
    <li className={`match-row${e.status === "live" ? " is-live" : ""}`}>
      <div className="match-time">
        {e.kickoff_confirmed ? (
          <time dateTime={e.starts_at}>{formatTime(e.starts_at)}</time>
        ) : (
          <em title="El proveedor no ha confirmado la hora">por confirmar</em>
        )}
        {status && (
          <span className={`match-status status-${e.status}`}>
            {e.status === "live" && <span className="live-dot" aria-hidden />}
            {status}
          </span>
        )}
      </div>
      <div className="match-teams">
        <span className="team team-home">
          <span className="team-name">{e.home.name}</span>
          <Crest team={e.home} />
        </span>
        {e.result ? (
          <strong className="score">
            {e.result.home} – {e.result.away}
          </strong>
        ) : (
          <span className="muted vs">vs</span>
        )}
        <span className="team team-away">
          <Crest team={e.away} />
          <span className="team-name">{e.away.name}</span>
        </span>
      </div>
      <div className="match-odds">
        {e.odds && p ? (
          <div className="odds-block" title={`${e.odds.method}\n\n${p.method}`}>
            <div className="odds-inline">
              {(["home", "draw", "away"] as const).map((k, i) => (
                <span key={k} className="odd-cell">
                  <small className="muted">{["1", "X", "2"][i]}</small>
                  <strong>{odds(e.odds!.median_price[k])}</strong>
                  <small className="muted">{percent(p[k])}</small>
                </span>
              ))}
            </div>
            <ProbabilityBar home={p.home} draw={p.draw} away={p.away} />
            <small className="muted odds-meta">
              {e.odds.n_bookmakers === 1 ? "1 casa" : `${e.odds.n_bookmakers} casas`}
              {e.odds.is_stale ? " · desactualizadas" : ""}
            </small>
          </div>
        ) : e.can_fetch_odds ? (
          <FetchOddsButton eventId={e.id} />
        ) : (
          <span className="muted small">sin cuotas</span>
        )}
      </div>
    </li>
  );
}

/** Implied probabilities as one bar (1 · X · 2). Widths only: the numbers are above. */
function ProbabilityBar({ home, draw, away }: { home: string; draw: string; away: string }) {
  // Exact decimals from the API; a CSS width is display only (the figures are printed).
  const pct = (v: string) => `${(Number(v) * 100).toFixed(1)}%`;
  return (
    <div className="prob-bar" aria-hidden>
      <span className="prob-home" style={{ width: pct(home) }} />
      <span className="prob-draw" style={{ width: pct(draw) }} />
      <span className="prob-away" style={{ width: pct(away) }} />
    </div>
  );
}

function Flag({ name, code }: { name: string; code: string | null }) {
  const url = countryFlagUrl(name, code);
  return url ? (
    // eslint-disable-next-line @next/next/no-img-element -- tiny cached SVG from our /flags route
    <img className="flag" src={url} alt="" width={20} height={14} loading="lazy" />
  ) : (
    <span className="flag flag-world" aria-hidden>
      ◎
    </span>
  );
}
