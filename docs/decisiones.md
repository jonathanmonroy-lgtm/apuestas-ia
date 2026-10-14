# Decisiones técnicas pendientes

Formato: Decisión | Opciones | Recomendación inicial | Estado

| # | Decisión | Opciones | Recomendación inicial | Estado |
|---|----------|----------|------------------------|--------|
| 1 | Backend | Python (FastAPI) vs Node (NestJS) | **FastAPI**: el ecosistema de IA/ML (pandas, scikit-learn, pytorch) es más natural en Python | Pendiente de confirmar |
| 2 | Frontend | Next.js vs SPA React simple | **Next.js**: SSR útil para SEO de noticias/reportes públicos | Pendiente |
| 3 | Base de datos | PostgreSQL vs MongoDB | **PostgreSQL**: los datos (apuestas, odds, usuarios) son muy relacionales | Pendiente |
| 4 | Cola de trabajos / cron | Celery + Redis vs APScheduler | **APScheduler** en un worker propio (`app.worker`), nunca en el proceso de la API. Una sola instancia; migrar a Celery si escala | Decidido |
| 5 | Modelo de pronóstico | LLM puro vs modelo estadístico + LLM para redacción | **Híbrido**: modelo estadístico/ML para la probabilidad, Claude para explicar el informe en lenguaje natural | Recomendado, no negociable por calidad |
| 6 | Hosting | Railway/Render (rápido) vs AWS/GCP (escalable) | Empezar en **Railway/Render** para el MVP, migrar después | Pendiente |
| 7 | Proveedor de fixtures/resultados | API-Football (gratis: sólo temporadas 2022-2024) vs football-data.org (gratis: LaLiga actual, 10 req/min, marcadores con retraso) | **football-data.org**: verificado 2026-10-14 con llamada real que API-Football gratis rechaza 2026 (`errors.plan`). Adaptador de API-Football se conserva inactivo por si se paga un plan | Decidido |

## Justificación del punto 5 (importante)
Un LLM **no debe inventar probabilidades de partidos** a partir de solo su conocimiento general — alucina y no tiene datos en tiempo real fiables. El patrón correcto es:

1. Un modelo estadístico/ML (entrenado con histórico real: resultados, odds, estadísticas) calcula una probabilidad.
2. Claude (vía API) toma esa probabilidad + las noticias relevantes + contexto, y **redacta** el informe explicando el "por qué" en lenguaje natural.
3. El usuario ve ambas cosas por separado: el número (con su fuente) y la explicación (marcada como análisis generado).

Esto evita informes con apariencia de certeza que en realidad son solo texto generado sin respaldo de datos.
