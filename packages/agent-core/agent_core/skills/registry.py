"""SkillRegistry：L0 catalog / L1 activate / L2 scripts。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from shared.logging import get_logger

from agent_core.skills.loader import Skill, discover_skills

logger = get_logger(__name__)


def _resolve_skills_dir(path: str | Path) -> Path:
    """相对路径时从 cwd 向上查找含 */SKILL.md 的目录。"""
    p = Path(path)
    if p.is_absolute():
        return p.resolve()
    here = Path.cwd()
    for base in [here, *here.parents]:
        candidate = (base / p).resolve()
        if candidate.is_dir() and any(candidate.glob("*/SKILL.md")):
            return candidate
        if (base / "pyproject.toml").is_file() and (base / p).is_dir():
            return (base / p).resolve()
    return (here / p).resolve()


class SkillRegistry:
    """项目级 Skills：渐进式披露入口。"""

    def __init__(self, skills_dir: str | Path | None = None) -> None:
        self._root = _resolve_skills_dir(skills_dir or "skills")
        self._skills: dict[str, Skill] = {}
        self.reload()

    @property
    def root(self) -> Path:
        return self._root

    def reload(self) -> None:
        self._skills = {s.name: s for s in discover_skills(self._root)}
        logger.info("skills loaded count=%s dir=%s", len(self._skills), self._root)

    def catalog(self) -> list[dict[str, Any]]:
        """L0：name + description（及绑定声明）。"""
        return [s.catalog_entry() for s in self._skills.values()]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def activate(self, name: str) -> dict[str, Any]:
        """L1：返回正文与解锁工具集合。"""
        skill = self._skills.get(name)
        if not skill:
            return {"ok": False, "error": f"unknown skill: {name}"}
        unlocked = list(dict.fromkeys([*skill.tools, *skill.mcp]))
        return {
            "ok": True,
            "name": skill.name,
            "description": skill.description,
            "body": skill.body,
            "unlocked_tools": unlocked,
            "tools": list(skill.tools),
            "mcp": list(skill.mcp),
        }

    def format_catalog_prompt(self) -> str:
        entries = self.catalog()
        if not entries:
            return "（无可用 Skills）"
        lines = []
        for e in entries:
            lines.append(f"- {e['name']}: {e.get('description') or '（无描述）'}")
        return "\n".join(lines)

    async def run_script(
        self,
        skill_name: str,
        script_name: str,
        *,
        args: list[str] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """L2：执行 skill 目录下 scripts/ 内白名单脚本。"""
        skill = self._skills.get(skill_name)
        if not skill:
            return {"ok": False, "error": f"unknown skill: {skill_name}"}
        scripts_dir = (skill.path.parent / "scripts").resolve()
        if not scripts_dir.is_dir():
            return {"ok": False, "error": "no scripts directory"}
        target = (scripts_dir / script_name).resolve()
        if not str(target).startswith(str(scripts_dir)) or not target.is_file():
            return {"ok": False, "error": f"script not allowed: {script_name}"}
        if target.suffix not in {".py", ".sh"}:
            return {"ok": False, "error": "only .py/.sh scripts allowed"}
        cmd = (
            ["python", str(target), *(args or [])]
            if target.suffix == ".py"
            else ["bash", str(target), *(args or [])]
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(skill.path.parent),
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                proc.kill()
                return {"ok": False, "error": f"script timeout after {timeout}s"}
            out = stdout.decode("utf-8", errors="replace")[:4000]
            err = stderr.decode("utf-8", errors="replace")[:2000]
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": out,
                "stderr": err,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
