from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db.session import get_db
from app.main import app


def test_health(migrated_engine):
    def override_db():
        with sessionmaker(bind=migrated_engine)() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_api_keys_never_leak_in_repr():
    settings = Settings(odds_api_key="super-secret-123", _env_file=None)
    assert "super-secret-123" not in repr(settings)
    assert settings.odds_api_key.get_secret_value() == "super-secret-123"


def test_internal_mode_is_on_by_default():
    # Compliance default until jurisdiction is decided: bookmaker names stay hidden.
    assert Settings(_env_file=None).internal_mode is True


def test_root_points_to_docs_instead_of_404(migrated_engine):
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert response.json()["docs"] == "/docs"
