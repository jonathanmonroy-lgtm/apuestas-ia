/**
 * Responsible gambling notice: a compact "+18" badge on every page (in the nav) that opens
 * the full text. The user asked to drop the full-width banners (2026-10-17); the badge
 * keeps the notice present on every screen. Help resources are jurisdiction-dependent:
 * update when the target country is decided.
 */
export function ResponsibleGamblingBadge() {
  return (
    <details className="rg-badge">
      <summary aria-label="Juego responsable: mayores de 18">+18</summary>
      <div className="rg-popover" role="note">
        <strong>Juega con responsabilidad.</strong> Esta plataforma ofrece información y
        estimaciones, nunca garantías de ganancia. <span className="badge badge-dato">Dato</span>{" "}
        es lo que informa un proveedor; <span className="badge badge-analisis">Análisis</span> es un
        cálculo nuestro, no un pronóstico. Fija límites de gasto y, si sientes que pierdes el
        control, busca ayuda profesional.
      </div>
    </details>
  );
}
