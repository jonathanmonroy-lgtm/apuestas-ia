"""News ingestion: provider -> IngestionRun (raw) -> NewsItem (+ team links).

- Every article is classified (classifier.py); only those about at least one LaLiga team
  we have in the calendar are stored. Off-topic ones are counted, never stored.
- De-duplicated by sha256 of the canonical URL (unique column): re-runs and syndicated
  copies with tracking params don't create duplicates.
- Only a short summary is kept (NEWS_SUMMARY_MAX_CHARS); the provider's `content`
  snippet is used for classification and discarded: it is also stripped from the raw
  payload kept on the IngestionRun (provider.redact_raw), and raw payloads are purged
  after RAW_PAYLOAD_RETENTION_DAYS (retention.py).
"""

import re
from collections import Counter
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import get_logger
from app.models import NewsItem, Participant
from app.models.enums import EntityType, IngestionStatus, Provider
from app.models.news import NEWS_SUMMARY_MAX_CHARS
from app.services.errors import ProviderError
from app.services.ingestion.fetch import fetch_with_budget
from app.services.ingestion.refs import find_ref
from app.services.ingestion.runs import RunOutcome, RunRecorder
from app.services.ingestion.urls import url_hash
from app.services.news_provider.base import NewsBatch, NewsProvider
from app.services.news_provider.classifier import classify
from app.services.quota import QuotaPolicy

log = get_logger(__name__)

JOB_NEWS = "news"
TITLE_MAX = 400
# Team ids in team_terms.py are football-data ids: resolve them through that provider.
TEAM_ID_SOURCE = Provider.FOOTBALL_DATA


def run_news_ingestion(
    session_factory: sessionmaker[Session], provider: NewsProvider, *, policy: QuotaPolicy
) -> RunOutcome:
    recorder = RunRecorder(session_factory, provider.source, JOB_NEWS)
    with session_factory() as s:
        newest_stored = s.scalar(select(func.max(NewsItem.published_at)))
    raw = fetch_with_budget(
        session_factory,
        recorder,
        provider.source,
        policy,
        provider.fetch_news,
        redact=provider.redact_raw,
    )
    if isinstance(raw, RunOutcome):
        return raw

    try:
        batch = provider.normalize(raw)
    except Exception as exc:
        if not isinstance(exc, ProviderError):
            log.exception("news_normalize_unexpected_error", run_id=recorder.run_id)
        return recorder.finish(IngestionStatus.FAILED, error=exc)

    gap = coverage_gap(batch, newest_stored, provider.page_size)
    if gap is not None:
        # Free plan: only the 100 newest results, no page 2. The page doesn't reach back
        # to what we already had, so articles in between were missed.
        log.warning("news_coverage_gap", run_id=recorder.run_id,
                    gap_from=gap[0].isoformat(), gap_to=gap[1].isoformat())  # fmt: skip

    try:
        with session_factory() as s, s.begin():
            stats = store_batch(s, batch, run_id=recorder.run_id)
    except Exception as exc:
        log.exception("news_store_failed", run_id=recorder.run_id)
        return recorder.finish(IngestionStatus.FAILED, error=exc)

    log.info("news_batch_stored", run_id=recorder.run_id, **stats)
    return recorder.finish(IngestionStatus.SUCCESS, records=stats["inserted"])


def coverage_gap(
    batch: NewsBatch, newest_stored: datetime | None, page_size: int
) -> tuple[datetime, datetime] | None:
    """(from, to) of a window we certainly missed, or None. Only a FULL page can hide
    older articles; if the page's oldest article is newer than everything we already
    stored, the articles in between are lost (free plan: no page 2)."""
    if newest_stored is None or batch.oldest_in_page is None:
        return None
    if batch.page_count < page_size or batch.oldest_in_page <= newest_stored:
        return None
    return newest_stored, batch.oldest_in_page


def clean_text(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    text = re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text or None
    return text[: limit - 1].rstrip() + "…"


def store_batch(s: Session, batch: NewsBatch, *, run_id: int | None) -> dict[str, int]:
    stats: Counter[str] = Counter()
    participants: dict[str, Participant | None] = {}

    def participant(team_id: str) -> Participant | None:
        if team_id not in participants:
            pid = find_ref(s, TEAM_ID_SOURCE, EntityType.PARTICIPANT, team_id)
            participants[team_id] = s.get(Participant, pid) if pid else None
        return participants[team_id]

    seen: set[str] = set()
    for a in batch.articles:
        result = classify(a.title, a.description, a.content, a.url)
        if not result.team_ids:
            stats[f"skipped_{result.reason}"] += 1
            continue
        teams = [p for tid in sorted(result.team_ids) if (p := participant(tid)) is not None]
        if not teams:
            stats["skipped_team_not_in_calendar"] += 1
            continue
        digest = url_hash(a.url)
        if digest in seen or s.scalar(select(NewsItem.id).where(NewsItem.url_hash == digest)):
            stats["skipped_duplicate"] += 1
            continue
        seen.add(digest)
        s.add(
            NewsItem(
                source=batch.source,
                publisher=(a.publisher or "")[:120] or None,
                url=a.url,
                url_hash=digest,
                title=clean_text(a.title, TITLE_MAX) or a.title[:TITLE_MAX],
                summary=clean_text(a.description, NEWS_SUMMARY_MAX_CHARS),
                language=batch.language,
                published_at=a.published_at,
                ingestion_run_id=run_id,
                participants=teams,
            )
        )
        stats["inserted"] += 1
    stats.setdefault("inserted", 0)
    return dict(stats)
