"""工具注册表：core / optional / MCP / 元工具；按 unlocked 渐进披露。"""

from __future__ import annotations

import ast
import operator
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from shared.logging import get_logger

from agent_core.tools.mcp import McpToolBridge

logger = get_logger(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class Retriever(Protocol):
    async def retrieve(self, query: str) -> Any: ...


class ToolRegistry:
    """Agent 可调用的工具集合（L0 catalog / L1 schema / L2 call）。"""

    def __init__(
        self,
        retriever: Retriever | None = None,
        *,
        allowed_http_hosts: list[str] | None = None,
        skills: Any | None = None,
    ) -> None:
        self.retriever = retriever
        self.mcp = McpToolBridge()
        self.skills = skills
        self.allowed_http_hosts = set(allowed_http_hosts or [])
        self._schemas: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._tiers: dict[str, str] = {}  # core | optional | meta | mcp
        self._register_builtins()
        self._register_meta_tools()

    def register(
        self,
        name: str,
        schema: dict[str, Any],
        handler: ToolHandler,
        *,
        tier: str = "optional",
    ) -> None:
        """动态注册工具。tier: core | optional | meta | mcp。"""
        self._schemas[name] = schema
        self._handlers[name] = handler
        self._tiers[name] = tier

    def _fn_schema(
        self, name: str, description: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }

    def _register_builtins(self) -> None:
        self.register(
            "retrieve",
            self._fn_schema(
                "retrieve",
                "从企业知识库检索与问题相关的文档片段",
                {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "检索查询"}},
                    "required": ["query"],
                },
            ),
            self._handle_retrieve,
            tier="core",
        )
        self.register(
            "calculator",
            self._fn_schema(
                "calculator",
                "计算简单算术表达式，例如 2+3*4",
                {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
            ),
            self._handle_calculator,
            tier="core",
        )
        self.register(
            "http_get",
            self._fn_schema(
                "http_get",
                "对允许列表内的 HTTPS 端点发起 GET（只读）",
                {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            ),
            self._handle_http_get,
            tier="optional",
        )

    def _register_meta_tools(self) -> None:
        self.register(
            "list_skills",
            self._fn_schema(
                "list_skills",
                "列出可用 Skills 目录（L0：name + description）",
                {"type": "object", "properties": {}},
            ),
            self._handle_list_skills,
            tier="meta",
        )
        self.register(
            "activate_skill",
            self._fn_schema(
                "activate_skill",
                "激活指定 Skill：注入完整指令并解锁其声明的 tools/mcp（L1）",
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Skill 名称"},
                    },
                    "required": ["name"],
                },
            ),
            self._handle_activate_skill,
            tier="meta",
        )
        self.register(
            "list_tools",
            self._fn_schema(
                "list_tools",
                "列出工具目录（L0）；含 core/optional/mcp 与是否已解锁",
                {
                    "type": "object",
                    "properties": {
                        "unlocked": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "当前已解锁工具名（可选）",
                        },
                    },
                },
            ),
            self._handle_list_tools,
            tier="meta",
        )

    async def load_mcp_servers(self, servers_json: str) -> None:
        await self.mcp.load_from_json(servers_json)
        for schema in self.mcp.openai_tools_schema:
            name = schema["function"]["name"]
            self._schemas[name] = schema
            self._tiers[name] = "mcp"

            async def _mcp_handler(
                arguments: dict[str, Any], *, _name: str = name
            ) -> dict[str, Any]:
                return await self.mcp.call(_name, arguments)

            self._handlers[name] = _mcp_handler

    def catalog(self, *, unlocked: set[str] | None = None) -> list[dict[str, Any]]:
        """L0：name + description + tier + unlocked。"""
        unlocked = unlocked or set()
        items: list[dict[str, Any]] = []
        for name, schema in self._schemas.items():
            fn = schema.get("function") or {}
            tier = self._tiers.get(name, "optional")
            always = tier in {"core", "meta"}
            items.append(
                {
                    "name": name,
                    "description": fn.get("description") or "",
                    "tier": tier,
                    "unlocked": always or name in unlocked,
                }
            )
        return items

    def openai_tools_schema(self, unlocked: set[str] | None = None) -> list[dict[str, Any]]:
        """L1：core + meta + unlocked 的完整 schema。"""
        unlocked = unlocked or set()
        result: list[dict[str, Any]] = []
        for name, schema in self._schemas.items():
            tier = self._tiers.get(name, "optional")
            if tier in {"core", "meta"} or name in unlocked:
                result.append(schema)
        return result

    def format_catalog_prompt(self, unlocked: set[str] | None = None) -> str:
        lines = []
        for item in self.catalog(unlocked=unlocked):
            flag = "unlocked" if item["unlocked"] else "locked"
            lines.append(
                f"- {item['name']} [{item['tier']}/{flag}]: {item['description']}"
            )
        return "\n".join(lines) if lines else "（无工具）"

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if not handler:
            if name.startswith("mcp_"):
                return await self.mcp.call(name, arguments)
            return {"ok": False, "error": f"unknown tool: {name}"}
        # 元工具需要 state 上下文
        if name in {"list_tools", "activate_skill", "list_skills"}:
            return await handler({**(arguments or {}), "__state__": state or {}})
        return await handler(arguments or {})

    async def _handle_retrieve(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.retriever:
            return {"ok": False, "error": "retriever not configured"}
        result = await self.retriever.retrieve(arguments["query"])
        return {
            "ok": True,
            "context": getattr(result, "context_text", ""),
            "hits": [h.model_dump() for h in getattr(result, "hits", [])],
        }

    async def _handle_calculator(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"ok": True, "result": safe_eval_arith(arguments["expression"])}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def _handle_http_get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._http_get(arguments.get("url", ""))

    async def _handle_list_skills(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.skills:
            return {"ok": True, "skills": []}
        return {"ok": True, "skills": self.skills.catalog()}

    async def _handle_activate_skill(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.skills:
            return {"ok": False, "error": "skills not configured"}
        name = arguments.get("name") or ""
        result = self.skills.activate(name)
        if not result.get("ok"):
            return result
        state = arguments.get("__state__") or {}
        active = list(state.get("active_skills") or [])
        if name not in active:
            active.append(name)
        unlocked = set(state.get("unlocked_tools") or [])
        unlocked.update(result.get("unlocked_tools") or [])
        instructions = list(state.get("skill_instructions") or [])
        body = result.get("body") or ""
        block = f"## Skill: {name}\n{body}".strip()
        if block not in instructions:
            instructions.append(block)
        result["state_patch"] = {
            "active_skills": active,
            "unlocked_tools": sorted(unlocked),
            "skill_instructions": instructions,
            "skill_event": {"name": name, "phase": "activated"},
        }
        return result

    async def _handle_list_tools(self, arguments: dict[str, Any]) -> dict[str, Any]:
        state = arguments.get("__state__") or {}
        unlocked = set(arguments.get("unlocked") or state.get("unlocked_tools") or [])
        return {"ok": True, "tools": self.catalog(unlocked=unlocked)}

    async def _http_get(self, url: str) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return {"ok": False, "error": "only https allowed"}
        host = parsed.hostname or ""
        if self.allowed_http_hosts and host not in self.allowed_http_hosts:
            return {"ok": False, "error": f"host not allowed: {host}"}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                text = resp.text[:4000]
                return {"ok": True, "status": resp.status_code, "body": text}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def aclose(self) -> None:
        await self.mcp.aclose()


def safe_eval_arith(expr: str) -> float:
    ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.USub: operator.neg,
    }

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported expression")

    return _eval(ast.parse(expr, mode="eval"))
