"use client";

import { useId, useMemo, useRef, useState } from "react";

import { formatDateTime, money, signedMoney } from "@/lib/format";

export type Point = { id: number; at: string; value: string; delta: string };

/**
 * Single-series line (bankroll, or cumulative profit when no bankroll is set).
 * Dataviz spec: 2px line, ~10% area wash, end dot r=4 with a 2px surface ring, one direct
 * label at the end, hairline grid, crosshair + tooltip (pointer and keyboard), table view.
 * Series colour #a8851c is validated for the dark surface (OKLCH L inside 0.48-0.67,
 * contrast >= 3:1). Numbers are parsed ONLY to place pixels; every value shown is the
 * API's exact decimal string.
 */
export function EvolutionChart({
  points,
  currency,
  label,
  baseline,
}: {
  points: Point[];
  currency: string;
  label: string;
  baseline: string; // starting bankroll, or "0" for cumulative profit
}) {
  const W = 1000;
  const H = 280;
  const pad = { top: 16, right: 72, bottom: 28, left: 56 };
  const [active, setActive] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const titleId = useId();

  const geo = useMemo(() => {
    const all = [{ value: baseline }, ...points];
    const values = all.map((p) => Number(p.value));
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (min === max) {
      min -= 1;
      max += 1;
    }
    const step = niceStep((max - min) / 4);
    const lo = Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step;
    const x = (i: number) => pad.left + (i / Math.max(all.length - 1, 1)) * (W - pad.left - pad.right);
    const y = (v: number) => pad.top + (1 - (v - lo) / (hi - lo)) * (H - pad.top - pad.bottom);
    const ticks: number[] = [];
    for (let t = lo; t <= hi + step / 2; t += step) ticks.push(Number(t.toFixed(2)));
    const coords = values.map((v, i) => [x(i), y(v)] as const);
    const line = coords.map(([cx, cy], i) => `${i ? "L" : "M"}${cx},${cy}`).join("");
    const area = `${line}L${coords.at(-1)![0]},${y(lo)}L${coords[0][0]},${y(lo)}Z`;
    return { coords, line, area, ticks, y };
  }, [points, baseline, pad.left, pad.right, pad.top, pad.bottom]);

  if (points.length === 0) {
    return <p className="muted">Aún no hay apuestas resueltas: la evolución aparecerá aquí.</p>;
  }

  // index 0 is the starting baseline; points are 1..n
  const onMove = (clientX: number) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const vx = ((clientX - rect.left) / rect.width) * W;
    let best = 1;
    for (let i = 1; i < geo.coords.length; i++) {
      if (Math.abs(geo.coords[i][0] - vx) < Math.abs(geo.coords[best][0] - vx)) best = i;
    }
    setActive(best);
  };

  const last = geo.coords.at(-1)!;
  const activePoint = active !== null ? points[active - 1] : null;

  return (
    <figure className="chart">
      <div className="chart-plot">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-labelledby={titleId}
          tabIndex={0}
          onPointerMove={(e) => onMove(e.clientX)}
          onPointerLeave={() => setActive(null)}
          onFocus={() => setActive(points.length)}
          onBlur={() => setActive(null)}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") setActive((a) => Math.max(1, (a ?? points.length) - 1));
            if (e.key === "ArrowRight") setActive((a) => Math.min(points.length, (a ?? 0) + 1));
          }}
        >
          <title id={titleId}>{label}</title>
          {geo.ticks.map((t) => (
            <g key={t}>
              <line className="chart-grid" x1={pad.left} x2={W - pad.right} y1={geo.y(t)} y2={geo.y(t)} />
              <text className="chart-tick" x={pad.left - 8} y={geo.y(t)} textAnchor="end" dominantBaseline="middle">
                {t.toLocaleString("es-ES")}
              </text>
            </g>
          ))}
          <path d={geo.area} className="chart-area" />
          <path d={geo.line} className="chart-line" />
          {active !== null && (
            <line className="chart-crosshair" x1={geo.coords[active][0]} x2={geo.coords[active][0]} y1={pad.top} y2={H - pad.bottom} />
          )}
          {active !== null && (
            <circle cx={geo.coords[active][0]} cy={geo.coords[active][1]} r={4} className="chart-dot" />
          )}
          <circle cx={last[0]} cy={last[1]} r={4} className="chart-dot" />
          <text className="chart-endlabel" x={last[0] + 10} y={last[1]} dominantBaseline="middle">
            {money(points.at(-1)!.value, currency)}
          </text>
          <text className="chart-tick" x={pad.left} y={H - 8}>
            inicio
          </text>
          <text className="chart-tick" x={W - pad.right} y={H - 8} textAnchor="end">
            apuesta {points.length}
          </text>
        </svg>
        {activePoint && (
          <div
            className="chart-tooltip"
            style={{ left: `${(geo.coords[active!][0] / W) * 100}%` }}
            role="status"
          >
            <strong>{money(activePoint.value, currency)}</strong>
            <span>
              {signedMoney(activePoint.delta, currency)} · {formatDateTime(activePoint.at)}
            </span>
          </div>
        )}
      </div>
      <details className="chart-table">
        <summary>Ver como tabla</summary>
        <table>
          <thead>
            <tr>
              <th>Liquidada</th>
              <th className="num">Resultado</th>
              <th className="num">{label}</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p) => (
              <tr key={p.id}>
                <td>{formatDateTime(p.at)}</td>
                <td className="num">{signedMoney(p.delta, currency)}</td>
                <td className="num">{money(p.value, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

function niceStep(raw: number): number {
  const exp = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / exp;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp;
}
