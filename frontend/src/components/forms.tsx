"use client";

import { useActionState } from "react";

import {
  type ActionState,
  createBet,
  deleteBet,
  selfExclude,
  setBankroll,
  setLimit,
  settleBet,
} from "@/app/actions";

function Feedback({ state }: { state: ActionState }) {
  if (!state) return null;
  return (
    <p className={state.ok ? "form-ok" : "form-error"} role={state.ok ? "status" : "alert"}>
      {state.message}
    </p>
  );
}

export type EventOption = { id: number; label: string; odds?: { home: string; draw: string; away: string } };

export function BetForm({
  events,
  defaultEventId,
  defaultSelection,
}: {
  events: EventOption[];
  defaultEventId?: number;
  defaultSelection?: string;
}) {
  const [state, action, pending] = useActionState(createBet, null);
  return (
    <form action={action} className="form-grid">
      <label>
        Partido
        <select name="event_id" defaultValue={defaultEventId ?? ""}>
          <option value="">Otro (escríbelo abajo)</option>
          {events.map((e) => (
            <option key={e.id} value={e.id}>
              {e.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Otro evento
        <input name="event_label" placeholder="p. ej. Partido no listado" maxLength={200} />
      </label>
      <label>
        Selección (1X2)
        <select name="selection" defaultValue={defaultSelection ?? "home"} required>
          <option value="home">1 · Local</option>
          <option value="draw">X · Empate</option>
          <option value="away">2 · Visitante</option>
        </select>
      </label>
      <label>
        Cuota obtenida
        <input name="odds_taken" inputMode="decimal" placeholder="1,95" required pattern="[0-9]+([.,][0-9]{1,3})?" />
      </label>
      <label>
        Importe
        <input name="stake" inputMode="decimal" placeholder="10,00" required pattern="[0-9]+([.,][0-9]{1,2})?" />
      </label>
      <label>
        Moneda
        <select name="currency" defaultValue="EUR">
          <option>EUR</option>
          <option>USD</option>
          <option>GBP</option>
        </select>
      </label>
      <label className="span-2">
        Notas
        <input name="notes" maxLength={2000} />
      </label>
      <div className="form-actions span-2">
        <button type="submit" disabled={pending}>
          {pending ? "Guardando…" : "Registrar apuesta"}
        </button>
        <small className="muted">
          Registras una apuesta que ya hiciste en otro sitio. Esta app no acepta apuestas.
        </small>
      </div>
      <Feedback state={state} />
    </form>
  );
}

export function SettleControls({ id, status }: { id: number; status: string }) {
  return (
    <form className="row-actions">
      {status === "pending" ? (
        <>
          <button formAction={settleBet.bind(null, id, "won")} className="btn-small">
            Ganada
          </button>
          <button formAction={settleBet.bind(null, id, "lost")} className="btn-small">
            Perdida
          </button>
          <button formAction={settleBet.bind(null, id, "void")} className="btn-small">
            Anulada
          </button>
        </>
      ) : (
        <button formAction={settleBet.bind(null, id, "pending")} className="btn-small">
          Reabrir
        </button>
      )}
      <button
        formAction={deleteBet.bind(null, id)}
        className="btn-small btn-danger"
        onClick={(e) => {
          if (!confirm("¿Borrar esta apuesta de tu registro?")) e.preventDefault();
        }}
      >
        Borrar
      </button>
    </form>
  );
}

export function BankrollForm({ current, currency }: { current: string | null; currency: string }) {
  const [state, action, pending] = useActionState(setBankroll, null);
  return (
    <form action={action} className="inline-form">
      <label>
        Bankroll inicial
        <input
          name="amount"
          inputMode="decimal"
          defaultValue={current ?? ""}
          placeholder="200,00"
          required
          pattern="[0-9]+([.,][0-9]{1,2})?"
        />
      </label>
      <input type="hidden" name="currency" value={currency} />
      <button type="submit" disabled={pending}>
        Guardar
      </button>
      <Feedback state={state} />
    </form>
  );
}

export function LimitForm({ currency }: { currency: string }) {
  const [state, action, pending] = useActionState(setLimit, null);
  return (
    <form action={action} className="inline-form">
      <label>
        Periodo
        <select name="period" defaultValue="weekly">
          <option value="daily">Diario</option>
          <option value="weekly">Semanal</option>
          <option value="monthly">Mensual</option>
        </select>
      </label>
      <label>
        Límite
        <input name="amount" inputMode="decimal" placeholder="50,00" required pattern="[0-9]+([.,][0-9]{1,2})?" />
      </label>
      <input type="hidden" name="currency" value={currency} />
      <button type="submit" disabled={pending}>
        Fijar límite
      </button>
      <Feedback state={state} />
    </form>
  );
}

export function SelfExclusionForm() {
  const [state, action, pending] = useActionState(selfExclude, null);
  return (
    <form
      action={action}
      className="inline-form"
      onSubmit={(e) => {
        if (!confirm("No podrás registrar apuestas hasta que termine, ni acortarla. ¿Continuar?"))
          e.preventDefault();
      }}
    >
      <label>
        Duración
        <select name="days" defaultValue="30">
          <option value="7">7 días</option>
          <option value="30">30 días</option>
          <option value="180">6 meses</option>
          <option value="indefinida">Indefinida</option>
        </select>
      </label>
      <button type="submit" disabled={pending} className="btn-danger">
        Autoexcluirme
      </button>
      <Feedback state={state} />
    </form>
  );
}
