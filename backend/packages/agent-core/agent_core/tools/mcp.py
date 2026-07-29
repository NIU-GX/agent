"""MCP 客户端适配：stdio 连接，缓存完整 schema，按 unlocked 渐进披露。"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)


class McpToolBridge:
    """可选依赖 mcp；配置为空时 no-op。支持按配置列表全量重连。"""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._entered = False
        self._schemas: list[dict[str, Any]] = []
        self._handlers: dict[str, Any] = {}
        self._servers: list[dict[str, Any]] = []
        self._configs: list[dict[str, Any]] = []

    @property
    def openai_tools_schema(self) -> list[dict[str, Any]]:
        """全部已发现 MCP 工具的完整 schema（披露过滤在 ToolRegistry）。"""
        return list(self._schemas)

    def catalog(self) -> list[dict[str, Any]]:
        """L0：按 server 分组的 tool name + description。"""
        return list(self._servers)

    def openai_tools_schema_filtered(self, unlocked: set[str]) -> list[dict[str, Any]]:
        return [
            s
            for s in self._schemas
            if (s.get("function") or {}).get("name") in unlocked
        ]

    async def load_from_json(self, servers_json: str) -> None:
        try:
            servers = json.loads(servers_json or "[]")
        except json.JSONDecodeError:
            logger.error("invalid mcp_servers_json")
            return
        if not isinstance(servers, list):
            return
        await self.reload_servers(servers)

    async def reload_servers(self, configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """全量关闭后按 enabled 配置重连；返回每个 server 的连接结果（含 error）。"""
        await self.aclose()
        self._configs = [dict(c) for c in configs if isinstance(c, dict)]
        results: list[dict[str, Any]] = []
        enabled = [
            c
            for c in self._configs
            if c.get("enabled", True) and str(c.get("command") or "").strip()
        ]
        if not enabled:
            return results
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            logger.warning("mcp package not installed; skip MCP tool loading")
            for cfg in enabled:
                entry = {
                    "name": cfg.get("name") or "mcp",
                    "command": cfg.get("command"),
                    "args": cfg.get("args") or [],
                    "tools": [],
                    "error": "mcp package not installed",
                }
                self._servers.append(entry)
                results.append(entry)
            return results

        if not self._entered:
            await self._stack.__aenter__()
            self._entered = True

        for cfg in enabled:
            entry = await self._connect_one(cfg, StdioServerParameters, stdio_client, ClientSession)
            results.append(entry)
        return results

    async def reconnect(self, name: str) -> dict[str, Any]:
        """按名称重连单个 server：实现为全量 reload（保留其它配置）。"""
        if not self._configs:
            return {"name": name, "ok": False, "error": "no mcp configs loaded"}
        results = await self.reload_servers(self._configs)
        for item in results:
            if item.get("name") == name:
                return {
                    "name": name,
                    "ok": "error" not in item,
                    "error": item.get("error"),
                    "tools": item.get("tools") or [],
                }
        # 可能被 disabled
        for cfg in self._configs:
            if cfg.get("name") == name:
                if not cfg.get("enabled", True):
                    return {"name": name, "ok": False, "error": "server disabled"}
                return {"name": name, "ok": False, "error": "reconnect failed"}
        return {"name": name, "ok": False, "error": "server not found"}

    async def _connect_one(
        self,
        cfg: dict[str, Any],
        StdioServerParameters: Any,
        stdio_client: Any,
        ClientSession: Any,
    ) -> dict[str, Any]:
        name = cfg.get("name") or "mcp"
        command = cfg.get("command")
        args = cfg.get("args") or []
        server_entry: dict[str, Any] = {
            "name": name,
            "command": command,
            "args": args,
            "tools": [],
        }
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
                desc = tool.description or f"MCP tool {tool.name}"
                self._schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "description": desc,
                            "parameters": tool.inputSchema
                            or {"type": "object", "properties": {}},
                        },
                    }
                )
                self._handlers[tool_name] = (session, tool.name)
                server_entry["tools"].append(
                    {
                        "name": tool_name,
                        "mcp_tool": tool.name,
                        "description": desc,
                    }
                )
            self._servers.append(server_entry)
            logger.info("mcp server loaded name=%s tools=%s", name, len(listed.tools))
        except Exception as exc:  # noqa: BLE001
            logger.exception("failed to load mcp server %s: %s", name, exc)
            server_entry["error"] = str(exc)
            self._servers.append(server_entry)
        return server_entry

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
            try:
                await self._stack.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning("mcp stack close error: %s", exc)
            self._entered = False
            self._stack = AsyncExitStack()
        self._handlers.clear()
        self._schemas.clear()
        self._servers.clear()
