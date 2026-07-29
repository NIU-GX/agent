import { apiDeleteJson, apiGet, apiPatchJson, apiPostJson, apiPutJson } from './http'

export interface AdminTool {
  name: string
  description: string
  tier: string
  source: string
  enabled: boolean
  mutable: boolean
  parameters?: Record<string, unknown>
  webhook_url?: string
  webhook_method?: string
  webhook_headers?: Record<string, unknown>
  timeout_sec?: number
}

export async function listAdminTools() {
  return apiGet<{ items: AdminTool[] }>('/tools')
}

export async function createWebhookTool(body: {
  name: string
  description?: string
  parameters?: Record<string, unknown>
  webhook_url: string
  webhook_method?: string
  webhook_headers?: Record<string, unknown>
  timeout_sec?: number
  tier?: string
  enabled?: boolean
}) {
  return apiPostJson<AdminTool>('/tools', body)
}

export async function updateWebhookTool(
  name: string,
  body: Partial<{
    description: string
    parameters: Record<string, unknown>
    webhook_url: string
    webhook_method: string
    webhook_headers: Record<string, unknown>
    timeout_sec: number
    tier: string
    enabled: boolean
  }>,
) {
  return apiPutJson<AdminTool>(`/tools/${encodeURIComponent(name)}`, body)
}

export async function deleteWebhookTool(name: string) {
  return apiDeleteJson<{ ok: boolean; name: string }>(`/tools/${encodeURIComponent(name)}`)
}

export async function setToolEnabled(name: string, enabled: boolean) {
  return apiPatchJson<{ name: string; enabled: boolean }>(
    `/tools/${encodeURIComponent(name)}/enabled`,
    { enabled },
  )
}
