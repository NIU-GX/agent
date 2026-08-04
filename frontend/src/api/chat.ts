/**
 * 后端 SSE 对话 / HITL 恢复。
 */
import { API_BASE, authHeaders } from './http'

/** 业务侧 run 指针；完整轨迹见 langfuse_url。 */
export type AgentRunPointer = {
  run_id: string
  session_id: string
  trace_id?: string | null
  langfuse_url?: string | null
  strategy?: string | null
  status: string
  tenant_id?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export type ChatEvent = {
  type: string
  data: Record<string, unknown>
}

async function readSse(
  resp: Response,
  onEvent: (ev: ChatEvent) => void,
) {
  if (!resp.ok || !resp.body) {
    throw new Error(`chat failed: ${resp.status}`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() || ''
    for (const part of parts) {
      const lines = part.split('\n')
      let eventType = 'message'
      let dataLine = ''
      for (const line of lines) {
        if (line.startsWith('event:')) eventType = line.slice(6).trim()
        if (line.startsWith('data:')) dataLine = line.slice(5).trim()
      }
      if (!dataLine) continue
      const payload = JSON.parse(dataLine) as ChatEvent
      onEvent({ type: eventType || payload.type, data: payload.data || {} })
    }
  }
}

export async function streamChat(
  body: {
    message: string
    strategy: string
    enable_rag?: boolean | null
    session_id?: string
    skills?: string[]
  },
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
) {
  const payload: Record<string, unknown> = {
    message: body.message,
    strategy: body.strategy,
    skills: body.skills,
  }
  if (body.session_id) payload.session_id = body.session_id
  if (body.enable_rag !== undefined) payload.enable_rag = body.enable_rag
  const resp = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: authHeaders(true),
    body: JSON.stringify(payload),
    signal,
  })
  await readSse(resp, onEvent)
}

export async function listChatRuns(sessionId: string, limit = 50): Promise<AgentRunPointer[]> {
  const qs = new URLSearchParams({
    session_id: sessionId,
    limit: String(limit),
  })
  const resp = await fetch(`${API_BASE}/chat/runs?${qs}`, {
    headers: authHeaders(false),
  })
  if (!resp.ok) throw new Error(`list runs failed: ${resp.status}`)
  return (await resp.json()) as AgentRunPointer[]
}

export async function resumeChat(
  body: {
    session_id: string
    strategy?: string
    approved: boolean
    plan_steps?: string[]
    message?: string
    enable_rag?: boolean | null
  },
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
) {
  const resp = await fetch(`${API_BASE}/chat/resume`, {
    method: 'POST',
    headers: authHeaders(true),
    body: JSON.stringify(body),
    signal,
  })
  await readSse(resp, onEvent)
}
