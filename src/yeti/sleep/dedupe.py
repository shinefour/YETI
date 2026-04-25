"""Drawer dedupe — find exact-text duplicates per (wing, room).

Walks the ChromaDB collection directly so we don't depend on a search
query that might miss results. Within each (wing, room) group, drawers
with identical (whitespace-normalised) content are collapsed to one;
older drawers are marked superseded via SupersededStore. Search
consumers then filter superseded ids out.

Cosine-similarity dedupe is intentionally NOT included here — it
requires careful threshold tuning to avoid false positives. Exact-text
dedupe is risk-free and already addresses the most common bloat
(re-ingestion of the same email, repeated bootstrap drawers).
"""

import logging
import re

from yeti.models.superseded import SupersededStore

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip()).lower()


def _enumerate_drawers() -> list[dict]:
    """Yield every drawer in the collection: id, text, metadata."""
    try:
        import chromadb

        from yeti.memory.client import MemPalaceClient
    except Exception:
        logger.exception("Cannot import chromadb / MemPalaceClient")
        return []

    client = MemPalaceClient()
    try:
        col = chromadb.PersistentClient(
            path=client.palace_path
        ).get_collection("mempalace_drawers")
    except Exception:
        logger.exception("Failed to open chromadb collection")
        return []

    try:
        # ChromaDB get() with no filter returns everything in pages.
        page_size = 500
        offset = 0
        items: list[dict] = []
        while True:
            page = col.get(
                limit=page_size,
                offset=offset,
                include=["documents", "metadatas"],
            )
            ids = page.get("ids") or []
            docs = page.get("documents") or []
            metas = page.get("metadatas") or []
            if not ids:
                break
            for i, _id in enumerate(ids):
                items.append(
                    {
                        "id": _id,
                        "text": docs[i] if i < len(docs) else "",
                        "metadata": (
                            metas[i] if i < len(metas) else {}
                        )
                        or {},
                    }
                )
            if len(ids) < page_size:
                break
            offset += page_size
        return items
    except Exception:
        logger.exception("Drawer enumeration failed")
        return []


def _drawer_sort_key(item: dict) -> str:
    """Sort key — newer drawers come last so we keep them.

    Falls back to the drawer id if no created_at metadata; this gives
    stable ordering even if timestamps are missing.
    """
    meta = item.get("metadata") or {}
    return str(
        meta.get("created_at")
        or meta.get("added_at")
        or item.get("id", "")
    )


def find_duplicate_groups() -> list[list[dict]]:
    """Group drawers by (wing, room, normalised text). Returns groups
    of size > 1, sorted oldest -> newest (last is canonical).
    """
    items = _enumerate_drawers()
    if not items:
        return []

    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for item in items:
        meta = item.get("metadata") or {}
        wing = (meta.get("wing") or "").strip()
        room = (meta.get("room") or "").strip()
        norm = _normalise(item.get("text") or "")
        if not norm:
            continue
        key = (wing, room, norm)
        buckets.setdefault(key, []).append(item)

    groups: list[list[dict]] = []
    for bucket_items in buckets.values():
        if len(bucket_items) > 1:
            bucket_items.sort(key=_drawer_sort_key)
            groups.append(bucket_items)
    return groups


def run_dedupe() -> dict:
    """Sweep duplicates and mark older drawers superseded.

    Returns counts for the audit log: {groups, superseded}.
    """
    store = SupersededStore()
    already_superseded = store.superseded_ids()

    groups = find_duplicate_groups()
    superseded_count = 0
    for group in groups:
        canonical = group[-1]
        for older in group[:-1]:
            old_id = older.get("id")
            new_id = canonical.get("id")
            if not old_id or not new_id or old_id == new_id:
                continue
            if old_id in already_superseded:
                continue
            store.supersede(
                drawer_id=old_id,
                superseded_by=new_id,
                reason="exact-text-duplicate",
            )
            superseded_count += 1

    logger.info(
        "Sleep dedupe: groups=%d superseded=%d",
        len(groups),
        superseded_count,
    )
    return {"groups": len(groups), "superseded": superseded_count}
