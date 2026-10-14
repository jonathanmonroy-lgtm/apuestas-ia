"""URL canonicalisation for de-duplication (news_item.url_hash = sha256 of this)."""

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_",)
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "ns_mchannel"}


def canonical_url(url: str) -> str:
    """Same article, same string: http/https unified, lowercase host without 'www.', no
    fragment, no tracking params, remaining params sorted, no trailing slash."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host if parts.port in (None, 80, 443) else f"{host}:{parts.port}"
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(TRACKING_PREFIXES) and k.lower() not in TRACKING_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", netloc, path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()
