"use server";

import { refresh } from "next/cache";

import { api, ApiError } from "@/lib/api";

/**
 * Server Actions for the user's own records. They forward to the FastAPI backend, which
 * validates everything (Decimal money, limits, self-exclusion) and is the only authority:
 * nothing here re-implements business rules. Errors come back as text for the form.
 */

export type ActionState = { ok: boolean; message: string } | null;

function failure(err: unknown): ActionState {
  if (err instanceof ApiError) {
    if (err.status === 403 || err.status === 409) return { ok: false, message: err.detail };
    return { ok: false, message: `No se pudo guardar: ${err.detail}` };
  }
  return { ok: false, message: "Error inesperado al guardar." };
}

const text = (form: FormData, key: string) => {
  const v = form.get(key);
  return typeof v === "string" && v.trim() ? v.trim() : undefined;
};

/** "10,50" or "10.50" -> "10.50": a decimal STRING for the API, never a float. */
const decimalField = (form: FormData, key: string) => text(form, key)?.replace(",", ".");

export async function createBet(_prev: ActionState, form: FormData): Promise<ActionState> {
  const eventId = text(form, "event_id");
  try {
    await api.createBet({
      event_id: eventId ? Number(eventId) : undefined,
      event_label: text(form, "event_label"),
      selection: text(form, "selection"),
      odds_taken: decimalField(form, "odds_taken"),
      stake: decimalField(form, "stake"),
      currency: text(form, "currency") ?? "EUR",
      notes: text(form, "notes"),
    });
  } catch (err) {
    return failure(err);
  }
  refresh();
  return { ok: true, message: "Apuesta registrada." };
}

export async function settleBet(id: number, status: "won" | "lost" | "void" | "pending") {
  await api.updateBet(id, { status });
  refresh();
}

export async function deleteBet(id: number) {
  await api.deleteBet(id);
  refresh();
}

export async function setBankroll(_prev: ActionState, form: FormData): Promise<ActionState> {
  try {
    await api.setBankroll({
      amount: decimalField(form, "amount") ?? "",
      currency: text(form, "currency") ?? "EUR",
    });
  } catch (err) {
    return failure(err);
  }
  refresh();
  return { ok: true, message: "Bankroll inicial guardado." };
}

export async function setLimit(_prev: ActionState, form: FormData): Promise<ActionState> {
  try {
    const res = await api.setLimit({
      period: (text(form, "period") ?? "daily") as "daily" | "weekly" | "monthly",
      amount: decimalField(form, "amount") ?? "",
      currency: text(form, "currency") ?? "EUR",
    });
    refresh();
    const pending = res.limits.some((l) => l.pending_amount);
    return {
      ok: true,
      message: pending
        ? `Subida registrada: se aplicará dentro de ${res.cooling_off_hours} h. Hasta entonces sigue el límite actual.`
        : "Límite aplicado desde ya.",
    };
  } catch (err) {
    return failure(err);
  }
}

export async function selfExclude(_prev: ActionState, form: FormData): Promise<ActionState> {
  const days = text(form, "days");
  try {
    await api.startSelfExclusion(days === "indefinida" || !days ? null : Number(days));
  } catch (err) {
    return failure(err);
  }
  refresh();
  return { ok: true, message: "Autoexclusión activada. No podrás registrar apuestas." };
}
