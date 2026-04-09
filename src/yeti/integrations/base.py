"""Integration adapter protocol — all integrations implement this interface."""

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel


class Event(BaseModel):
    """An event pulled from an external system."""

    source: str
    event_type: str
    title: str
    body: str
    timestamp: datetime
    metadata: dict[str, Any] = {}


class Action(BaseModel):
    """An action to push to an external system."""

    action_type: str
    params: dict[str, Any]


class ActionResult(BaseModel):
    """Result of executing an action."""

    success: bool
    message: str
    metadata: dict[str, Any] = {}


class Item(BaseModel):
    """A search result from an external system."""

    source: str
    title: str
    body: str
    url: str = ""
    metadata: dict[str, Any] = {}


class IntegrationAdapter(Protocol):
    """Common interface for all external system integrations."""

    async def pull(self, since: datetime) -> list[Event]:
        """Pull new events/changes since the given timestamp."""
        ...

    async def push(self, action: Action) -> ActionResult:
        """Execute a write action on the external system."""
        ...

    async def search(self, query: str) -> list[Item]:
        """Search the external system."""
        ...

    async def health(self) -> bool:
        """Check if the integration is connected and working."""
        ...
