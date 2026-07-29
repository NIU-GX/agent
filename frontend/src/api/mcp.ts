import { apiDeleteJson, apiGet, apiPatchJson, apiPostJson, apiPutJson } from './http'

export interface AdminMcpServer {
  name: string
  command: string
  args: unknown[]
  env: Record<string, unknown>
  enabled: boolean
  last_error?: string | null
  tools?: { name: string; mcp_tool?: string; description?: string }[]
  created_at?: string | null
  updated_at?: string | null
}

export async function listAdminMcp() {
  return apiGet<{ items: AdminMcpServer[] }>('/mcp')
}

export async function createMcpServer(body: {
  name: string
  command: string
  args?: unknown[]
  env?: Record<string, unknown>
  enabled?: boolean
}) {
  return apiPostJson<AdminMcpServer>('/mcp', body)
}

export async function updateMcpServer(
  name: string,
  body: Partial<{
    command: string
    args: unknown[]
    env: Record<string, unknown>
    enabled: boolean
  }>,
) {
  return apiPutJson<AdminMcpServer>(`/mcp/${encodeURIComponent(name)}`, body)
}

export async function deleteMcpServer(name: string) {
  return apiDeleteJson<{ ok: boolean; name: string }>(`/mcp/${encodeURIComponent(name)}`)
}

export async function setMcpEnabled(name: string, enabled: boolean) {
  return apiPatchJson<AdminMcpServer>(`/mcp/${encodeURIComponent(name)}/enabled`, { enabled })
}

export async function reconnectMcp(name: string) {
  return apiPostJson<{
    ok: boolean
    name: string
    error?: string | null
    server: AdminMcpServer
  }>(`/mcp/${encodeURIComponent(name)}/reconnect`, {})
}
