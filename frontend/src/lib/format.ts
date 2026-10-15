/**
 * Display formatting. Money and odds arrive from the API as exact decimal STRINGS and are
 * formatted as strings: they never go through a JS float (no 0.1 + 0.2 surprises).
 * Dates arrive as UTC ISO strings and are shown in Colombia's time zone (the product's day), labelled.
 */

export const DISPLAY_TZ = "America/Bogota"; // the product's day and clock: Colombia (UTC-5)

function groupThousands(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

/** "1234.5" -> "1.234,50" (es-ES), exact. */
export function decimal(value: string, fractionDigits = 2): string {
  const negative = value.startsWith("-");
  const [int, frac = ""] = value.replace("-", "").split(".");
  const padded = (frac + "0".repeat(fractionDigits)).slice(0, fractionDigits);
  // The API already rounds money to the cent; we only pad, never round here.
  const body = fractionDigits > 0 ? `${groupThousands(int)},${padded}` : groupThousands(int);
  return negative ? `−${body}` : body;
}

const SYMBOLS: Record<string, string> = { EUR: "€", USD: "US$", GBP: "£" };

export function money(value: string | null | undefined, currency: string): string {
  if (value == null) return "—";
  return `${decimal(value)} ${SYMBOLS[currency] ?? currency}`;
}

const isZero = (value: string) => /^-?0*(\.0*)?$/.test(value);

/** Profit with an explicit sign: "+9,50 €", "−20,00 €", "0,00 €". */
export function signedMoney(value: string | null | undefined, currency: string): string {
  if (value == null) return "—";
  if (value.startsWith("-") || isZero(value)) return money(value, currency);
  return `+${money(value, currency)}`;
}

/** Ratio as percent by shifting the decimal point (exact, no float, no rounding):
 * "0.5667" -> "56,67 %", "-1.0000" -> "−100,00 %". The API sends 4 decimals. */
export function percent(ratio: string | null | undefined): string {
  if (ratio == null) return "—";
  const negative = ratio.startsWith("-");
  const [int, frac = ""] = ratio.replace("-", "").split(".");
  const padded = frac.padEnd(4, "0");
  const whole = (int + padded.slice(0, 2)).replace(/^0+(?=\d)/, "");
  const shown = `${whole},${padded.slice(2, 4)} %`;
  return negative && !isZero(ratio) ? `−${shown}` : shown;
}

/** Decimal odds with every significant digit (min 2): "2.1" -> "2,10", "2.085" -> "2,085". */
export function odds(value: string): string {
  const frac = (value.split(".")[1] ?? "").replace(/0+$/, "");
  return decimal(value, Math.max(2, frac.length));
}

const dateFmt = new Intl.DateTimeFormat("es-ES", {
  timeZone: DISPLAY_TZ,
  weekday: "short",
  day: "numeric",
  month: "short",
});
const timeFmt = new Intl.DateTimeFormat("es-ES", {
  timeZone: DISPLAY_TZ,
  hour: "2-digit",
  minute: "2-digit",
});
const dateTimeFmt = new Intl.DateTimeFormat("es-ES", {
  timeZone: DISPLAY_TZ,
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export const formatDate = (iso: string) => dateFmt.format(new Date(iso));
export const formatTime = (iso: string) => timeFmt.format(new Date(iso));
export const formatDateTime = (iso: string) => dateTimeFmt.format(new Date(iso));

/** Calendar day key in Spain's time zone, for grouping matches by day. */
export function dayKey(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: DISPLAY_TZ }).format(new Date(iso));
}

export function ago(iso: string | null, now = new Date()): string {
  if (!iso) return "nunca";
  const minutes = Math.round((now.getTime() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "hace menos de 1 min";
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `hace ${hours} h`;
  return `hace ${Math.round(hours / 24)} días`;
}
