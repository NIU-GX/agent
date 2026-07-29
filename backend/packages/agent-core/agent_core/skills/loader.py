"""Skill 加载：解析 SKILL.md（YAML frontmatter + Markdown body）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Skill:
    """单个 Skill：L0 元数据 + L1 正文 + 绑定工具。"""

    name: str
    description: str
    body: str
    path: Path | None = None
    tools: list[str] = field(default_factory=list)
    mcp: list[str] = field(default_factory=list)

    def catalog_entry(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
            "mcp": list(self.mcp),
        }


def skill_from_record(record: dict[str, Any]) -> Skill:
    """从 Store/API 记录构造 Skill（无文件系统 path）。"""
    return Skill(
        name=str(record.get("name") or "").strip(),
        description=str(record.get("description") or ""),
        body=str(record.get("body") or ""),
        path=None,
        tools=[str(x).strip() for x in (record.get("tools") or []) if str(x).strip()],
        mcp=[str(x).strip() for x in (record.get("mcp") or []) if str(x).strip()],
    )


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def parse_skill_md(path: Path) -> Skill:
    """从 SKILL.md 解析 Skill。"""
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        name = path.parent.name
        return Skill(name=name, description="", body=text.strip(), path=path)

    raw_meta, body = match.group(1), match.group(2).strip()
    meta = _parse_simple_yaml(raw_meta)
    name = str(meta.get("name") or path.parent.name).strip()
    description = str(meta.get("description") or "").strip()
    tools = _as_str_list(meta.get("tools"))
    mcp = _as_str_list(meta.get("mcp"))
    return Skill(
        name=name,
        description=description,
        body=body,
        path=path,
        tools=tools,
        mcp=mcp,
    )


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return []


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """极简 YAML：支持标量与同缩进 `-` 列表（够用 frontmatter）。"""
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if re.match(r"^\s*-\s+", line) and current_key is not None:
            item = re.sub(r"^\s*-\s+", "", line).strip().strip("\"'")
            if current_list is None:
                current_list = []
                result[current_key] = current_list
            current_list.append(item)
            continue
        m = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        current_key = key
        current_list = None
        if val == "" or val == "|" or val == ">":
            result[key] = []
            current_list = result[key]
            continue
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            result[key] = val[1:-1]
        elif val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                result[key] = []
            else:
                result[key] = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
        else:
            result[key] = val
    return result


def discover_skills(root: Path) -> list[Skill]:
    """扫描 root/*/SKILL.md。"""
    if not root.is_dir():
        return []
    skills: list[Skill] = []
    for skill_md in sorted(root.glob("*/SKILL.md")):
        try:
            skills.append(parse_skill_md(skill_md))
        except Exception:  # noqa: BLE001
            continue
    return skills
