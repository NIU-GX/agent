"""装配层桥接：将独立 Tool/Skill/MCP Store 热注入 Agent 运行时。

本模块可 import agent-core（仅装配层）；Store 本身不依赖 agent-core。
"""

from __future__ import annotations

from typing import Any

from agent_core.skills import SkillRegistry, filesystem_skill_seeds
from agent_core.tools import ToolRegistry
from shared.logging import get_logger
from shared.mcp_store import McpStore
from shared.skill_store import SkillStore
from shared.tool_store import ToolStore

logger = get_logger(__name__)


class CapabilitySync:
    """封装 Store → Runtime 同步；CRUD 写成功后调用对应 sync_*。"""

    def __init__(
        self,
        *,
        tools: ToolRegistry,
        skills: SkillRegistry,
        tool_store: ToolStore,
        skill_store: SkillStore,
        mcp_store: McpStore,
    ) -> None:
        self.tools = tools
        self.skills = skills
        self.tool_store = tool_store
        self.skill_store = skill_store
        self.mcp_store = mcp_store

    async def sync_tools(self) -> None:
        webhooks = await self.tool_store.list_tools()
        self.tools.sync_webhooks(webhooks)
        flags = await self.tool_store.list_flags()
        self.tools.apply_enabled_flags(flags)
        logger.info("synced tools webhooks=%s flags=%s", len(webhooks), len(flags))

    async def sync_skills(self) -> None:
        records = await self.skill_store.list_skills(enabled_only=False)
        self.skills.reload_from(records)
        logger.info("synced skills count=%s", len(self.skills.catalog()))

    async def sync_mcp(self) -> list[dict[str, Any]]:
        servers = await self.mcp_store.list_servers(enabled_only=False)
        results = await self.tools.reload_mcp_configs(servers)
        for item in results:
            name = str(item.get("name") or "")
            if not name:
                continue
            err = item.get("error")
            await self.mcp_store.set_last_error(name, str(err) if err else None)
        # 清理已成功连接的 last_error；未出现在 results 中的 disabled 不清
        for srv in servers:
            if not srv.get("enabled", True):
                continue
            name = srv["name"]
            matched = next((r for r in results if r.get("name") == name), None)
            if matched is None:
                await self.mcp_store.set_last_error(name, "not connected")
            elif not matched.get("error"):
                await self.mcp_store.set_last_error(name, None)
        logger.info("synced mcp servers=%s", len(servers))
        return results

    async def sync_all(self) -> None:
        await self.sync_tools()
        await self.sync_skills()
        await self.sync_mcp()
