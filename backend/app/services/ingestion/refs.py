"""ExternalRef helpers: resolve a provider's id to our internal id."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExternalRef
from app.models.enums import EntityType, Provider


def find_ref(
    session: Session, source: Provider, entity_type: EntityType, external_id: str
) -> int | None:
    return session.scalar(
        select(ExternalRef.entity_id).where(
            ExternalRef.source == source,
            ExternalRef.entity_type == entity_type,
            ExternalRef.external_id == external_id,
        )
    )


def add_ref(
    session: Session,
    source: Provider,
    entity_type: EntityType,
    external_id: str,
    entity_id: int,
    raw_name: str | None = None,
) -> None:
    session.add(
        ExternalRef(
            source=source,
            entity_type=entity_type,
            external_id=external_id,
            entity_id=entity_id,
            raw_name=raw_name,
        )
    )
