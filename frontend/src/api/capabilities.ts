import { apiGet } from './http'

export type ToolCatalogItem = {
  name: string
  description: string
  tier: string
  unlocked: boolean
}

export type SkillCatalogItem = {
  name: string
  description: string
  tools?: string[]
  mcp?: string[]
}

export type SkillDetail = {
  name: string
  description?: string
  body?: string
  tools?: string[]
  mcp?: string[]
}

export type McpServerItem = {
  name: string
  command?: string
  args?: string[]
  tools?: { name: string; mcp_tool?: string; description?: string }[]
  error?: string
}

export async function fetchTools() {
  return apiGet<{ items: ToolCatalogItem[] }>('/capabilities/tools')
}

export async function fetchSkills() {
  return apiGet<{ items: SkillCatalogItem[] }>('/capabilities/skills')
}

export async function fetchSkillDetail(name: string) {
  return apiGet<{ ok: boolean; item: SkillDetail | null; error?: string }>(
    `/capabilities/skills?name=${encodeURIComponent(name)}`,
  )
}

export async function fetchMcp() {
  return apiGet<{ items: McpServerItem[] }>('/capabilities/mcp')
}
