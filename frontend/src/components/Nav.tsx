"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Partidos del día" },
  { href: "/resumen", label: "Resumen" },
  { href: "/programacion", label: "LaLiga" },
  { href: "/historial", label: "Historial" },
] as const;

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="nav" aria-label="Principal">
      <Link href="/" className="brand">
        Apuestas <span>IA</span>
      </Link>
      <ul>
        {LINKS.map((l) => {
          const active = l.href === "/" ? pathname === "/" : pathname.startsWith(l.href);
          return (
            <li key={l.href}>
              <Link href={l.href} aria-current={active ? "page" : undefined}>
                {l.label}
              </Link>
            </li>
          );
        })}
      </ul>
      <span className="nav-mode" title="Jurisdicción y monetización sin decidir">
        Modo interno
      </span>
    </nav>
  );
}
