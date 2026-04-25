"""MemPalace MCP client — communicates with mempalace via stdio subprocess."""

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


_usage_store = None


def _usage():
    """Lazy-singleton usage store. Returns None if init fails."""
    global _usage_store
    if _usage_store is None:
        try:
            from yeti.memory.usage import UsageStore

            _usage_store = UsageStore()
        except Exception:
            logger.exception("UsageStore init failed")
            _usage_store = False  # sentinel — give up trying
    return _usage_store or None


def _log_search(query: str, source: str) -> None:
    s = _usage()
    if s is not None:
        try:
            s.log_search(query, source)
        except Exception:
            logger.exception("usage log_search failed")


def _log_kg(entity: str, source: str) -> None:
    s = _usage()
    if s is not None:
        try:
            s.log_kg_query(entity, source)
        except Exception:
            logger.exception("usage log_kg_query failed")


def _log_drawer_hits(
    ids: list[str], source: str, query: str | None = None
) -> None:
    s = _usage()
    if s is not None:
        try:
            s.log_drawer_hits(ids, source, query)
        except Exception:
            logger.exception("usage log_drawer_hits failed")

# All known mempalace MCP tools and their status
TOOLS = {
    # Implemented — wired up and available
    "mempalace_status": {
        "description": "Palace overview — total drawers, wing and room counts",
        "implemented": True,
    },
    "mempalace_list_wings": {
        "description": "List all wings with drawer counts",
        "implemented": True,
    },
    "mempalace_list_rooms": {
        "description": "List rooms within a wing (or all rooms)",
        "implemented": True,
    },
    "mempalace_search": {
        "description": "Semantic search returning verbatim drawer content",
        "implemented": True,
    },
    "mempalace_add_drawer": {
        "description": "File verbatim content into the palace",
        "implemented": True,
    },
    "mempalace_check_duplicate": {
        "description": "Check if content already exists before filing",
        "implemented": True,
    },
    "mempalace_kg_query": {
        "description": "Query the knowledge graph for an entity's relationships",
        "implemented": True,
    },
    "mempalace_kg_add": {
        "description": "Add a fact to the knowledge graph with optional time window",
        "implemented": True,
    },
    "mempalace_get_taxonomy": {
        "description": "Full taxonomy: wing -> room -> drawer count",
        "implemented": True,
    },
    "mempalace_kg_invalidate": {
        "description": "Mark a fact as no longer true",
        "implemented": True,
    },
    "mempalace_kg_timeline": {
        "description": "Chronological timeline of facts",
        "implemented": True,
    },
    "mempalace_delete_drawer": {
        "description": "Delete a drawer by ID",
        "implemented": True,
    },
    # Not yet wired — agent should suggest these when useful
    "mempalace_get_aaak_spec": {
        "description": "Get the AAAK dialect specification",
        "implemented": False,
    },
    "mempalace_kg_stats": {
        "description": "Knowledge graph overview: entities, triples, types",
        "implemented": False,
    },
    "mempalace_traverse": {
        "description": "Walk the palace graph from a room across wings",
        "implemented": False,
    },
    "mempalace_find_tunnels": {
        "description": "Find rooms that bridge two wings",
        "implemented": False,
    },
    "mempalace_graph_stats": {
        "description": "Palace graph overview: rooms, tunnels, edges",
        "implemented": False,
    },
    "mempalace_diary_write": {
        "description": "Write to personal agent diary in AAAK format",
        "implemented": False,
    },
    "mempalace_diary_read": {
        "description": "Read recent diary entries",
        "implemented": False,
    },
}


