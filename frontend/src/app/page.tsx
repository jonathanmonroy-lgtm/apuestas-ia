import type { Metadata } from "next";
import Link from "next/link";

import { FetchOddsButton } from "@/components/FetchOddsButton";
import { Badge, Freshness, PageHeader } from "@/components/ui";
import { api, type EventOut } from "@/lib/api";
import { countryFlagUrl, countryName } from "@/lib/countries";
import { dayKey, formatTime, odds, percent } from "@/lib/format";

export const metadata: Metadata = { title: "Partidos del día" };

const DAYS = [
  { key: "ayer", label: "Ayer", offset: -1 },
  { key: "hoy", label: "Hoy", offset: 0 },
  { key: "manana", label: "Mañana", offset: 1 },
] as const;

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

export default async function PartidosDelDiaPage({ searchParams }: PageProps<"/">) {
  const params = await searchParams;
  const dayParam = DAYS.find((d) => d.key === one(params.dia)) ?? DAYS[1];
  const day = colombiaDay(dayParam.offset);
  const menu = await api.countries(day);

  const requested = one(params.pais);
  const selected =
    menu.countries.find((c) => c.name === requested) ??
    menu.countries.find((c) => c.name === "Colombia") ??
    menu.countries[0];
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

      <nav className="filters" aria-label="Día">
        {DAYS.map((d) => (
          <Link key={d.key} href={href(selected?.name, d.key)} className="chip"
                aria-current={d.key === dayParam.key ? "true" : undefined}>
            {d.label}
          </Link>
        ))}
        <span className="muted small">
          {day} · hora de Colombia ({menu.timezone})
        </span>
      </nav>

      {menu.countries.length === 0 ? (
        <p className="muted">
          No hay partidos guardados para este día. El plan gratuito sólo da ayer, hoy y mañana;
          el worker los sincroniza cada 6 h (o a mano: <code>uv run python -m app.jobs.sync_day</code>).
        </p>
      ) : (
        <div className="day-layout">
          <aside className="country-list" aria-label="Países">
            <ul>
              {menu.countries.map((c) => (
                <li key={c.name}>
                  <Link href={href(c.name, dayParam.key)}
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
              <Badge kind="analisis" /> cuota = mediana de las casas (sin exchanges, sólo
              prepartido) y su probabilidad implícita, no un pronóstico.
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
              {e.odds.n_bookmakers} casas{e.odds.is_stale ? " · desactualizadas" : ""}
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
    // eslint-disable-next-line @next/next/no-img-element -- tiny external SVG, no optimisation needed
    <img className="flag" src={url} alt="" width={20} height={14} loading="lazy" />
  ) : (
    <span className="flag flag-world" aria-hidden>
      ◎
    </span>
  );
}
