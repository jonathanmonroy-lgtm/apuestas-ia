import type { Metadata } from "next";

import { BetForm, SettleControls } from "@/components/forms";
import { Badge, PageHeader, toneOf } from "@/components/ui";
import { api, type Bet } from "@/lib/api";
import { formatDate, formatDateTime, formatTime, money, odds, signedMoney } from "@/lib/format";

export const metadata: Metadata = { title: "Historial" };

const STATUS = {
  pending: "Pendiente",
  won: "Ganada",
  lost: "Perdida",
  void: "Anulada",
} as const;
const SELECTION = { home: "1", draw: "X", away: "2" } as const;

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

export default async function HistorialPage({ searchParams }: PageProps<"/historial">) {
  const params = await searchParams;
  const estado = one(params.estado);
  const desde = one(params.desde);
  const hasta = one(params.hasta);
  const evento = Number(one(params.evento)) || undefined;

  const [bets, events, exclusion] = await Promise.all([
    api.bets({
      status: estado && estado in STATUS ? estado : undefined,
      date_from: desde,
      date_to: hasta,
      limit: 200,
    }),
    api.events(),
    api.selfExclusion(),
  ]);
  const options = events.events
    .filter((e) => e.status === "scheduled")
    .map((e) => ({
      id: e.id,
      label: `${formatDate(e.starts_at)} ${e.kickoff_confirmed ? formatTime(e.starts_at) : "(hora por confirmar)"} · ${e.home.name} – ${e.away.name}`,
    }));

  return (
    <main className="page">
      <PageHeader title="Historial">
        <Badge kind="tuyo" />
      </PageHeader>

      <section className="card" aria-labelledby="nueva">
        <h2 id="nueva">Registrar una apuesta</h2>
        {exclusion.active ? (
          <p className="form-error">Tienes una autoexclusión activa: no puedes registrar apuestas.</p>
        ) : (
          <BetForm events={options} defaultEventId={evento} />
        )}
      </section>

      <form className="filters" method="get" aria-label="Filtros">
        <label>
          Estado
          <select name="estado" defaultValue={estado ?? ""}>
            <option value="">Todos</option>
            {Object.entries(STATUS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label>
          Desde
          <input type="date" name="desde" defaultValue={desde} />
        </label>
        <label>
          Hasta
          <input type="date" name="hasta" defaultValue={hasta} />
        </label>
        <button type="submit">Filtrar</button>
        <span className="muted small">{bets.total} apuestas</span>
      </form>

      {bets.items.length === 0 ? (
        <p className="muted">No hay apuestas con estos filtros.</p>
      ) : (
        <div className="table-wrap">
          <table className="bets">
            <thead>
              <tr>
                <th>Fecha</th>
                <th>Partido</th>
                <th>Sel.</th>
                <th className="num">Cuota</th>
                <th className="num">Importe</th>
                <th>Estado</th>
                <th className="num">Resultado</th>
                <th>
                  <span className="visually-hidden">Acciones</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {bets.items.map((b) => (
                <BetRow key={b.id} bet={b} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

function BetRow({ bet: b }: { bet: Bet }) {
  const tone = toneOf(b.profit);
  return (
    <tr>
      <td>{formatDateTime(b.placed_at)}</td>
      <td>
        {b.event_label}
        {b.notes && <div className="muted small">{b.notes}</div>}
      </td>
      <td>{SELECTION[b.selection]}</td>
      <td className="num">{odds(b.odds_taken)}</td>
      <td className="num">{money(b.stake, b.currency)}</td>
      <td>
        <span className={`pill pill-${b.status}`}>{STATUS[b.status]}</span>
      </td>
      <td className={`num${tone ? ` tone-${tone}` : ""}`}>
        {b.profit == null ? "—" : (
          <>
            {tone === "positive" && <span aria-hidden>▲ </span>}
            {tone === "negative" && <span aria-hidden>▼ </span>}
            {signedMoney(b.profit, b.currency)}
          </>
        )}
      </td>
      <td>
        <SettleControls id={b.id} status={b.status} />
      </td>
    </tr>
  );
}
