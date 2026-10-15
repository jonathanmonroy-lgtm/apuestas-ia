"use client";

/** Shown when the backend API can't be reached or fails: never invent data instead. */
export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="page">
      <h1>No se pudieron cargar los datos</h1>
      <p>
        La interfaz no muestra datos que no vengan de la API. Comprueba que el backend está
        arrancado (<code>uv run uvicorn app.main:app</code> en <code>backend/</code>).
      </p>
      <p className="muted small">{error.message}</p>
      <button onClick={reset}>Reintentar</button>
    </main>
  );
}
