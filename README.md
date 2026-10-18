# Apuestas IA — Plataforma de información para la comunidad de apostadores

## 📱 Descargar la app (Android)

**[⬇️ Descargar apuestas-ia.apk](https://github.com/jonathanmonroy-lgtm/apuestas-ia/releases/latest/download/apuestas-ia.apk)** · [todas las versiones](https://github.com/jonathanmonroy-lgtm/apuestas-ia/releases)

1. Abre el enlace desde el navegador del móvil y descarga el APK.
2. Ábrelo y acepta *instalar apps de orígenes desconocidos* para el navegador.
3. Si Play Protect avisa, pulsa *Más detalles → Instalar de todas formas*.

Versión de prueba interna, **sólo mayores de 18 años**. Información y estimaciones, nunca garantías de ganancia. La app (Flutter, carpeta `app_movil/`) se compila en GitHub Actions y cada tag `v*` publica un APK nuevo.

## 🎯 Qué es esto
Plataforma que centraliza **noticias, reportes estadísticos, probabilidades (odds)** de distintas casas/fuentes, y un módulo de **IA** que aprende de todo ese histórico para generar **informes y pronósticos razonados** (no garantizados) para la comunidad.

> ⚠️ Esto NO es un sistema de "apuestas seguras" ni promete ganancias. Es una herramienta de información y análisis. Debe incluir siempre mensajes de **juego responsable** y cumplir la normativa local (edad mínima, licencias, disclaimers).

## 🧱 Arquitectura (visión MVP)

```
apuestas-ia/
├── backend/            # API (FastAPI/Python o Node/Nest, a definir)
│   └── app/
│       ├── api/        # endpoints REST
│       ├── models/     # modelos de datos (apuestas, eventos, usuarios)
│       ├── services/   # scraping, ingestión de noticias, odds
│       └── ia/         # entrenamiento, inferencia, pipelines ML
├── frontend/           # App web (Next.js/React)
├── docs/               # documentación de producto y arquitectura
├── scripts/            # utilidades (seed, migraciones, cron jobs)
└── .claude/
    └── CLAUDE.md        # memoria/instrucciones persistentes para Claude Code
```

## 🚀 Primeros pasos

```bash
cd apuestas-ia
claude          # abre Claude Code en esta carpeta
```

Dentro de Claude Code, pide por ejemplo:
> "Lee docs/roadmap.md y docs/arquitectura.md, y ayúdame a montar el backend inicial según el plan del Sprint 0."

## 📍 Estado actual
Fase 0 — Definición y scaffolding. Ver `docs/roadmap.md`.
