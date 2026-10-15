import type { ReactNode } from "react";

import { DISPLAY_TZ } from "@/lib/format";

/**
 * The provenance label every number carries (CLAUDE.md principle: the user must tell
 * hard data from analysis from model output).
 * - dato: observed fact from a provider (result, kick-off, a stored price)
 * - analisis: our arithmetic over data (median, implied probability, ROI)
 * - inferido: a rule/heuristic decided it (which teams a news item is about)
 * - tuyo: the user's own records (bets, bankroll)
 * - noticia: third-party content
 */
export type Provenance = "dato" | "analisis" | "inferido" | "tuyo" | "noticia";

const LABELS: Record<Provenance, string> = {
  dato: "Dato",
  analisis: "Análisis",
  inferido: "Inferido",
  tuyo: "Tus registros",
  noticia: "Noticia de terceros",
};

export function Badge({ kind, title }: { kind: Provenance; title?: string }) {
  return (
    <span className={`badge badge-${kind}`} title={title}>
      {LABELS[kind]}
    </span>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "positive" | "negative";
}) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className={`tile-value${tone ? ` tone-${tone}` : ""}`}>
        {tone === "positive" && <span aria-hidden>▲ </span>}
        {tone === "negative" && <span aria-hidden>▼ </span>}
        {value}
      </div>
      {hint && <div className="tile-hint">{hint}</div>}
    </div>
  );
}

/** Profit sign as tone: colour is always paired with an arrow glyph, never alone. */
export function toneOf(value: string | null | undefined): "positive" | "negative" | undefined {
  if (value == null || /^-?0*(\.0*)?$/.test(value)) return undefined;
  return value.startsWith("-") ? "negative" : "positive";
}

export function Freshness({ label, iso, stale }: { label: string; iso: string | null; stale?: boolean }) {
  return (
    <span className={`freshness${stale ? " is-stale" : ""}`}>
      {label}: {iso ? <time dateTime={iso}>{new Date(iso).toLocaleString("es-ES", { timeZone: DISPLAY_TZ, dateStyle: "short", timeStyle: "short" })}</time> : "sin datos"}
      {stale && <strong> · desactualizado</strong>}
    </span>
  );
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="page-header">
      <h1>{title}</h1>
      {children && <div className="page-header-meta">{children}</div>}
    </header>
  );
}
