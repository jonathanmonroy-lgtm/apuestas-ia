from fastapi import FastAPI

from app.api import health
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level, settings.log_json)

app = FastAPI(title="Apuestas IA API", version="0.1.0")
app.include_router(health.router)
