"""工具注册表：core / optional / MCP / webhook / 元工具；按 unlocked 渐进披露。"""

from __future__ import annotations

import ast
import operator
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from shared.config import settings
from shared.logging import get_logger

from agent_core.tools.guard import (
    CallIdempotencyCache,
    check_visibility,
    normalize_result,
    tool_error,
    validate_arguments,
)
from agent_core.tools.mcp import McpToolBridge

logger = get_logger(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class Retriever(Protocol):
    async def retrieve(self, query: str, *, scope: dict[str, Any] | None = None) -> Any: ...


class ToolRegistry:
    """Agent 可调用的工具集合（L0 catalog / L1 schema / L2 call + 门禁）。"""

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
        self._tiers: dict[str, str] = {}  # core | optional | meta | mcp | webhook
        self._sources: dict[str, str] = {}  # builtin | meta | webhook | mcp
        self._enabled: dict[str, bool] = {}
        self._webhook_meta: dict[str, dict[str, Any]] = {}
        self._idempotency = CallIdempotencyCache()
        self._register_builtins()
        self._register_meta_tools()

    def register(
        self,
        name: str,
        schema: dict[str, Any],
        handler: ToolHandler,
        *,
        tier: str = "optional",
        source: str = "builtin",
        enabled: bool = True,
    ) -> None:
        """动态注册工具。tier: core | optional | meta | mcp | webhook。"""
        self._schemas[name] = schema
        self._handlers[name] = handler
        self._tiers[name] = tier
        self._sources[name] = source
        self._enabled[name] = enabled

    def unregister(self, name: str) -> bool:
        """卸载工具（主要用于 webhook / mcp）。"""
        if name not in self._schemas:
            return False
        self._schemas.pop(name, None)
        self._handlers.pop(name, None)
        self._tiers.pop(name, None)
        self._sources.pop(name, None)
        self._enabled.pop(name, None)
        self._webhook_meta.pop(name, None)
        return True

    def set_enabled(self, name: str, enabled: bool) -> bool:
        if name not in self._schemas:
            return False
        self._enabled[name] = bool(enabled)
        return True

    def apply_enabled_flags(self, flags: dict[str, bool]) -> None:
        for name, enabled in flags.items():
            if name in self._schemas:
                self._enabled[name] = bool(enabled)

    def register_webhook(
        self,
        name: str,
        *,
        description: str,
        parameters: dict[str, Any] | None = None,
        webhook_url: str,
        webhook_method: str = "POST",
        webhook_headers: dict[str, Any] | None = None,
        timeout_sec: float = 30.0,
        tier: str = "optional",
        enabled: bool = True,
    ) -> None:
        """注册 HTTP Webhook 工具：call 时将 arguments 作为 JSON body POST/PUT。"""
        params = parameters or {"type": "object", "properties": {}, "additionalProperties": False}
        if "additionalProperties" not in params:
            params = {**params, "additionalProperties": False}
        schema = self._fn_schema(name, description or f"Webhook tool {name}", params)
        meta = {
            "url": webhook_url,
            "method": (webhook_method or "POST").upper(),
            "headers": dict(webhook_headers or {}),
            "timeout_sec": float(timeout_sec or 30.0),
        }
        self._webhook_meta[name] = meta

        async def _handler(arguments: dict[str, Any], *, _name: str = name) -> dict[str, Any]:
            return await self._call_webhook(_name, arguments)

        self.register(
            name,
            schema,
            _handler,
            tier=tier if tier in {"optional", "core", "meta"} else "optional",
            source="webhook",
            enabled=enabled,
        )

    def sync_webhooks(self, records: list[dict[str, Any]]) -> None:
        """用 Store 记录替换全部 webhook 工具。"""
        wanted = {str(r["name"]) for r in records if r.get("name")}
        for name in list(self._webhook_meta.keys()):
            if name not in wanted:
                self.unregister(name)
        for rec in records:
            name = str(rec.get("name") or "").strip()
            if not name:
                continue
            self.register_webhook(
                name,
                description=str(rec.get("description") or ""),
                parameters=rec.get("parameters") if isinstance(rec.get("parameters"), dict) else None,
                webhook_url=str(rec.get("webhook_url") or ""),
                webhook_method=str(rec.get("webhook_method") or "POST"),
                webhook_headers=rec.get("webhook_headers")
                if isinstance(rec.get("webhook_headers"), dict)
                else None,
                timeout_sec=float(rec.get("timeout_sec") or 30.0),
                tier=str(rec.get("tier") or "optional"),
                enabled=bool(rec.get("enabled", True)),
            )

    def clear_mcp_tools(self) -> None:
        for name in [n for n, s in self._sources.items() if s == "mcp"]:
            self.unregister(name)

    def ingest_mcp_schemas(self) -> None:
        """将当前 McpToolBridge 已发现工具挂入 registry。"""
        self.clear_mcp_tools()
        for schema in self.mcp.openai_tools_schema:
            name = (schema.get("function") or {}).get("name")
            if not name:
                continue

            async def _mcp_handler(
                arguments: dict[str, Any], *, _name: str = name
            ) -> dict[str, Any]:
                return await self.mcp.call(_name, arguments)

            self.register(name, schema, _mcp_handler, tier="mcp", source="mcp", enabled=True)

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
                "从企业知识库检索与问题相关的文档片段。仅在需要内部政策/流程/知识时调用；"
                "query 应为简洁检索句，禁止空字符串。",
                {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "minLength": 1,
                            "description": "检索查询（非空）",
                        }
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            self._handle_retrieve,
            tier="core",
            source="builtin",
        )
        self.register(
            "calculator",
            self._fn_schema(
                "calculator",
                "计算简单算术表达式（+ - * / 与括号），例如 2+3*4。不要用于文字推理。",
                {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "minLength": 1,
                            "description": "算术表达式，仅数字与运算符",
                        }
                    },
                    "required": ["expression"],
                    "additionalProperties": False,
                },
            ),
            self._handle_calculator,
            tier="core",
            source="builtin",
        )
        self.register(
            "http_get",
            self._fn_schema(
                "http_get",
                "对允许列表内的 http(s) 端点发起只读 GET。url 必须含协议与主机。",
                {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "minLength": 8,
                            "description": "完整 URL，如 https://example.com/path",
                        }
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            ),
            self._handle_http_get,
            tier="optional",
            source="builtin",
        )
        self.register(
            "web_search",
            self._fn_schema(
                "web_search",
                "对公开互联网关键词检索，返回标题/链接/摘要（需 WEB_SEARCH_API_KEY）。"
                "仅公开信息场景使用；企业内部问题优先 retrieve。",
                {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "minLength": 1,
                            "description": "搜索查询（非空）",
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "description": "返回条数 1-20，默认取配置",
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            self._handle_web_search,
            tier="optional",
            source="builtin",
        )

    def _register_meta_tools(self) -> None:
        self.register(
            "list_skills",
            self._fn_schema(
                "list_skills",
                "列出可用 Skills 目录（L0：name + description）。无参数。",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            self._handle_list_skills,
            tier="meta",
            source="meta",
        )
        self.register(
            "activate_skill",
            self._fn_schema(
                "activate_skill",
                "激活指定 Skill：注入完整指令并解锁其声明的 tools/mcp（L1）。"
                "name 必须来自 list_skills 目录中的已有 Skill。",
                {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Skill 名称（非空，须存在于目录）",
                        },
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            ),
            self._handle_activate_skill,
            tier="meta",
            source="meta",
        )
        self.register(
            "list_tools",
            self._fn_schema(
                "list_tools",
                "列出工具目录（L0）；含 core/optional/mcp 与是否已解锁。",
                {
                    "type": "object",
                    "properties": {
                        "unlocked": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "当前已解锁工具名（可选）",
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            self._handle_list_tools,
            tier="meta",
            source="meta",
        )

    async def load_mcp_servers(self, servers_json: str) -> None:
        await self.mcp.load_from_json(servers_json)
        self.ingest_mcp_schemas()

    async def reload_mcp_configs(self, configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按配置列表全量重连 MCP，并刷新 registry 中的 mcp 工具。"""
        results = await self.mcp.reload_servers(configs)
        self.ingest_mcp_schemas()
        return results

    def catalog(self, *, unlocked: set[str] | None = None) -> list[dict[str, Any]]:
        """L0：name + description + tier + unlocked（跳过 disabled）。"""
        unlocked = unlocked or set()
        items: list[dict[str, Any]] = []
        for name, schema in self._schemas.items():
            if not self._enabled.get(name, True):
                continue
            fn = schema.get("function") or {}
            tier = self._tiers.get(name, "optional")
            always = tier in {"core", "meta"}
            items.append(
                {
                    "name": name,
                    "description": fn.get("description") or "",
                    "tier": tier,
                    "source": self._sources.get(name, "builtin"),
                    "enabled": True,
                    "mutable": self._sources.get(name) == "webhook",
                    "unlocked": always or name in unlocked,
                }
            )
        return items

    def admin_catalog(self) -> list[dict[str, Any]]:
        """管理台列表：含 disabled；附带 webhook 元数据。"""
        items: list[dict[str, Any]] = []
        for name, schema in self._schemas.items():
            fn = schema.get("function") or {}
            source = self._sources.get(name, "builtin")
            item: dict[str, Any] = {
                "name": name,
                "description": fn.get("description") or "",
                "tier": self._tiers.get(name, "optional"),
                "source": source,
                "enabled": self._enabled.get(name, True),
                "mutable": source == "webhook",
                "parameters": fn.get("parameters") or {},
            }
            if source == "webhook" and name in self._webhook_meta:
                meta = self._webhook_meta[name]
                item.update(
                    {
                        "webhook_url": meta.get("url"),
                        "webhook_method": meta.get("method"),
                        "webhook_headers": meta.get("headers") or {},
                        "timeout_sec": meta.get("timeout_sec"),
                    }
                )
            items.append(item)
        return items

    def openai_tools_schema(self, unlocked: set[str] | None = None) -> list[dict[str, Any]]:
        """L1：core + meta + unlocked 的完整 schema（跳过 disabled）。"""
        unlocked = unlocked or set()
        result: list[dict[str, Any]] = []
        for name, schema in self._schemas.items():
            if not self._enabled.get(name, True):
                continue
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
        arguments: dict[str, Any] | None = None,
        *,
        state: dict[str, Any] | None = None,
        call_id: str | None = None,
    ) -> dict[str, Any]:
        """L2 执行：可见性 → 参数 schema →（幂等）执行 → 结果契约。"""
        cached = self._idempotency.get(call_id)
        if cached is not None:
            return {**cached, "idempotent_replay": True}

        state = state or {}
        unlocked = set(state.get("unlocked_tools") or [])
        in_registry = name in self._schemas
        handler = self._handlers.get(name)

        if not in_registry and handler is None and not name.startswith("mcp_"):
            err = tool_error(
                error=f"unknown tool: {name}",
                error_code="unknown_tool",
                fixable=True,
                hint="先 list_tools / activate_skill 解锁后再调用；勿调用未披露工具。",
            )
            self._idempotency.put(call_id, err)
            return err

        tier = self._tiers.get(name) or ("mcp" if name.startswith("mcp_") else "optional")
        enabled = self._enabled.get(name, True) if in_registry else True
        gate = check_visibility(
            name=name,
            tier=tier,
            enabled=enabled,
            registered=True,
            unlocked=unlocked,
        )
        if gate is not None:
            self._idempotency.put(call_id, gate)
            return gate

        arg_err = validate_arguments(name, arguments or {}, self._schemas.get(name))
        if arg_err is not None:
            self._idempotency.put(call_id, arg_err)
            return arg_err

        try:
            if handler is None and name.startswith("mcp_"):
                raw = await self.mcp.call(name, arguments or {})
            elif handler is None:
                raw = tool_error(
                    error=f"unknown tool: {name}",
                    error_code="unknown_tool",
                    fixable=True,
                )
            elif name in {"list_tools", "activate_skill", "list_skills", "retrieve"}:
                raw = await handler({**(arguments or {}), "__state__": state})
            else:
                raw = await handler(arguments or {})
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool %s raised", name)
            raw = tool_error(
                error=str(exc),
                error_code="tool_exception",
                fixable=False,
            )

        result = normalize_result(name, raw)
        self._idempotency.put(call_id, result)
        return result

    async def _call_webhook(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        meta = self._webhook_meta.get(name)
        if not meta:
            return {"ok": False, "error": f"webhook meta missing: {name}"}
        url = str(meta.get("url") or "")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return {"ok": False, "error": "webhook url must be http(s)"}
        host = parsed.hostname or ""
        if self.allowed_http_hosts and host not in self.allowed_http_hosts:
            return {"ok": False, "error": f"host not allowed: {host}"}
        method = str(meta.get("method") or "POST").upper()
        headers = dict(meta.get("headers") or {})
        headers.setdefault("Content-Type", "application/json")
        timeout = float(meta.get("timeout_sec") or 30.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(method, url, json=arguments or {}, headers=headers)
                text = resp.text[:8000]
                try:
                    body: Any = resp.json()
                except Exception:  # noqa: BLE001
                    body = text
                if resp.is_error:
                    return {
                        "ok": False,
                        "error": f"webhook status {resp.status_code}",
                        "status": resp.status_code,
                        "body": body,
                    }
                return {"ok": True, "status": resp.status_code, "body": body}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def _handle_retrieve(self, arguments: dict[str, Any]) -> dict[str, Any]:
        state = arguments.get("__state__") or {}
        if state.get("enable_rag") is False:
            return {
                "ok": True,
                "skipped": True,
                "context": "",
                "hits": [],
                "error": "rag disabled by routing",
            }
        if not self.retriever:
            return {"ok": False, "error": "retriever not configured"}
        result = await self.retriever.retrieve(
            arguments["query"],
            scope=state.get("retrieval_scope"),
        )
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

    async def _handle_web_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "query required"}
        max_results = arguments.get("max_results")
        try:
            limit = int(max_results) if max_results is not None else int(settings.web_search_max_results)
        except (TypeError, ValueError):
            limit = int(settings.web_search_max_results)
        limit = max(1, min(limit, 20))
        return await self._web_search(query, max_results=limit)

    async def _web_search(self, query: str, *, max_results: int) -> dict[str, Any]:
        provider = (settings.web_search_provider or "tavily").strip().lower()
        api_key = (settings.web_search_api_key or "").strip()
        if not api_key:
            return {
                "ok": False,
                "error": "WEB_SEARCH_API_KEY not configured",
                "provider": provider,
            }
        if provider != "tavily":
            return {"ok": False, "error": f"unsupported web_search provider: {provider}"}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "max_results": max_results,
                        "include_answer": False,
                        "search_depth": "basic",
                    },
                )
                if resp.is_error:
                    return {
                        "ok": False,
                        "error": f"tavily status {resp.status_code}",
                        "body": resp.text[:1000],
                    }
                data = resp.json()
                results = []
                for item in data.get("results") or []:
                    results.append(
                        {
                            "title": item.get("title") or "",
                            "url": item.get("url") or "",
                            "snippet": item.get("content") or item.get("snippet") or "",
                        }
                    )
                return {"ok": True, "provider": "tavily", "query": query, "results": results}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "provider": provider}

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
