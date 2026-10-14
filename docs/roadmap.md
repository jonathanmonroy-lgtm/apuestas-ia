# Roadmap — Apuestas IA

## Sprint 0 — Fundaciones (esta semana)
- [ ] Decidir stack definitivo (ver `decisiones.md`)
- [ ] Repo en Git + CI básico (lint + tests)
- [ ] Esqueleto backend (health check endpoint)
- [ ] Esqueleto frontend (página de login/dashboard vacío)
- [ ] Modelo de datos inicial: Usuario, Evento, Apuesta, FuenteNoticia, Odd

## Sprint 1 — Registro de apuestas (MVP #1)
- [ ] CRUD de apuestas del usuario (manual, sin IA todavía)
- [ ] Cálculo de métricas básicas: ROI, win rate, stake total
- [ ] Vista de historial con filtros (deporte, fecha, resultado)

## Sprint 2 — Ingesta de datos externos
- [ ] Conector a al menos 1 fuente de noticias (RSS/API)
- [ ] Conector a al menos 1 fuente de odds (API de casas o agregador)
- [ ] Normalización de datos en un modelo común
- [ ] Job programado (cron) de ingestión + almacenamiento histórico

## Sprint 3 — Primeros reportes (sin IA generativa aún)
- [ ] Reportes estadísticos simples (forma reciente de un equipo, head-to-head)
- [ ] Panel de "probabilidades implícitas" a partir de las odds agregadas

## Sprint 4 — Módulo de IA v1
- [ ] Pipeline de features a partir del histórico (noticias + resultados + odds)
- [ ] Modelo base de pronóstico (empezar simple: regresión logística o gradient boosting, NO un LLM desde el día 1)
- [ ] Informe generado combinando: datos duros + salida del modelo + resumen en lenguaje natural (aquí sí entra un LLM, para **redactar** el informe, no para "adivinar" el resultado)
- [ ] Métricas de calidad del modelo (calibración, no solo accuracy)

## Sprint 5 — Pulido y cumplimiento
- [ ] Mensajes de juego responsable integrados en el flujo
- [ ] Disclaimers legales visibles
- [ ] Verificación de edad / jurisdicción
- [ ] Auditoría de seguridad básica (secrets, rate limiting, OWASP top 10)

---

## Notas de alcance
- **No** se construye un "bot que apuesta solo" ni ejecución automática de apuestas en el MVP — demasiado riesgo legal y técnico.
- La IA generativa (Claude vía API) se usa para **redactar y explicar** informes, no para inventar probabilidades sin base estadística.
