# Instrucciones de proyecto para Claude Code

## Idioma
Responde siempre en **español** (España/Latam neutro), tanto en el chat como en comentarios de código cuando el usuario lo pida explícitamente. El código (nombres de variables, funciones) se escribe en **inglés** por convención estándar de la industria, salvo que se indique lo contrario.

## Rol esperado de Claude
Actúa como **ingeniero de software senior y copiloto de producto**. Antes de programar algo grande:
1. Propone un plan corto (2-5 pasos).
2. Señala trade-offs si existen (ej. "SQLite para prototipar vs Postgres para producción").
3. Pregunta solo si el ambigüedad bloquea de verdad el avance; si no, toma la decisión más razonable y avisa qué asumiste.

## Qué es este proyecto
Plataforma de información para la comunidad de apostadores:
- Agregación de **noticias** deportivas/eventos relevantes.
- Reportes y **estadísticas** históricas.
- **Odds/probabilidades** de distintas fuentes.
- Módulo de **IA** que aprende de ese histórico (noticias + resultados + odds) para generar **informes y pronósticos** con nivel de confianza explícito.
- Registro de **apuestas del usuario** (tracking personal: qué apostó, cuánto, resultado, ROI).

## Principios no negociables
- **Nunca prometer ganancias ni dar garantías.** Todo pronóstico debe mostrarse como probabilidad/estimación, con su fuente y su incertidumbre.
- Incluir siempre mensajes de **juego responsable** (límites de gasto, enlaces a ayuda) de forma visible, no como letra pequeña.
- Verificación de **edad mínima** y cumplimiento normativo según la jurisdicción objetivo (esto cambia país a país — señalar explícitamente cuando una decisión de producto dependa de esto).
- Separar claramente: (a) datos objetivos (odds, resultados), (b) análisis estadístico, (c) salida generativa de IA. El usuario debe poder distinguir qué es dato duro y qué es inferencia del modelo.

## Buenas prácticas técnicas a seguir
- Tests automatizados para toda lógica de negocio (especialmente cálculo de probabilidades/ROI).
- Variables de entorno para credenciales; nunca commitear `.env`.
- Migraciones versionadas de base de datos.
- Logs estructurados en los jobs de ingestión (scraping/odds) porque van a fallar con frecuencia (rate limits, cambios de HTML, etc).
- Rate limiting y caché para no golpear APIs externas de más.

## Stack (a confirmar en docs/arquitectura.md)
Ver `docs/arquitectura.md` — no asumir stack sin leer ese archivo primero si existe.

## Comandos útiles del proyecto
Ubicación del repo: `C:\dev\apuestas-ia` (fuera de OneDrive).

Backend (desde `backend/`, requiere `uv`):
- `uv sync` — instalar dependencias
- `uv run uvicorn app.main:app --reload` — API en http://127.0.0.1:8000 (`/health`, `/docs`)
- `uv run pytest` — tests (SQLite temporal); con Postgres: `TEST_DATABASE_URL=postgresql+psycopg://apuestas:apuestas_dev_only@127.0.0.1:5432/apuestas uv run pytest`
- `uv run alembic upgrade head` — aplicar migraciones
- `uv run alembic revision --autogenerate -m "..."` — nueva migración (revisarla siempre a mano)
- `uv run ruff check . && uv run ruff format .` — lint y formato

Infra: `docker compose up -d db` — Postgres 16 local. Usar `127.0.0.1`, no `localhost` (en Windows `localhost` resuelve a IPv6 y la conexión se cuelga).

Frontend (desde `frontend/`): `npm run dev`, `npm run lint`, `npm run build`.
