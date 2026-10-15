import type { Metadata } from "next";
import { Fraunces, Inter } from "next/font/google";

import { Nav } from "@/components/Nav";
import { ResponsibleGamblingBanner } from "@/components/ResponsibleGamblingBanner";

import "./globals.css";

const fraunces = Fraunces({ variable: "--font-serif", subsets: ["latin"] });
const inter = Inter({ variable: "--font-sans", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "Apuestas IA", template: "%s · Apuestas IA" },
  description: "Información, estadísticas y registro personal de apuestas. Sin garantías de ganancia.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="es" className={`${fraunces.variable} ${inter.variable}`}>
      <body>
        <ResponsibleGamblingBanner />
        <Nav />
        {children}
        <footer className="footer">
          <p>
            <strong>Cómo leer esta app.</strong> <span className="badge badge-dato">Dato</span> es lo
            que informa un proveedor (calendario, resultados, cuotas observadas).{" "}
            <span className="badge badge-analisis">Análisis</span> es un cálculo nuestro sobre esos
            datos (mediana, probabilidad implícita, ROI); no es un pronóstico.{" "}
            <span className="badge badge-inferido">Inferido</span> lo decidió una regla automática y
            puede fallar. Ninguna cifra es una garantía.
          </p>
          <p className="muted">
            Fuentes: football-data.org (calendario y resultados, con retraso), The Odds API (cuotas
            prepartido, región UE), NewsAPI.org (noticias, ~24 h de retraso). Horas en hora de
            España. +18. Modo interno: no se muestran casas de apuestas.
          </p>
        </footer>
      </body>
    </html>
  );
}
