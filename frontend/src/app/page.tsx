import Link from "next/link";

import { EvolutionChart } from "@/components/EvolutionChart";
import { BankrollForm, LimitForm, SelfExclusionForm } from "@/components/forms";
import { Badge, Freshness, PageHeader, StatTile, toneOf } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, formatDate, formatDateTime, formatTime, money, percent, signedMoney } from "@/lib/format";

const CURRENCY = "EUR";
const PERIOD_ES = { daily: "Diario", weekly: "Semanal", monthly: "Mensual" } as const;

export default async function ResumenPage() {
  const [metrics, limits, exclusion, events, news] = await Promise.all([
    api.metrics(CURRENCY),
    api.limits(CURRENCY),
    api.selfExclusion(),
    api.events(),
    api.news({ limit: 5 }),
  ]);
  const m = metrics;
  const upcoming = events.events.filter((e) => e.status === "scheduled").slice(0, 4);
  const points = m.series.map((p) => ({
    id: p.bet_id,
    at: p.settled_at,
    value: p.bankroll ?? p.cumulative_profit,
    delta: p.profit,
  }));

  return (
    <main className="page">
      <PageHeader title="Resumen">
        <Badge kind="tuyo" title="Calculado sobre las apuestas que tú registras" />
      </PageHeader>

      <section aria-label="Tus métricas">
        <div className="kpis">
          <StatTile
            label="Bankroll"
            value={m.bankroll ? money(m.bankroll, CURRENCY) : "—"}
            hint={m.bankroll ? `inicial ${money(m.starting_bankroll, CURRENCY)}` : "fija tu bankroll inicial abajo"}
          />
          <StatTile
            label="Beneficio"
            value={signedMoney(m.profit, CURRENCY)}
            tone={toneOf(m.profit)}
            hint={`${m.n_won + m.n_lost} resueltas`}
          />
          <StatTile label="ROI" value={percent(m.roi)} tone={toneOf(m.roi)} hint={m.definitions.roi} />
          <StatTile label="Acierto" value={percent(m.win_rate)} hint={`${m.n_won} ganadas · ${m.n_lost} perdidas`} />
          <StatTile label="Stake medio" value={m.avg_stake ? money(m.avg_stake, CURRENCY) : "—"} hint={m.definitions.avg_stake} />
          <StatTile
            label="Pendiente"
            value={money(m.pending_stake, CURRENCY)}
            hint={m.available ? `disponible ${money(m.available, CURRENCY)}` : `${m.n_pending} apuestas abiertas`}
          />
        </div>
        {m.n_void > 0 && (
          <p className="muted small">
            {m.n_void} apuesta(s) anulada(s): devolvieron el importe y no cuentan en ROI, acierto ni
            stake medio.
          </p>
        )}
      </section>

      <section className="card" aria-labelledby="evo">
        <div className="card-head">
          <h2 id="evo">{m.bankroll ? "Evolución del bankroll" : "Beneficio acumulado"}</h2>
          <Badge kind="tuyo" />
        </div>
        <EvolutionChart
          points={points}
          currency={CURRENCY}
          label={m.bankroll ? "Bankroll" : "Beneficio acumulado"}
          baseline={m.starting_bankroll ?? "0"}
        />
        <BankrollForm current={m.starting_bankroll} currency={CURRENCY} />
      </section>

      <section className="card rg" aria-labelledby="rg">
        <div className="card-head">
          <h2 id="rg">Juego responsable</h2>
        </div>
        {exclusion.active ? (
          <p className="form-error" role="status">
            Autoexclusión activa
            {exclusion.ends_at ? ` hasta el ${formatDateTime(exclusion.ends_at)}` : " (indefinida)"}. No
            puedes registrar apuestas.
          </p>
        ) : null}
        {limits.limits.length === 0 ? (
          <p>
            No tienes límites de gasto. Te recomendamos fijar uno antes de registrar apuestas.
          </p>
        ) : (
          <ul className="limits">
            {limits.limits.map((l) => (
              <li key={l.period}>
                <strong>{PERIOD_ES[l.period]}</strong>: llevas {money(l.used, l.currency)} de{" "}
                {money(l.amount, l.currency)} · te quedan <strong>{money(l.remaining, l.currency)}</strong>
                {l.pending_amount && l.pending_effective_from && (
                  <span className="muted">
                    {" "}
                    (subida a {money(l.pending_amount, l.currency)} desde el{" "}
                    {formatDateTime(l.pending_effective_from)})
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        <p className="muted small">{limits.note}</p>
        <div className="rg-forms">
          <LimitForm currency={CURRENCY} />
          {!exclusion.active && <SelfExclusionForm />}
        </div>
        <p className="small">
          Si el juego te genera problemas, pide ayuda profesional. Los recursos de ayuda dependen
          del país y se mostrarán cuando se decida la jurisdicción.
        </p>
      </section>

      <div className="two-col">
        <section className="card" aria-labelledby="next">
          <div className="card-head">
            <h2 id="next">Próximos partidos</h2>
            <Freshness label="Calendario" iso={events.data_as_of.fixtures} />
          </div>
          <ul className="compact-list">
            {upcoming.map((e) => (
              <li key={e.id}>
                <span>
                  {e.home.name} – {e.away.name}
                </span>
                <span className="muted">
                  {formatDate(e.starts_at)} · {e.kickoff_confirmed ? formatTime(e.starts_at) : "hora por confirmar"}
                </span>
              </li>
            ))}
            {upcoming.length === 0 && <li className="muted">Sin partidos en los próximos 14 días.</li>}
          </ul>
          <Link href="/programacion">Ver programación con cuotas →</Link>
        </section>

        <section className="card" aria-labelledby="news">
          <div className="card-head">
            <h2 id="news">Últimas noticias</h2>
            <Badge kind="noticia" />
          </div>
          <ul className="news-list">
            {news.items.map((n) => (
              <li key={n.id}>
                <a href={n.url} target="_blank" rel="noopener noreferrer">
                  {n.title}
                </a>
                <span className="muted small">
                  {n.publisher} · {ago(n.published_at)} · {n.teams.map((t) => t.name).join(", ")}{" "}
                  <Badge kind="inferido" title="Equipos asignados por reglas automáticas" />
                </span>
              </li>
            ))}
          </ul>
          <p className="muted small">{news.notice}</p>
        </section>
      </div>
    </main>
  );
}
