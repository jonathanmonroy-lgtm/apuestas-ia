import type { Metadata } from "next";
import Link from "next/link";

import { Badge, Freshness, PageHeader } from "@/components/ui";
import { api, type EventOut, type NewsItem } from "@/lib/api";
import { ago, dayKey, formatDate, formatTime, odds, percent } from "@/lib/format";

export const metadata: Metadata = { title: "Programación" };

const RANGES = [7, 14, 30] as const;
const OUTCOMES = [
  { key: "home", label: "1" },
  { key: "draw", label: "X" },
  { key: "away", label: "2" },
] as const;

const STATUS_ES: Record<EventOut["status"], string> = {
  scheduled: "Programado",
  live: "En juego",
  finished: "Finalizado",
  postponed: "Aplazado",
  cancelled: "Cancelado",
  unknown: "Estado desconocido",
};

// LaLiga: 20 teams, double round robin = 38 matchdays.
const MATCHDAYS = Array.from({ length: 38 }, (_, i) => i + 1);

export default async function ProgramacionPage({ searchParams }: PageProps<"/programacion">) {
  const params = await searchParams;
  const jornadaParam = Number(Array.isArray(params.jornada) ? params.jornada[0] : params.jornada);
  const jornada = MATCHDAYS.includes(jornadaParam) ? jornadaParam : undefined;
  const days = RANGES.includes(Number(params.dias) as (typeof RANGES)[number]) ? Number(params.dias) : 14;
  const today = new Date();
  const to = new Date(today.getTime() + days * 86_400_000);
  const iso = (d: Date) => d.toISOString().slice(0, 10);

  const [data, news] = await Promise.all([
    jornada
      ? api.events({ matchday: jornada })
      : api.events({ date_from: iso(today), date_to: iso(to) }),
    api.news({ limit: 100 }),
  ]);

  const byDay = new Map<string, EventOut[]>();
  for (const e of data.events) byDay.set(dayKey(e.starts_at), [...(byDay.get(dayKey(e.starts_at)) ?? []), e]);
  const newsFor = (e: EventOut) =>
    news.items.filter((n) => n.teams.some((t) => t.id === e.home.id || t.id === e.away.id));
  const anyStale = data.events.some((e) => e.odds?.is_stale);

  return (
    <main className="page">
      <PageHeader title="Programación">
        <Freshness label="Calendario" iso={data.data_as_of.fixtures} />
        <Freshness label="Cuotas" iso={data.data_as_of.odds} stale={anyStale} />
      </PageHeader>

      <div className="filters">
        <nav className="filters-inline" aria-label="Rango de fechas">
          {RANGES.map((r) => (
            <Link
              key={r}
              href={`/programacion?dias=${r}`}
              aria-current={!jornada && r === days ? "true" : undefined}
              className="chip"
            >
              Próximos {r} días
            </Link>
          ))}
        </nav>
        <form method="get" className="filters-inline" aria-label="Ver una jornada">
          <label>
            Jornada
            <select name="jornada" defaultValue={jornada ?? ""}>
              <option value="" disabled>
                Elige…
              </option>
              {MATCHDAYS.map((j) => (
                <option key={j} value={j}>
                  Jornada {j}
                </option>
              ))}
            </select>
          </label>
          <button type="submit">Ver</button>
        </form>
        {jornada && (
          <nav className="filters-inline" aria-label="Jornada anterior y siguiente">
            {jornada > 1 && (
              <Link href={`/programacion?jornada=${jornada - 1}`} className="chip">
                ← Jornada {jornada - 1}
              </Link>
            )}
            {jornada < MATCHDAYS.length && (
              <Link href={`/programacion?jornada=${jornada + 1}`} className="chip">
                Jornada {jornada + 1} →
              </Link>
            )}
          </nav>
        )}
      </div>
      {jornada && (
        <h2 className="day-title">
          Jornada {jornada} · {data.events.length} partidos
        </h2>
      )}

      <p className="explainer">
        <Badge kind="analisis" /> Las cuotas son la <strong>mediana</strong> de
        las casas que ofrecen el partido (sin exchanges, sólo prepartido). La probabilidad implícita
        es lo que esas cuotas descuentan, quitado el margen de las casas: <strong>no es un
        pronóstico nuestro</strong>.
      </p>

      {data.events.length === 0 && (
        <p className="muted">{jornada ? `La jornada ${jornada} no tiene partidos en el calendario.` : "No hay partidos en este rango."}</p>
      )}

      {[...byDay.entries()].map(([day, events]) => (
        <section key={day} aria-label={formatDate(events[0].starts_at)}>
          <h2 className="day-title">{formatDate(events[0].starts_at)}</h2>
          <div className="event-grid">
            {events.map((e) => (
              <EventCard key={e.id} event={e} news={newsFor(e)} />
            ))}
          </div>
        </section>
      ))}
    </main>
  );
}