class MemPalaceClient:
    """MCP client that spawns mempalace as a subprocess."""

    def __init__(self, palace_path: str = "/data/mempalace"):
        self.palace_path = palace_path
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._initialized = False
        self._lock = asyncio.Lock()

    async def _ensure_running(self):
        """Start the MCP server subprocess and complete the handshake."""
        if (
            self._process
            and self._process.returncode is None
            and self._initialized
        ):
            return

        env = os.environ.copy()
        env["MEMPALACE_PALACE_PATH"] = self.palace_path

        self._process = await asyncio.create_subprocess_exec(
            "python",
            "-m",
            "mempalace.mcp_server",
            "--palace",
            self.palace_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # MCP handshake: initialize -> notifications/initialized
        self._request_id += 1
        init_request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "yeti",
                    "version": "0.1.0",
                },
            },
        }
        await self._write_line(init_request)
        init_response = await self._read_response()
        logger.info(
            "MemPalace initialized: %s",
            init_response.get("result", {}).get(
                "serverInfo", {}
            ),
        )

        # Send initialized notification (no response expected)
        await self._write_line(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )

        self._initialized = True

    async def _write_line(self, payload: dict) -> None:
        line = json.dumps(payload) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

    async def _read_response(self) -> dict:
        """Read a JSON-RPC response from stdout."""
        line = await asyncio.wait_for(
            self._process.stdout.readline(),
            timeout=30,
        )
        if not line:
            raise ConnectionError(
                "MemPalace process closed unexpectedly"
            )
        return json.loads(line.decode())

    async def _send_request(
        self, method: str, params: dict | None = None
    ) -> dict:
        """Send a JSON-RPC request to the MCP server."""
        async with self._lock:
            await self._ensure_running()

            self._request_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params or {},
            }
            await self._write_line(request)
            return await self._read_response()

    @staticmethod
    def _unwrap_result(response: dict) -> Any:
        """Unwrap MCP tool result content."""
        if "error" in response:
            return {"error": response["error"]}

        result = response.get("result", {})
        content = result.get("content", [])
        if not content:
            return result

        text = content[0].get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    async def call_tool(
        self, tool_name: str, arguments: dict | None = None
    ) -> dict[str, Any]:
        """Call a mempalace MCP tool by name."""
        tool_info = TOOLS.get(tool_name)

        if not tool_info:
            return {
                "error": f"Unknown tool: {tool_name}",
                "available_tools": list(TOOLS.keys()),
            }

        if not tool_info["implemented"]:
            return {
                "error": "not_implemented",
                "tool": tool_name,
                "description": tool_info["description"],
                "message": (
                    f"The '{tool_name}' tool exists in MemPalace "
                    f"but isn't wired up in YETI yet. "
                    f"It would: {tool_info['description']}. "
                    f"Consider adding it if this capability "
                    f"would be useful."
                ),
            }

        try:
            response = await self._send_request(
                "tools/call",
                {
                    "name": tool_name,
                    "arguments": arguments or {},
                },
            )
            return self._unwrap_result(response)
        except Exception as e:
            logger.exception(
                "MemPalace tool call failed: %s", tool_name
            )
            return {"error": str(e)}

    # --- Convenience methods ---

    async def search(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        limit: int = 5,
        source: str = "unknown",
    ) -> dict:
        args = {"query": query, "limit": limit}
        if wing:
            args["wing"] = wing
        if room:
            args["room"] = room
        result = await self.call_tool("mempalace_search", args)
        _log_search(query, source)
        return result

    async def store(
        self,
        content: str,
        wing: str,
        room: str,
        source: str = "yeti",
    ) -> dict:
        return await self.call_tool(
            "mempalace_add_drawer",
            {
                "wing": wing,
                "room": room,
                "content": content,
                "added_by": source,
            },
        )

    async def status(self) -> dict:
        return await self.call_tool("mempalace_status")

    async def list_wings(self) -> dict:
        return await self.call_tool("mempalace_list_wings")

    async def list_rooms(self, wing: str | None = None) -> dict:
        args = {}
        if wing:
            args["wing"] = wing
        return await self.call_tool("mempalace_list_rooms", args)

    async def check_duplicate(
        self, content: str, threshold: float = 0.9
    ) -> dict:
        return await self.call_tool(
            "mempalace_check_duplicate",
            {"content": content, "threshold": threshold},
        )

    async def kg_query(
        self,
        entity: str,
        as_of: str | None = None,
        source: str = "unknown",
    ) -> dict:
        args = {"entity": entity}
        if as_of:
            args["as_of"] = as_of
        result = await self.call_tool("mempalace_kg_query", args)
        _log_kg(entity, source)
        return result

    async def kg_add(
        self,
        subject: str,
        predicate: str,
        obj: str,
        valid_from: str | None = None,
    ) -> dict:
        args = {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
        }
        if valid_from:
            args["valid_from"] = valid_from
        return await self.call_tool("mempalace_kg_add", args)

    async def kg_invalidate(
        self,
        subject: str,
        predicate: str,
        obj: str,
        ended: str | None = None,
    ) -> dict:
        args = {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
        }
        if ended:
            args["ended"] = ended
        return await self.call_tool(
            "mempalace_kg_invalidate", args
        )

    async def kg_timeline(
        self, entity: str | None = None
    ) -> dict:
        args = {}
        if entity:
            args["entity"] = entity
        return await self.call_tool(
            "mempalace_kg_timeline", args
        )

    async def get_taxonomy(self) -> dict:
        return await self.call_tool(
            "mempalace_get_taxonomy"
        )

    async def delete_drawer(self, drawer_id: str) -> dict:
        return await self.call_tool(
            "mempalace_delete_drawer",
            {"drawer_id": drawer_id},
        )

    async def search_drawers_with_ids(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        limit: int = 5,
        source: str = "unknown",
    ) -> list[dict]:
        """Search drawers and return results WITH drawer IDs.

        Hits ChromaDB directly since the MCP search tool doesn't
        expose IDs. Each result has: id, text, wing, room, metadata.
        """
        import chromadb

        try:
            client = chromadb.PersistentClient(
                path=self.palace_path
            )
            col = client.get_collection("mempalace_drawers")

            where = {}
            if wing:
                where["wing"] = wing
            if room:
                where["room"] = room

            result = col.query(
                query_texts=[query],
                n_results=min(limit, col.count()),
                where=where or None,
                include=["documents", "metadatas", "distances"],
            )

            items = []
            for i, doc in enumerate(result["documents"][0]):
                meta = result["metadatas"][0][i]
                items.append(
                    {
                        "id": result["ids"][0][i],
                        "text": doc[:500],
                        "wing": meta.get("wing", ""),
                        "room": meta.get("room", ""),
                        "metadata": meta,
                        "distance": result["distances"][0][i],
                    }
                )

            # Filter drawers superseded by the sleep dedupe pass.
            try:
                from yeti.models.superseded import SupersededStore

                blocked = SupersededStore().superseded_ids()
                if blocked:
                    items = [
                        it
                        for it in items
                        if it["id"] not in blocked
                    ]
            except Exception:
                logger.exception(
                    "Failed to filter superseded drawers"
                )

            _log_drawer_hits(
                [it["id"] for it in items], source, query
            )
            return items
        except Exception:
            logger.exception("Direct ChromaDB search failed")
            return []

    async def close(self):
        """Shut down the MCP server subprocess."""
        if (
            self._process
            and self._process.returncode is None
        ):
            self._process.terminate()
            await self._process.wait()
        self._initialized = False

    def get_unimplemented_tools(self) -> list[dict]:
        """List tools that exist but aren't wired up yet."""
        return [
            {"name": name, "description": info["description"]}
            for name, info in TOOLS.items()
            if not info["implemented"]
        ]
