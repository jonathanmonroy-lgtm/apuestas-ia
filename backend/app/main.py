from fastapi import FastAPI

from app.api import bets, events, health, me, news
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level, settings.log_json)

app = FastAPI(title="Apuestas IA API", version="0.1.0")
app.include_router(health.router)
app.include_router(events.router)
app.include_router(news.router)
app.include_router(bets.router)
app.include_router(me.router)


@app.get("/", include_in_schema=False)
def root() -> dict[str, object]:
    """There is no UI here (the UI is the Next.js app): point to what exists."""
    return {
        "name": "Apuestas IA API",
        "docs": "/docs",
        "health": "/health",
        "endpoints": [
            "/api/events",
            "/api/news",
            "/api/bets",
            "/api/bets-metrics",
            "/api/me/bankroll",
            "/api/me/limits",
            "/api/me/self-exclusion",
        ],
        "frontend": "http://127.0.0.1:3000 (npm run dev en frontend/)",
    }
