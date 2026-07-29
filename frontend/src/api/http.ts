/** 统一 HTTP 客户端：base + API Key。 */

export const API_BASE = '/api/v1'

export function apiKey(): string {
  return localStorage.getItem('apiKey') || 'dev-api-key-change-me'
}

export function authHeaders(json = true): HeadersInit {
  const headers: Record<string, string> = {
    'X-API-Key': apiKey(),
  }
  if (json) headers['Content-Type'] = 'application/json'
  return headers
}

export async function apiGet<T = unknown>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, { headers: authHeaders(false) })
  if (!resp.ok) throw new Error(`${path} failed: ${resp.status}`)
  return resp.json() as Promise<T>
}

export async function apiPostJson<T = unknown>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: authHeaders(true),
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error(`${path} failed: ${resp.status}`)
  return resp.json() as Promise<T>
}

export async function apiPostForm<T = unknown>(path: string, form: FormData): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: authHeaders(false),
    body: form,
  })
  if (!resp.ok) throw new Error(`${path} failed: ${resp.status} ${await resp.text()}`)
  return resp.json() as Promise<T>
}

export async function apiPutJson<T = unknown>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: 'PUT',
    headers: authHeaders(true),
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error(`${path} failed: ${resp.status}`)
  return resp.json() as Promise<T>
}

export async function apiPatchJson<T = unknown>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: 'PATCH',
    headers: authHeaders(true),
    body: JSON.stringify(body),
  })
  if (!resp.ok) throw new Error(`${path} failed: ${resp.status}`)
  return resp.json() as Promise<T>
}

export async function apiDeleteJson<T = unknown>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: 'DELETE',
    headers: authHeaders(false),
  })
  if (!resp.ok) throw new Error(`${path} failed: ${resp.status}`)
  return resp.json() as Promise<T>
}
