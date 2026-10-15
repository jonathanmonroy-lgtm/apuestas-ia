# Diseño del frontend (Fase 6)

La plantilla no llegó a rellenarse, así que **todo lo de abajo está decidido por Claude**
(2026-10-14) para que lo revises. Cambia lo que no te encaje y lo ajusto.

Restricciones fijas: modo solo interno (sin nombres de casas, sin monetizar), juego
responsable visible en todas las vistas, y separar siempre dato / análisis / inferencia.

## General — decidido por Claude
- Público: tú solo (modo interno, sin login; usuario local único provisional).
- Escritorio primero, usable en móvil (rejillas que se reorganizan, sin scroll horizontal
  salvo la tabla del historial, que hace scroll dentro de su caja).
- Tema oscuro (el que ya traía el esqueleto: verde muy oscuro + dorado, Fraunces + Inter).
  No hay tema claro.
- Idioma español; cuotas decimales europeas ("2,10"); moneda por defecto EUR.
- Horas en hora de España (Europe/Madrid), etiquetado en el pie. La API guarda y sirve UTC.
- Cada número lleva su procedencia con una etiqueta: **Dato** (proveedor), **Análisis**
  (cálculo nuestro, p. ej. mediana o probabilidad implícita), **Inferido** (regla
  automática, p. ej. a qué equipo pertenece una noticia), **Tus registros**,
  **Noticia de terceros**. Leyenda explicada en el pie de todas las páginas.
- Banner +18 de juego responsable fijo arriba en todas las vistas.

## Vista 1 — Resumen (`/`)
- Fila de KPIs: bankroll, beneficio, ROI, acierto, stake medio, pendiente/disponible.
  Sin datos se muestra "—", nunca "0 %" (sin datos ≠ 0 %). Color de signo siempre con
  flecha ▲/▼ (nunca sólo color).
- Gráfico de evolución: una serie (bankroll si hay bankroll inicial; si no, beneficio
  acumulado), por apuesta resuelta en orden de liquidación; cruceta + tooltip (ratón y
  teclado ←/→) y "Ver como tabla". Color de serie validado para fondo oscuro.
- Panel de juego responsable: límites con lo usado y lo que queda, fijar límite, y
  autoexcluirse (con confirmación). Subir un límite espera 24 h; bajarlo es inmediato.
- Próximos 4 partidos y últimas 5 noticias.

## Vista 2 — Historial (`/historial`)
- Formulario de nueva apuesta arriba (partido de la lista o texto libre, 1X2, cuota,
  importe, moneda, notas). Llegando desde Programación, el partido viene preseleccionado.
  Si hay autoexclusión activa el formulario se sustituye por el aviso.
- Filtros en una fila: estado, desde, hasta.
- Tabla: fecha, partido, selección, cuota, importe, estado, resultado, acciones
  (Ganada / Perdida / Anulada, Reabrir, Borrar con confirmación).
- Borrar elimina la apuesta de tu registro (es tu registro personal; anular es otra cosa:
  una apuesta anulada por la casa devuelve el importe).
- No se pide ni se muestra la casa de apuestas (modo interno).

## Vista 3 — Programación (`/programacion`)
- Rango: próximos 7 / 14 (por defecto) / 30 días; partidos agrupados por día.
- Por partido: hora o "hora por confirmar" (Dato), jornada, estado y resultado (Dato);
  cuota 1X2 = **mediana** de las casas, nº de casas y antigüedad (Análisis), aviso si
  están desactualizadas; probabilidad implícita sin margen con barra (Análisis) y el
  margen; noticias relacionadas desplegables (Inferido); botón "Registrar apuesta".
- Mediana (no mejor cuota): la mejor cuota invita a "buscar casa", que en modo interno no
  se muestra.

## Fuera de alcance de la Fase 6
- Login / multiusuario y verificación de edad (pendiente de decidir autenticación y
  jurisdicción).
- Informes de IA (Fase posterior; la etiqueta "IA" se añadirá cuando exista).
- Tema claro.
