"""Notion integration adapter."""

import logging
from datetime import datetime

import httpx

from yeti.config import settings
from yeti.integrations.base import (
    ActionResult,
    Event,
    Item,
)

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionAdapter:
    """Notion API adapter."""

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=NOTION_API,
            headers={
                "Authorization": f"Bearer {settings.notion_api_key}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=15,
        )

    async def health(self) -> bool:
        if not settings.notion_api_key:
            return False
        try:
            async with self._client() as client:
                r = await client.get("/users/me")
                return r.status_code == 200
        except Exception:
            logger.exception("Notion health check failed")
            return False

    async def pull(self, since: datetime) -> list[Event]:
        """Search for recently edited pages."""
        async with self._client() as client:
            r = await client.post(
                "/search",
                json={
                    "filter": {"property": "object", "value": "page"},
                    "sort": {
                        "direction": "descending",
                        "timestamp": "last_edited_time",
                    },
                    "page_size": 50,
                },
            )
            r.raise_for_status()
            data = r.json()

        events = []
        for page in data.get("results", []):
            edited = datetime.fromisoformat(
                page["last_edited_time"]
            )
            if edited < since:
                continue

            title = _extract_title(page)
            events.append(
                Event(
                    source="notion",
                    event_type="page_updated",
                    title=title,
                    body="",
                    timestamp=edited,
                    metadata={
                        "page_id": page["id"],
                        "url": page.get("url", ""),
                    },
                )
            )
        return events

    async def search(self, query: str) -> list[Item]:
        async with self._client() as client:
            r = await client.post(
                "/search",
                json={
                    "query": query,
                    "page_size": 20,
                },
            )
            r.raise_for_status()
            data = r.json()

        items = []
        for result in data.get("results", []):
            title = _extract_title(result)
            items.append(
                Item(
                    source="notion",
                    title=title,
                    body=result.get("object", ""),
                    url=result.get("url", ""),
                    metadata={"id": result["id"]},
                )
            )
        return items

    async def push(self, action) -> ActionResult:
        if action.action_type == "create_page":
            return await self._create_page(action.params)
        return ActionResult(
            success=False,
            message=f"Unknown action: {action.action_type}",
        )

    async def _create_page(
        self, params: dict
    ) -> ActionResult:
        payload = {
            "parent": {"database_id": params["database_id"]},
            "properties": {
                "title": {
                    "title": [
                        {"text": {"content": params["title"]}}
                    ]
                }
            },
        }
        async with self._client() as client:
            r = await client.post("/pages", json=payload)
            r.raise_for_status()
            data = r.json()

        return ActionResult(
            success=True,
            message=f"Created page: {params['title']}",
            metadata={
                "page_id": data["id"],
                "url": data.get("url", ""),
            },
        )


def _extract_title(page: dict) -> str:
    """Extract title from a Notion page object."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            title_items = prop.get("title", [])
            if title_items:
                return title_items[0].get("plain_text", "Untitled")
    return "Untitled"
