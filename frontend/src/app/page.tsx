import type { Metadata } from "next";
import Link from "next/link";

import { FetchOddsButton } from "@/components/FetchOddsButton";
import { Badge, Freshness, PageHeader } from "@/components/ui";
import { api, type CountriesResponse, type EventOut } from "@/lib/api";
import { countryFlagUrl, countryName } from "@/lib/countries";
import { dayKey, formatTime, odds, percent } from "@/lib/format";

export const metadata: Metadata = { title: "Partidos del día" };

// Old links (?dia=ayer) keep working; new ones carry the date (?dia=2026-10-18).
const LEGACY_DAYS: Record<string, number> = { ayer: -1, hoy: 0, manana: 1 };
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

const SOURCE_NOTE: Record<CountriesResponse["source"], string> = {
  espn: "Fuente: ESPN (no oficial). El worker la sincroniza cada 30 min (ayer–mañana) y cada 6 h el resto de días",
  api_football: "Fuente: API-Football (plan gratuito: sólo ayer, hoy y mañana), cada 6 h",
};

const weekdayFmt = new Intl.DateTimeFormat("es-ES", { timeZone: "UTC", weekday: "short", day: "numeric" });

const STATUS_ES: Record<EventOut["status"], string> = {
  scheduled: "",
  live: "En juego",
  finished: "Final",
  postponed: "Aplazado",
  cancelled: "Cancelado",
  unknown: "Estado desconocido",
};

/** Colombia's calendar day `offset` days from today, as YYYY-MM-DD. */
function colombiaDay(offset: number): string {
  return dayKey(new Date(Date.now() + offset * 86_400_000).toISOString());
}

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

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
  const day = ISO_DAY.test(dia) && !Number.isNaN(Date.parse(dia)) ? dia : colombiaDay(legacy);
  const menu = await api.countries(day);
  // The API sorts by the provider's English name; the menu reads in Spanish.
  const countries = [...menu.countries].sort(
    (a, b) =>
      Number(a.name === "World") - Number(b.name === "World") ||
      countryName(a.name, a.code).localeCompare(countryName(b.name, b.code), "es"),
  );
  const days = menu.browse_days.includes(day) ? menu.browse_days : [...menu.browse_days, day].sort();

  const requested = one(params.pais);
  const selected =
    countries.find((c) => c.name === requested) ??
    countries.find((c) => c.name === "Colombia") ??
    countries[0];
  const data = selected ? await api.events({ day, country: selected.name }) : null;

  const byLeague = new Map<string, EventOut[]>();
  for (const e of data?.events ?? []) byLeague.set(e.competition, [...(byLeague.get(e.competition) ?? []), e]);
  const href = (pais: string | undefined, dia: string) =>
    `/?${new URLSearchParams({ dia, ...(pais ? { pais } : {}) })}`;

  return (
    <main className="page">
      <PageHeader title="Partidos del día">
        <Freshness label="Partidos" iso={menu.data_as_of.fixtures} />
        <Freshness label="Cuotas" iso={menu.data_as_of.odds} />
      </PageHeader>

      <nav className="day-strip" aria-label="Día">
        {days.map((d) => (
          <Link key={d} href={href(selected?.name, d)} className="chip"
                aria-current={d === day ? "true" : undefined}>
            {dayLabel(d, today)}
          </Link>
        ))}
      </nav>
      <p className="muted small">
        {day} · hora de Colombia ({menu.timezone}) · {SOURCE_NOTE[menu.source]}.
      </p>

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
                  <Link href={href(c.name, day)}
                        aria-current={c.name === selected?.name ? "true" : undefined}>
                    <Flag name={c.name} code={c.code} />
                    <span className="country-name">{countryName(c.name, c.code)}</span>
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
            <p className="explainer">
              <Badge kind="dato" /> hora, estado y resultado ·{" "}
              <Badge kind="analisis" /> cuota prepartido y su probabilidad implícita, no un
              pronóstico. Con varias casas es la mediana (sin exchanges); con «1 casa» es la
              cuota de esa única casa, no una cifra de mercado.
            </p>
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
          </section>
        </div>
      )}
    </main>
  );
}

function MatchRow({ e }: { e: EventOut }) {
  const status = STATUS_ES[e.status];
  return (
    <li className="match-row">
      <div className="match-time">
        {e.kickoff_confirmed ? (
          <time dateTime={e.starts_at}>{formatTime(e.starts_at)}</time>
        ) : (
          <em title="El proveedor no ha confirmado la hora">por confirmar</em>
        )}
        {status && <span className={`match-status status-${e.status}`}>{status}</span>}
      </div>
      <div className="match-teams">
        <span>{e.home.name}</span>
        {e.result ? (
          <strong className="score">
            {e.result.home} – {e.result.away}
          </strong>
        ) : (
          <span className="muted">vs</span>
        )}
        <span>{e.away.name}</span>
      </div>
      <div className="match-odds">
        {e.odds && e.implied_probability ? (
          <div className="odds-inline" title={e.odds.method}>
            {(["home", "draw", "away"] as const).map((k, i) => (
              <span key={k} className="odd-cell">
                <small className="muted">{["1", "X", "2"][i]}</small>
                <strong>{odds(e.odds!.median_price[k])}</strong>
                <small className="muted">{percent(e.implied_probability![k])}</small>
              </span>
            ))}
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
