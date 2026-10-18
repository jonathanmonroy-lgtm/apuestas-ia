import type { Metadata } from "next";
import { Fraunces, Inter } from "next/font/google";

import { Nav } from "@/components/Nav";

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
        <Nav />
        {children}
      </body>
    </html>
  );
}
