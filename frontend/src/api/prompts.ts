import { apiGet, apiPostJson } from './http'

export interface PromptListItem {
  key: string
  name: string
  description: string
  active_version: number
  version_count: number
  content_preview: string
  updated_at: string | null
}

export interface PromptVersion {
  id: string
  prompt_key: string
  version: number
  content: string
  change_note: string | null
  created_by: string | null
  created_at: string | null
  is_active: boolean
}

export interface PromptDetail {
  key: string
  name: string
  description: string
  active_version: number
  updated_at: string | null
  created_at: string | null
  versions: PromptVersion[]
}

export async function fetchPrompts(): Promise<{ items: PromptListItem[] }> {
  return apiGet('/prompts')
}

export async function fetchPrompt(key: string): Promise<PromptDetail> {
  return apiGet(`/prompts/${encodeURIComponent(key)}`)
}

export async function createPromptVersion(
  key: string,
  body: {
    content: string
    change_note?: string
    created_by?: string
    activate?: boolean
  },
): Promise<PromptVersion> {
  return apiPostJson(`/prompts/${encodeURIComponent(key)}/versions`, body)
}

export async function rollbackPrompt(
  key: string,
  version: number,
): Promise<{ key: string; from_version: number; active_version: number; content: string }> {
  return apiPostJson(`/prompts/${encodeURIComponent(key)}/rollback`, { version })
}
