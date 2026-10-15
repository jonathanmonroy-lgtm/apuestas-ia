"use client";

import { useActionState } from "react";

import { fetchOdds } from "@/app/actions";

/** "Get the real odds" for one match: one budgeted request to the provider, on demand. */
export function FetchOddsButton({ eventId }: { eventId: number }) {
  const [state, action, pending] = useActionState(fetchOdds.bind(null, eventId), null);
  return (
    <form action={action} className="fetch-odds">
      <button type="submit" className="btn-small" disabled={pending}>
        {pending ? "Consultando…" : "Ver cuotas reales"}
      </button>
      {state && (
        <span className={state.ok ? "form-ok small" : "muted small"} role="status">
          {state.message}
        </span>
      )}
    </form>
  );
}
