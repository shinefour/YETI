"""Jira integration adapter."""

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


class JiraAdapter:
    """Jira Cloud REST API adapter."""

    def __init__(self):
        self.base_url = settings.jira_url.rstrip("/")
        self.auth = (settings.jira_email, settings.jira_api_token)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self.base_url}/rest/api/3",
            auth=self.auth,
            headers={"Accept": "application/json"},
            timeout=15,
        )

    async def health(self) -> bool:
        if not settings.jira_url or not settings.jira_api_token:
            return False
        try:
            async with self._client() as client:
                r = await client.get("/myself")
                return r.status_code == 200
        except Exception:
            logger.exception("Jira health check failed")
            return False

    async def pull(self, since: datetime) -> list[Event]:
        since_str = since.strftime("%Y-%m-%d %H:%M")
        jql = f'updated >= "{since_str}" ORDER BY updated DESC'
        async with self._client() as client:
            r = await client.get(
                "/search",
                params={
                    "jql": jql,
                    "maxResults": 50,
                    "fields": "summary,status,assignee,"
                    "updated,project,description",
                },
            )
            r.raise_for_status()
            data = r.json()

        events = []
        for issue in data.get("issues", []):
            fields = issue["fields"]
            assignee = fields.get("assignee") or {}
            project = fields.get("project") or {}
            events.append(
                Event(
                    source="jira",
                    event_type="issue_updated",
                    title=f"{issue['key']}: {fields['summary']}",
                    body=fields.get("description") or "",
                    timestamp=datetime.fromisoformat(
                        fields["updated"]
                    ),
                    metadata={
                        "key": issue["key"],
                        "status": fields["status"]["name"],
                        "assignee": assignee.get(
                            "displayName", ""
                        ),
                        "project": project.get("name", ""),
                        "url": f"{self.base_url}/browse/"
                        f"{issue['key']}",
                    },
                )
            )
        return events

    async def search(self, query: str) -> list[Item]:
        jql = (
            f'text ~ "{query}" ORDER BY updated DESC'
        )
        async with self._client() as client:
            r = await client.get(
                "/search",
                params={
                    "jql": jql,
                    "maxResults": 20,
                    "fields": "summary,status,project",
                },
            )
            r.raise_for_status()
            data = r.json()

        items = []
        for issue in data.get("issues", []):
            fields = issue["fields"]
            project = fields.get("project") or {}
            items.append(
                Item(
                    source="jira",
                    title=f"{issue['key']}: {fields['summary']}",
                    body=fields["status"]["name"],
                    url=f"{self.base_url}/browse/{issue['key']}",
                    metadata={
                        "key": issue["key"],
                        "project": project.get("name", ""),
                    },
                )
            )
        return items

    async def push(self, action) -> ActionResult:
        if action.action_type == "create_issue":
            return await self._create_issue(action.params)
        if action.action_type == "add_comment":
            return await self._add_comment(action.params)
        return ActionResult(
            success=False,
            message=f"Unknown action: {action.action_type}",
        )

    async def _create_issue(
        self, params: dict
    ) -> ActionResult:
        payload = {
            "fields": {
                "project": {"key": params["project_key"]},
                "summary": params["summary"],
                "issuetype": {
                    "name": params.get("issue_type", "Task")
                },
            }
        }
        if params.get("description"):
            payload["fields"]["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": params["description"],
                            }
                        ],
                    }
                ],
            }

        async with self._client() as client:
            r = await client.post("/issue", json=payload)
            r.raise_for_status()
            data = r.json()

        key = data["key"]
        return ActionResult(
            success=True,
            message=f"Created {key}",
            metadata={
                "key": key,
                "url": f"{self.base_url}/browse/{key}",
            },
        )

    async def _add_comment(
        self, params: dict
    ) -> ActionResult:
        issue_key = params["issue_key"]
        body = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": params["comment"],
                            }
                        ],
                    }
                ],
            }
        }
        async with self._client() as client:
            r = await client.post(
                f"/issue/{issue_key}/comment", json=body
            )
            r.raise_for_status()

        return ActionResult(
            success=True,
            message=f"Comment added to {issue_key}",
        )
