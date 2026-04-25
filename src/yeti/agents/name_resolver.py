"""Canonicalise mentioned names to known entities.

Triage extracts whatever the LLM put into ``people_mentioned`` —
"Lúcia", "Lucia", "Lucia Romão", "Joana Goncalves", "Joana Gonçalves"
all surface as separate names in practice. This module folds those
mentions onto known canonical names from contact drawers + KG, so
downstream lookup hits the same record.

Folding rules: NFC normalise, strip diacritics, lowercase, collapse
whitespace. If folded form matches exactly one known canonical -> use
it. If it matches none -> pass through unchanged (LLM may have
introduced a real new entity). If it matches multiple -> pass through
unchanged so DISAMBIGUATION can still surface.

A 5-minute TTL cache rebuilds the known-entity set from MemPalace.
Failure to refresh is fail-soft: triage keeps working with the last
cached set, or with no canonicalisation if cache is empty.
"""

import logging
import re
import time
import unicodedata

from yeti.memory.client import MemPalaceClient

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 300.0
_WS_RE = re.compile(r"\s+")


def _strip_diacritics(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def fold(name: str) -> str:
    """Lowercase, diacritic-stripped, whitespace-collapsed key."""
    if not name:
        return ""
    s = unicodedata.normalize("NFC", name).strip()
    s = _strip_diacritics(s)
    s = _WS_RE.sub(" ", s)
    return s.lower()


class _Cache:
    def __init__(self) -> None:
        self._fetched_at: float = 0.0
        # folded -> set of canonical names
        self._index: dict[str, set[str]] = {}

    def is_fresh(self) -> bool:
        return (
            self._fetched_at > 0
            and time.monotonic() - self._fetched_at < _CACHE_TTL_S
        )

    def get(self, folded: str) -> set[str]:
        return self._index.get(folded, set())

    def replace(self, index: dict[str, set[str]]) -> None:
        self._index = index
        self._fetched_at = time.monotonic()

    def is_loaded(self) -> bool:
        return self._fetched_at > 0


_cache = _Cache()


def _add_canonical(
    index: dict[str, set[str]], canonical: str
) -> None:
    """Index a canonical name under its full-form fold and each
    space-separated token's fold (so first names map back).
    """
    canonical = canonical.strip()
    if not canonical:
        return
    folded_full = fold(canonical)
    if folded_full:
        index.setdefault(folded_full, set()).add(canonical)
    for token in canonical.split():
        folded_token = fold(token)
        if folded_token and folded_token != folded_full:
            index.setdefault(folded_token, set()).add(canonical)


async def _build_index(
    client: MemPalaceClient,
) -> dict[str, set[str]]:
    """Rebuild the canonical-name index from MemPalace state."""
    index: dict[str, set[str]] = {}

    try:
        drawers = await client.search_drawers_with_ids(
            query="Name",
            wing="people",
            room="contacts",
            limit=200,
            source="name-resolver",
        )
    except Exception:
        logger.exception("Resolver: drawer enumeration failed")
        drawers = []

    for d in drawers:
        text = d.get("text") or ""
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("name:"):
                name = line.split(":", 1)[1].strip()
                if name:
                    _add_canonical(index, name)
                break

    return index


async def refresh_cache(
    client: MemPalaceClient | None = None,
) -> None:
    """Rebuild the cache. Idempotent; safe to call from triage."""
    client = client or MemPalaceClient()
    try:
        idx = await _build_index(client)
        _cache.replace(idx)
    except Exception:
        logger.exception("Resolver: cache refresh failed")


async def resolve(
    name: str,
    client: MemPalaceClient | None = None,
) -> str | None:
    """Return canonical name if folded form maps to exactly one match.

    Returns None when zero or multiple known canonicals match. Caller
    should fall back to the original name in that case.
    """
    if not name or not name.strip():
        return None
    if not _cache.is_fresh():
        await refresh_cache(client)
    folded = fold(name)
    if not folded:
        return None
    matches = _cache.get(folded)
    if len(matches) == 1:
        return next(iter(matches))
    return None


async def canonicalise_list(
    names: list[str],
    client: MemPalaceClient | None = None,
) -> list[str]:
    """Map a list of mentioned names through the resolver.

    Preserves order, dedupes by canonical, falls back to the original
    when no unique resolution is found.
    """
    client = client or MemPalaceClient()
    if not _cache.is_fresh():
        await refresh_cache(client)

    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        if not raw or not raw.strip():
            continue
        cleaned = raw.strip()
        canonical = await resolve(cleaned, client=client)
        chosen = canonical if canonical else cleaned
        key = fold(chosen)
        if key in seen:
            continue
        seen.add(key)
        out.append(chosen)
    return out
