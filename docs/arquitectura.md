# Arquitectura — visión técnica

## Diagrama lógico (texto)

```
[Fuentes externas]
  Noticias (RSS/API) ─┐
  Odds (API casas)    ─┼──► [Servicio de Ingesta] ──► [Base de datos: Postgres]
  Estadísticas (API)  ─┘                                     │
                                                               ▼
                                                   [Pipeline de features]
                                                               │
                                                               ▼
                                                   [Modelo ML de pronóstico]
                                                               │
                                                               ▼
                                          [Claude API: redacción del informe]
                                                               │
                                                               ▼
                                                       [Backend API REST]
                                                               │
                                                               ▼
                                                        [Frontend Next.js]
```

## Entidades principales (borrador)

- **User**: id, email, fecha_nacimiento (verificación edad), país (jurisdicción), límites_autoimpuestos
- **Event**: id, deporte, equipos/competidores, fecha, estado
- **Odd**: id, event_id, casa/fuente, mercado (1x2, over/under, etc), valor, timestamp
- **NewsItem**: id, fuente, titulo, resumen, url, fecha, event_id (opcional, si está relacionado)
- **Bet** (registro del usuario): id, user_id, event_id, mercado, stake, odd_tomada, resultado, fecha
- **Forecast** (salida de IA): id, event_id, probabilidad_modelo, confianza, informe_texto (generado por Claude), fecha_generación, version_modelo

## Por qué separar `Forecast.probabilidad_modelo` de `Forecast.informe_texto`
Para que el frontend pueda mostrar claramente: "esto es un número calculado" vs "esto es una explicación generada", cumpliendo el principio de transparencia del CLAUDE.md.

## Integración con Claude API
- Usar la API de Anthropic (`/v1/messages`) solo para la etapa de **redacción** del informe final, nunca para calcular la probabilidad en sí.
- Prompt del sistema debe incluir: los datos duros (probabilidad, odds, noticias relevantes) y pedir explícitamente que distinga "dato" de "interpretación".
