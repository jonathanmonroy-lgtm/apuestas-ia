/**
 * Always-visible responsible gambling notice (non-negotiable product rule).
 * Help resources are jurisdiction-dependent: update when the target country is decided.
 */
export function ResponsibleGamblingBanner() {
  return (
    <aside
      role="note"
      aria-label="Juego responsable"
      style={{
        background: "var(--surface-2)",
        borderBottom: "1px solid var(--border)",
        padding: "10px var(--gutter)",
        fontSize: 14,
        color: "var(--text)",
      }}
    >
      <strong style={{ color: "var(--gold)" }}>+18 · Juega con responsabilidad.</strong>{" "}
      Esta plataforma ofrece información y estimaciones, nunca garantías de ganancia. Fija
      límites de gasto y, si sientes que pierdes el control, busca ayuda profesional.
    </aside>
  );
}
