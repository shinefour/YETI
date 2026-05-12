"""Tests for the canonical-name resolver."""

import pytest

from yeti.agents import name_resolver
from yeti.agents.name_resolver import (
    canonicalise_list,
    fold,
    humanize_email_local,
    refresh_cache,
    resolve,
)


def test_humanize_email_local_dotted():
    assert humanize_email_local("warren.hamilton") == "Warren Hamilton"


def test_humanize_email_local_underscore():
    assert humanize_email_local("max_keil") == "Max Keil"


def test_humanize_email_local_hyphen():
    assert humanize_email_local("anna-maria") == "Anna Maria"


def test_humanize_email_local_passes_through_proper_name():
    assert humanize_email_local("Warren Hamilton") == "Warren Hamilton"


def test_humanize_email_local_passes_through_mixed_case():
    assert humanize_email_local("McDonald") == "McDonald"


def test_humanize_email_local_passes_through_single_token():
    assert humanize_email_local("daniel") == "daniel"


def test_humanize_email_local_passes_through_with_space():
    assert humanize_email_local("warren hamilton") == "warren hamilton"


def test_humanize_email_local_empty():
    assert humanize_email_local("") == ""
    assert humanize_email_local("   ") == ""


def test_fold_strips_diacritics():
    assert fold("Lúcia Romão") == "lucia romao"
    assert fold("  Lúcia  Romão  ") == "lucia romao"


def test_fold_lowercases():
    assert fold("Daniel Mundt") == "daniel mundt"


def test_fold_collapses_whitespace():
    assert fold("Daniel\tMundt") == "daniel mundt"


def test_fold_empty():
    assert fold("") == ""
    assert fold(None) == ""  # type: ignore


class _Client:
    def __init__(self, drawers):
        self.drawers = drawers

    async def search_drawers_with_ids(
        self, query, wing=None, room=None, limit=5, source="x"
    ):
        return self.drawers


@pytest.fixture(autouse=True)
def reset_cache():
    name_resolver._cache.replace({})
    name_resolver._cache._fetched_at = 0.0


@pytest.mark.asyncio
async def test_resolve_full_name_match():
    client = _Client(
        [{"text": "Name: Lucia Romão\nRole: Engineer"}]
    )
    await refresh_cache(client=client)
    assert await resolve("Lúcia Romão") == "Lucia Romão"


@pytest.mark.asyncio
async def test_resolve_first_name_unique_match():
    client = _Client(
        [
            {"text": "Name: Sonia Scibor"},
            {"text": "Name: Daniel Mundt"},
        ]
    )
    await refresh_cache(client=client)
    assert await resolve("Sonia") == "Sonia Scibor"


@pytest.mark.asyncio
async def test_resolve_first_name_ambiguous_returns_none():
    client = _Client(
        [
            {"text": "Name: Daniel Costa"},
            {"text": "Name: Daniel Mundt"},
        ]
    )
    await refresh_cache(client=client)
    assert await resolve("Daniel") is None


@pytest.mark.asyncio
async def test_resolve_unknown_returns_none():
    client = _Client(
        [{"text": "Name: Sonia Scibor"}]
    )
    await refresh_cache(client=client)
    assert await resolve("Stranger") is None


@pytest.mark.asyncio
async def test_canonicalise_list_dedupes_variants():
    client = _Client(
        [{"text": "Name: Lucia Romão\n"}]
    )
    await refresh_cache(client=client)
    out = await canonicalise_list(
        ["Lúcia", "Lucia Romão", "Lucia Romao"], client=client
    )
    # All three fold to the same canonical -> single entry.
    assert out == ["Lucia Romão"]


@pytest.mark.asyncio
async def test_canonicalise_list_keeps_unknowns():
    client = _Client(
        [{"text": "Name: Sonia Scibor"}]
    )
    await refresh_cache(client=client)
    out = await canonicalise_list(
        ["Sonia", "Stranger"], client=client
    )
    assert out == ["Sonia Scibor", "Stranger"]


@pytest.mark.asyncio
async def test_canonicalise_list_skips_blanks():
    client = _Client([])
    await refresh_cache(client=client)
    out = await canonicalise_list(
        ["", "  ", "Anyone"], client=client
    )
    assert out == ["Anyone"]
