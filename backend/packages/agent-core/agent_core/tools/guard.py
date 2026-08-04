"""工具调用正确性门禁：事前 schema、事中校验/可见性、事后结果契约。"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)

# 始终可调用（无需 unlocked）
ALWAYS_ALLOWED_TIERS = frozenset({"core", "meta"})

# 工具结果必须至少具备的字段
RESULT_REQUIRED_KEYS = frozenset({"ok"})


def tool_error(
    *,
    error: str,
    error_code: str,
    fixable: bool = False,
    hint: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一可回灌模型的错误结构。"""
    out: dict[str, Any] = {
        "ok": False,
        "error": error,
        "error_code": error_code,
        "fixable": fixable,
    }
    if hint:
        out["hint"] = hint
    if details:
        out["details"] = details
    return out


def parameters_schema(openai_tool_schema: dict[str, Any] | None) -> dict[str, Any]:
    """从 OpenAI function schema 取出 parameters JSON Schema。"""
    if not openai_tool_schema:
        return {"type": "object", "properties": {}}
    fn = openai_tool_schema.get("function") or {}
    params = fn.get("parameters")
    if isinstance(params, dict) and params:
        return params
    return {"type": "object", "properties": {}}


def validate_arguments(
    name: str,
    arguments: Any,
    openai_tool_schema: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """事中参数校验。通过返回 None，失败返回 tool_error dict。"""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return tool_error(
            error=f"arguments must be a JSON object for tool {name}",
            error_code="invalid_args_type",
            fixable=True,
            hint="将 arguments 改为 JSON 对象，例如 {\"query\": \"...\"}",
        )

    schema = parameters_schema(openai_tool_schema)
    try:
        import jsonschema
        from jsonschema import Draft202012Validator
    except ImportError:
        return _lightweight_validate(name, arguments, schema)

    try:
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(arguments), key=lambda e: list(e.path))
    except Exception as exc:  # noqa: BLE001 — schema 本身有问题时降级轻量校验
        logger.debug("jsonschema validator init failed for %s: %s", name, exc)
        return _lightweight_validate(name, arguments, schema)

    if not errors:
        return None

    messages: list[str] = []
    fields: list[str] = []
    for err in errors[:8]:
        path = ".".join(str(p) for p in err.path) or "(root)"
        fields.append(path)
        messages.append(f"{path}: {err.message}")
    return tool_error(
        error=f"arguments invalid for tool {name}",
        error_code="invalid_args",
        fixable=True,
        hint="按 tools schema 修正参数后重试；勿编造未声明字段。",
        details={"fields": fields, "messages": messages},
    )


def _lightweight_validate(
    name: str,
    arguments: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any] | None:
    """无 jsonschema 时的最小 required / type / enum 校验。"""
    required = schema.get("required") or []
    props = schema.get("properties") or {}
    missing = [r for r in required if r not in arguments or arguments[r] in (None, "")]
    if missing:
        return tool_error(
            error=f"missing required fields for tool {name}: {', '.join(missing)}",
            error_code="invalid_args",
            fixable=True,
            hint=f"补齐必填字段: {', '.join(missing)}",
            details={"fields": missing},
        )
    bad: list[str] = []
    for key, value in arguments.items():
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        if expected == "string" and not isinstance(value, str):
            bad.append(f"{key}: expected string")
        elif expected == "integer" and not isinstance(value, int):
            bad.append(f"{key}: expected integer")
        elif expected == "number" and not isinstance(value, (int, float)):
            bad.append(f"{key}: expected number")
        elif expected == "boolean" and not isinstance(value, bool):
            bad.append(f"{key}: expected boolean")
        elif expected == "array" and not isinstance(value, list):
            bad.append(f"{key}: expected array")
        elif expected == "object" and not isinstance(value, dict):
            bad.append(f"{key}: expected object")
        enum = spec.get("enum")
        if isinstance(enum, list) and value not in enum:
            bad.append(f"{key}: must be one of {enum}")
        min_len = spec.get("minLength")
        if isinstance(min_len, int) and isinstance(value, str) and len(value) < min_len:
            bad.append(f"{key}: shorter than minLength {min_len}")
    if bad:
        return tool_error(
            error=f"arguments invalid for tool {name}",
            error_code="invalid_args",
            fixable=True,
            hint="按 schema 修正类型/枚举/长度后重试",
            details={"messages": bad},
        )
    return None


def check_visibility(
    *,
    name: str,
    tier: str | None,
    enabled: bool,
    registered: bool,
    unlocked: set[str] | None,
) -> dict[str, Any] | None:
    """事前/事中可见性与启用检查。"""
    if not registered:
        return tool_error(
            error=f"unknown tool: {name}",
            error_code="unknown_tool",
            fixable=True,
            hint="先 list_tools / activate_skill 解锁后再调用；勿调用未披露工具。",
        )
    if not enabled:
        return tool_error(
            error=f"tool disabled: {name}",
            error_code="tool_disabled",
            fixable=False,
            hint="该工具已被管理员禁用，请改用其它工具或直接作答。",
        )
    tier_name = tier or "optional"
    if tier_name in ALWAYS_ALLOWED_TIERS:
        return None
    unlocked = unlocked or set()
    if name not in unlocked:
        return tool_error(
            error=f"tool locked: {name}",
            error_code="tool_locked",
            fixable=True,
            hint="先 activate_skill 或确认路由已解锁该工具，再调用。",
        )
    return None


def normalize_result(name: str, result: Any) -> dict[str, Any]:
    """事后结果契约：统一 ok 字段，并做轻量业务一致性标注。"""
    if not isinstance(result, dict):
        return tool_error(
            error=f"tool {name} returned non-object result",
            error_code="invalid_result",
            fixable=False,
            details={"raw_type": type(result).__name__},
        )

    out = dict(result)
    if "ok" not in out:
        # 约定缺失 ok 时：有 error 视为失败，否则成功
        out["ok"] = "error" not in out

    if out.get("ok") is True:
        if name == "retrieve" and not out.get("skipped"):
            hits = out.get("hits") or []
            if not hits and not (out.get("context") or "").strip():
                out["warning"] = "empty_retrieval"
                out["hint"] = out.get("hint") or "检索无命中，勿编造知识库内容，可改写 query 或改用其它来源。"
        elif name == "web_search":
            results = out.get("results") or []
            if not results:
                out["warning"] = "empty_results"
                out["hint"] = out.get("hint") or "搜索无结果，可改写 query 或缩小范围后重试。"
        elif name == "calculator" and "result" not in out:
            return tool_error(
                error="calculator missing numeric result",
                error_code="invalid_result",
                fixable=True,
                hint="重新调用 calculator 并确保 expression 为合法算术式。",
            )
    return out


class CallIdempotencyCache:
    """按 tool_call_id 去重，避免同一步重复执行副作用。"""

    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._data: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def get(self, call_id: str | None) -> dict[str, Any] | None:
        if not call_id:
            return None
        hit = self._data.get(call_id)
        if hit is None:
            return None
        self._data.move_to_end(call_id)
        return dict(hit)

    def put(self, call_id: str | None, result: dict[str, Any]) -> None:
        if not call_id:
            return
        self._data[call_id] = dict(result)
        self._data.move_to_end(call_id)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)
