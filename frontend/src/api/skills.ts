import { apiDeleteJson, apiGet, apiPatchJson, apiPostJson, apiPutJson } from './http'

export interface AdminSkill {
  name: string
  description: string
  body: string
  tools: string[]
  mcp: string[]
  enabled: boolean
  created_at?: string | null
  updated_at?: string | null
}

export async function listAdminSkills() {
  return apiGet<{ items: AdminSkill[] }>('/skills')
}

export async function getAdminSkill(name: string) {
  return apiGet<AdminSkill>(`/skills/${encodeURIComponent(name)}`)
}

export async function createSkill(body: {
  name: string
  description?: string
  body?: string
  tools?: string[]
  mcp?: string[]
  enabled?: boolean
}) {
  return apiPostJson<AdminSkill>('/skills', body)
}

export async function updateSkill(
  name: string,
  body: Partial<{
    description: string
    body: string
    tools: string[]
    mcp: string[]
    enabled: boolean
  }>,
) {
  return apiPutJson<AdminSkill>(`/skills/${encodeURIComponent(name)}`, body)
}

export async function deleteSkill(name: string) {
  return apiDeleteJson<{ ok: boolean; name: string }>(`/skills/${encodeURIComponent(name)}`)
}

export async function setSkillEnabled(name: string, enabled: boolean) {
  return apiPatchJson<AdminSkill>(`/skills/${encodeURIComponent(name)}/enabled`, { enabled })
}
