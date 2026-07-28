"""工具注册表：retrieve / calculator / MCP / HTTP。"""

from __future__ import annotations

import ast
import json
import operator
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from shared.logging import get_logger

from agent_core.tools.mcp import McpToolBridge

logger = get_logger(__name__)


class Retriever(Protocol):
    async def retrieve(self, query: str) -> Any: ...


class ToolRegistry:
    """Agent 可调用的工具集合。"""

    def __init__(
        self,
        retriever: Retriever | None = None,
        *,
        allowed_http_hosts: list[str] | None = None,
    ) -> None:
        self.retriever = retriever
        self.mcp = McpToolBridge()
        self.allowed_http_hosts = set(allowed_http_hosts or [])

    async def load_mcp_servers(self, servers_json: str) -> None:
        await self.mcp.load_from_json(servers_json)

    def openai_tools_schema(self) -> list[dict[str, Any]]:
        base = [
            {
                "type": "function",
                "function": {
                    "name": "retrieve",
                    "description": "从企业知识库检索与问题相关的文档片段",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "检索查询"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "calculator",
                    "description": "计算简单算术表达式，例如 2+3*4",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {"type": "string"},
                        },
                        "required": ["expression"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "http_get",
                    "description": "对允许列表内的 HTTPS 端点发起 GET（只读）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                        },
                        "required": ["url"],
                    },
                },
            },
        ]
        return base + self.mcp.openai_tools_schema

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "retrieve":
            if not self.retriever:
                return {"ok": False, "error": "retriever not configured"}
            result = await self.retriever.retrieve(arguments["query"])
            return {
                "ok": True,
                "context": getattr(result, "context_text", ""),
                "hits": [h.model_dump() for h in getattr(result, "hits", [])],
            }
        if name == "calculator":
            try:
                return {"ok": True, "result": safe_eval_arith(arguments["expression"])}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
        if name == "http_get":
            return await self._http_get(arguments.get("url", ""))
        if name.startswith("mcp_"):
            return await self.mcp.call(name, arguments)
        return {"ok": False, "error": f"unknown tool: {name}"}

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
