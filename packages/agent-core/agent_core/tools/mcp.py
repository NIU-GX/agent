"""MCP 客户端适配：把外部 MCP Server 的 tools 挂到 ToolRegistry。"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)


class McpToolBridge:
    """可选依赖 mcp；配置为空时 no-op。"""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._entered = False
        self._schemas: list[dict[str, Any]] = []
        self._handlers: dict[str, Any] = {}

    @property
    def openai_tools_schema(self) -> list[dict[str, Any]]:
        return list(self._schemas)

    async def load_from_json(self, servers_json: str) -> None:
        try:
            servers = json.loads(servers_json or "[]")
        except json.JSONDecodeError:
            logger.error("invalid mcp_servers_json")
            return
        if not servers:
            return
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            logger.warning("mcp package not installed; skip MCP tool loading")
            return

        if not self._entered:
            await self._stack.__aenter__()
            self._entered = True

        for cfg in servers:
            name = cfg.get("name") or "mcp"
            command = cfg.get("command")
            args = cfg.get("args") or []
            if not command:
                continue
            try:
                params = StdioServerParameters(
                    command=command, args=args, env=cfg.get("env")
                )
                read, write = await self._stack.enter_async_context(stdio_client(params))
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = await session.list_tools()
                for tool in listed.tools:
                    tool_name = f"mcp_{name}_{tool.name}"
                    self._schemas.append(
                        {
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "description": tool.description or f"MCP tool {tool.name}",
                                "parameters": tool.inputSchema
                                or {"type": "object", "properties": {}},
                            },
                        }
                    )
                    self._handlers[tool_name] = (session, tool.name)
                logger.info("mcp server loaded name=%s tools=%s", name, len(listed.tools))
            except Exception as exc:  # noqa: BLE001
                logger.exception("failed to load mcp server %s: %s", name, exc)

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        pair = self._handlers.get(name)
        if not pair:
            return {"ok": False, "error": f"unknown mcp tool: {name}"}
        session, tool_name = pair
        try:
            result = await session.call_tool(tool_name, arguments)
            content = []
            for item in getattr(result, "content", []) or []:
                text = getattr(item, "text", None)
                if text:
                    content.append(text)
                else:
                    content.append(str(item))
            return {"ok": True, "result": "\n".join(content) if content else str(result)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def aclose(self) -> None:
        if self._entered:
            await self._stack.aclose()
            self._entered = False
        self._handlers.clear()
        self._schemas.clear()