function EventCard({ event: e, news }: { event: EventOut; news: NewsItem[] }) {
  const open = e.status === "scheduled";
  return (
    <article className="event-card">
      <header>
        <div className="event-meta">
          <Badge kind="dato" title="Calendario: football-data.org" />
          <span>
            {e.kickoff_confirmed ? (
              <time dateTime={e.starts_at}>{formatTime(e.starts_at)}</time>
            ) : (
              <em title="El proveedor sólo ha fijado la fecha">hora por confirmar</em>
            )}
            {e.round ? ` · ${e.round}` : ""}
          </span>
          {e.status !== "scheduled" && <span className="status">{STATUS_ES[e.status]}</span>}
        </div>
        <h3>
          {e.home.name} <span className="muted">–</span> {e.away.name}
          {e.result && (
            <span className="score">
              {" "}
              {e.result.home}–{e.result.away}
            </span>
          )}
        </h3>
      </header>

      {e.odds && e.implied_probability ? (
        <div className="odds">
          <div className="odds-head">
            <Badge kind="analisis" title={e.odds.method} />
            <span className="muted small">
              mediana de {e.odds.n_bookmakers} casas · {ago(e.odds.as_of)}
            </span>
            {e.odds.is_stale && <strong className="stale">⚠ cuotas desactualizadas</strong>}
          </div>
          <table className="odds-table">
            <thead>
              <tr>
                {OUTCOMES.map((o) => (
                  <th key={o.key} scope="col">
                    {o.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                {OUTCOMES.map((o) => (
                  <td key={o.key} className="num">
                    {odds(e.odds!.median_price[o.key])}
                  </td>
                ))}
              </tr>
              <tr>
                {OUTCOMES.map((o) => {
                  const p = e.implied_probability![o.key];
                  return (
                    <td key={o.key}>
                      <div className="meter" title={e.implied_probability!.method}>
                        {/* width from the exact string ratio; display value is the text */}
                        <div className="meter-fill" style={{ width: `${Number(p) * 100}%` }} />
                      </div>
                      <span className="small">{percent(p)}</span>
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
          <p className="muted small">Margen de las casas sobre la mediana: {percent(e.implied_probability.overround)}</p>
        </div>
      ) : (
        <p className="muted small">Sin cuotas para este partido.</p>
      )}

      <footer className="event-foot">
        {news.length > 0 && (
          <details>
            <summary>
              {news.length} noticia{news.length > 1 ? "s" : ""} <Badge kind="inferido" title="Asignadas por reglas automáticas" />
            </summary>
            <ul className="news-list">
              {news.slice(0, 5).map((n) => (
                <li key={n.id}>
                  <a href={n.url} target="_blank" rel="noopener noreferrer">
                    {n.title}
                  </a>
                  <span className="muted small">
                    {n.publisher} · {ago(n.published_at)}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}
        {open && (
          <Link href={`/historial?evento=${e.id}`} className="btn-link">
            Registrar apuesta
          </Link>
        )}
      </footer>
    </article>
  );
}
