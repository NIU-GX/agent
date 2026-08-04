<template>
  <div class="chat">
    <div class="thread" ref="threadRef">
      <div v-if="!hasTurn" class="welcome">
        <div class="welcome-mark">A</div>
        <h1>有什么可以帮忙的？</h1>
        <p>基于企业知识库与工具链回答。输入 <code>/</code> 可选择 Skill。</p>
        <div class="suggestions">
          <button
            v-for="s in suggestions"
            :key="s"
            type="button"
            class="chip"
            @click="useSuggestion(s)"
          >
            {{ s }}
          </button>
        </div>
      </div>

      <div v-else class="column">
        <div class="msg user">
          <div class="bubble">{{ lastQuestion }}</div>
        </div>

        <div v-if="activity.length" class="activity">
          <button type="button" class="activity-toggle" @click="showActivity = !showActivity">
            <span>{{ showActivity ? '收起过程' : '查看过程' }}</span>
            <span class="count">{{ activity.length }}</span>
          </button>
          <ul v-show="showActivity" class="activity-list">
            <li v-for="(item, idx) in activity" :key="idx" :class="item.kind">
              <span class="tag">{{ labelOf(item.kind) }}</span>
              <a
                v-if="item.href"
                class="detail link"
                :href="item.href"
                target="_blank"
                rel="noopener noreferrer"
              >{{ item.summary }}</a>
              <span v-else class="detail">{{ item.summary }}</span>
            </li>
          </ul>
          <p v-if="langfuseUrl" class="trace-link-wrap">
            <a class="trace-link" :href="langfuseUrl" target="_blank" rel="noopener noreferrer">
              在 Langfuse 查看追踪
            </a>
          </p>
        </div>

        <div v-if="pendingHitl" class="hitl">
          <div class="hitl-head">需要你确认执行计划</div>
          <ol v-if="planSteps.length" class="plan">
            <li v-for="(step, i) in planSteps" :key="i">{{ step }}</li>
          </ol>
          <pre v-else>{{ JSON.stringify(pendingHitl.payload ?? pendingHitl, null, 2) }}</pre>
          <div class="hitl-actions">
            <button type="button" class="btn primary" :disabled="loading" @click="resume(true)">
              批准继续
            </button>
            <button type="button" class="btn ghost" :disabled="loading" @click="resume(false)">
              拒绝
            </button>
          </div>
        </div>

        <div v-if="answer || loading" class="msg assistant">
          <div class="avatar">A</div>
          <div class="content">
            <div v-if="answer" class="prose">{{ answer }}</div>
            <div v-else class="typing">
              <span /><span /><span />
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="dock">
      <div class="composer">
        <div v-if="selectedSkills.length" class="skill-chips">
          <button
            v-for="name in selectedSkills"
            :key="name"
            type="button"
            class="skill-chip"
            :title="`移除 ${name}`"
            @click="removeSkill(name)"
          >
            /{{ name }}
            <span aria-hidden="true">×</span>
          </button>
        </div>
        <div class="input-wrap">
          <div v-if="slashOpen" class="slash-pop" @mousedown.prevent>
            <div class="slash-head">选择 Skill</div>
            <button
              v-for="(s, idx) in filteredSkills"
              :key="s.name"
              type="button"
              class="slash-item"
              :class="{ on: idx === slashIndex }"
              @mouseenter="slashIndex = idx"
              @click="pickSlashSkill(s.name)"
            >
              <strong>/{{ s.name }}</strong>
              <em>{{ s.description || '无描述' }}</em>
            </button>
            <div v-if="!filteredSkills.length" class="slash-empty">
              {{ skillsLoadError || '没有匹配的 Skill' }}
            </div>
            <div v-else-if="skillsLoadError" class="slash-note">{{ skillsLoadError }}</div>
          </div>
          <textarea
            v-model="message"
            rows="1"
            :disabled="loading"
            placeholder="输入消息，或用 / 选择 Skill…"
            @keydown="onKeydown"
            @input="onInput"
            ref="inputRef"
          />
        </div>
        <div class="composer-bar">
          <div class="tools">
            <div class="seg" role="group" aria-label="策略">
              <button
                v-for="opt in strategyOptions"
                :key="opt.value"
                type="button"
                class="seg-item"
                :class="{ on: strategy === opt.value }"
                @click="strategy = opt.value"
              >
                {{ opt.label }}
              </button>
            </div>
            <button
              type="button"
              class="tool-btn"
              :class="{ on: slashOpen || selectedSkills.length > 0 }"
              title="输入 / 也可唤起"
              @click.stop="openSlashFromButton"
            >
              /
            </button>
            <div class="seg" role="group" aria-label="RAG">
              <button
                v-for="opt in ragOptions"
                :key="opt.value"
                type="button"
                class="seg-item"
                :class="{ on: enableRag === opt.value }"
                :title="opt.title"
                @click="enableRag = opt.value"
              >
                {{ opt.label }}
              </button>
            </div>
          </div>
          <button
            type="button"
            class="send"
            :disabled="loading || !message.trim()"
            :aria-busy="loading"
            @click="send"
          >
            {{ loading ? '…' : '↑' }}
          </button>
        </div>
      </div>
      <p class="hint">
        <span>Enter 发送 · / 选择 Skill · Shift+Enter 换行</span>
        <span v-if="sessionId" class="session-id">{{ shortId }}</span>
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchSkills, type SkillCatalogItem } from '../api/capabilities'
import { resumeChat, streamChat, type ChatEvent } from '../api/chat'

type ActivityItem = { kind: string; summary: string; href?: string }

const strategy = ref('auto')
/** null = Auto（由意图路由决定）；true/false = 手动覆盖 */
const enableRag = ref<boolean | null>(null)
const message = ref('')
const loading = ref(false)
const answer = ref('')
const sessionId = ref<string | null>(null)
const langfuseUrl = ref<string | null>(null)
const lastQuestion = ref('')
const pendingHitl = ref<Record<string, unknown> | null>(null)
const activity = ref<ActivityItem[]>([])
const skillOptions = ref<SkillCatalogItem[]>([])
const skillsLoadError = ref('')
const selectedSkills = ref<string[]>([])
const showActivity = ref(false)
const slashOpen = ref(false)
const slashIndex = ref(0)
const threadRef = ref<HTMLElement | null>(null)
const inputRef = ref<HTMLTextAreaElement | null>(null)

/** API 不可用时的本地兜底，保证 / 菜单仍可用 */
const FALLBACK_SKILLS: SkillCatalogItem[] = [
  {
    name: 'kb-qa',
    description: '企业知识库问答：优先检索、强制引用，可解锁 http_get',
    tools: ['http_get'],
  },
  {
    name: 'calc-assist',
    description: '数值与算术辅助：优先用 calculator，避免心算错误',
    tools: ['calculator'],
  },
  {
    name: 'web-research',
    description: '公网调研：web_search + http_get，注明来源',
    tools: ['web_search', 'http_get'],
  },
]

const strategyOptions = [
  { value: 'auto', label: 'Auto' },
  { value: 'cot', label: 'CoT' },
  { value: 'react', label: 'ReAct' },
  { value: 'plan_execute', label: 'Plan' },
  { value: 'multi_agent', label: 'Multi' },
] as const

const ragOptions = [
  { value: null as boolean | null, label: 'RAG·Auto', title: '由意图路由决定是否检索知识库' },
  { value: true as boolean | null, label: '开', title: '强制启用知识库检索' },
  { value: false as boolean | null, label: '关', title: '强制关闭知识库检索' },
]

const suggestions = [
  '对比两份政策文档的差异并给出结论',
  '检索知识库中关于权限审批的流程',
  '用计算器核对这段费用合计是否正确',
  '搜索一下最新的公开行业资讯并总结',
]

const slashMatch = computed(() => {
  // 行首或空白后的 /xxx；光标在末尾输入时触发
  const m = message.value.match(/(^|[\s\n])\/([^\s\n]*)$/)
  if (!m) return null
  return { prefix: m[1], query: (m[2] || '').toLowerCase() }
})

const filteredSkills = computed(() => {
  const q = slashMatch.value?.query ?? ''
  const list = skillOptions.value.length ? skillOptions.value : FALLBACK_SKILLS
  if (!q) return list
  return list.filter(
    (s) =>
      s.name.toLowerCase().includes(q) ||
      (s.description || '').toLowerCase().includes(q),
  )
})

const labels: Record<string, string> = {
  strategy: '策略',
  intent: '路由',
  thought: '思考',
  plan: '计划',
  hitl: '确认',
  tool_start: '工具',
  tool_end: '完成',
  skill_start: '技能',
  skill_end: '技能',
  agent_start: '智能体',
  agent_end: '智能体',
  citation: '引用',
  trace: '追踪',
  error: '错误',
}

const hasTurn = computed(() => Boolean(lastQuestion.value))
const shortId = computed(() => (sessionId.value || '').slice(0, 8))
const planSteps = computed(() => {
  const steps = pendingHitl.value?.plan_steps
  return Array.isArray(steps) ? (steps as string[]) : []
})

function labelOf(kind: string) {
  return labels[kind] || kind
}

function summarize(kind: string, data: Record<string, unknown> | string): string {
  if (typeof data === 'string') return data
  if (kind === 'strategy') return String(data.strategy || JSON.stringify(data))
  if (kind === 'intent') {
    const rag = data.enable_rag ? 'RAG开' : 'RAG关'
    const web = data.enable_web_search ? '联网开' : '联网关'
    const strat = data.strategy ? String(data.strategy) : ''
    const reason = data.reason ? String(data.reason) : ''
    const skills = Array.isArray(data.skills) ? data.skills.join(',') : ''
    return [rag, web, strat, skills, reason].filter(Boolean).join(' · ')
  }
  if (kind === 'thought') return String(data.text || '')
  if (kind === 'plan') {
    const steps = data.steps
    return Array.isArray(steps) ? steps.join(' → ') : JSON.stringify(data)
  }
  if (kind === 'agent_start' || kind === 'agent_end') {
    const agent = data.agent ? String(data.agent) : ''
    const task = data.task ? String(data.task) : ''
    const ok = data.ok === false ? '失败' : data.ok === true ? '完成' : '开始'
    return [agent, ok, task].filter(Boolean).join(' · ')
  }
  if (kind === 'tool_start' || kind === 'tool_end' || kind === 'skill_start' || kind === 'skill_end') {
    const name = data.name ? String(data.name) : ''
    const ok = data.ok === false ? '失败' : data.ok === true ? '成功' : ''
    const agent = data.agent ? String(data.agent) : ''
    return [agent, name, ok].filter(Boolean).join(' · ') || JSON.stringify(data)
  }
  if (kind === 'citation') {
    const citations = data.citations
    const n = Array.isArray(citations) ? citations.length : 0
    return n ? `${n} 条引用` : '引用'
  }
  if (kind === 'error') return String(data.message || data)
  return JSON.stringify(data)
}

function rememberTrace(data: Record<string, unknown>) {
  const url = data.langfuse_url
  if (typeof url === 'string' && url) {
    langfuseUrl.value = url
  }
}

function useSuggestion(text: string) {
  message.value = text
  void nextTick(() => {
    autoResize()
    inputRef.value?.focus()
  })
}

function syncSlashMenu() {
  if (slashMatch.value) {
    slashOpen.value = true
    const max = Math.max(filteredSkills.value.length - 1, 0)
    slashIndex.value = Math.min(slashIndex.value, max)
  } else {
    slashOpen.value = false
    slashIndex.value = 0
  }
}

function onInput() {
  autoResize()
  syncSlashMenu()
}

watch(message, () => {
  syncSlashMenu()
})

function openSlashFromButton() {
  const el = inputRef.value
  if (slashMatch.value) {
    slashOpen.value = true
    el?.focus()
    return
  }
  const base = message.value
  message.value = base && !/[\s\n]$/.test(base) ? `${base} /` : `${base}/`
  void nextTick(() => {
    autoResize()
    syncSlashMenu()
    el?.focus()
    if (el) {
      const len = message.value.length
      el.setSelectionRange(len, len)
    }
  })
}

function stripSlashToken() {
  message.value = message.value.replace(/(^|\s)\/[^\s]*$/, (_, sp) => (sp === ' ' ? ' ' : '')).replace(/\s+$/, '')
}

function pickSlashSkill(name: string) {
  if (!selectedSkills.value.includes(name)) {
    selectedSkills.value = [...selectedSkills.value, name]
  }
  stripSlashToken()
  slashOpen.value = false
  slashIndex.value = 0
  void nextTick(() => {
    autoResize()
    inputRef.value?.focus()
  })
}

function removeSkill(name: string) {
  selectedSkills.value = selectedSkills.value.filter((n) => n !== name)
}

function onKeydown(e: KeyboardEvent) {
  if (slashOpen.value && filteredSkills.value.length) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      slashIndex.value = (slashIndex.value + 1) % filteredSkills.value.length
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      slashIndex.value =
        (slashIndex.value - 1 + filteredSkills.value.length) % filteredSkills.value.length
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      const skill = filteredSkills.value[slashIndex.value]
      if (skill) pickSlashSkill(skill.name)
      return
    }
    if (e.key === 'Tab') {
      e.preventDefault()
      const skill = filteredSkills.value[slashIndex.value]
      if (skill) pickSlashSkill(skill.name)
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      stripSlashToken()
      slashOpen.value = false
      return
    }
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    void send()
  }
}

function onDocClick(e: MouseEvent) {
  const target = e.target as HTMLElement | null
  if (!target?.closest('.composer')) slashOpen.value = false
}

function autoResize() {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}

async function scrollBottom() {
  await nextTick()
  const el = threadRef.value
  if (el) el.scrollTop = el.scrollHeight
}

watch([activity, answer, pendingHitl], () => {
  void scrollBottom()
})

function handleEvent(ev: ChatEvent) {
  if (ev.type === 'intent') {
    activity.value.push({ kind: 'intent', summary: summarize('intent', ev.data) })
    showActivity.value = true
  } else if (ev.type === 'strategy') {
    sessionId.value = String(ev.data.session_id || sessionId.value || '')
    rememberTrace(ev.data)
    activity.value.push({ kind: 'strategy', summary: summarize('strategy', ev.data) })
    if (langfuseUrl.value) {
      activity.value.push({
        kind: 'trace',
        summary: '打开 Langfuse Trace',
        href: langfuseUrl.value,
      })
    }
  } else if (ev.type === 'thought') {
    activity.value.push({ kind: 'thought', summary: summarize('thought', ev.data) })
  } else if (ev.type === 'plan') {
    activity.value.push({ kind: 'plan', summary: summarize('plan', ev.data) })
  } else if (ev.type === 'agent_start' || ev.type === 'agent_end') {
    activity.value.push({ kind: ev.type, summary: summarize(ev.type, ev.data) })
  } else if (ev.type === 'hitl') {
    pendingHitl.value = ev.data
    sessionId.value = String(ev.data.session_id || sessionId.value || '')
    rememberTrace(ev.data)
    activity.value.push({ kind: 'hitl', summary: '等待人工确认' })
  } else if (
    ev.type === 'tool_start' ||
    ev.type === 'tool_end' ||
    ev.type === 'skill_start' ||
    ev.type === 'skill_end'
  ) {
    activity.value.push({ kind: ev.type, summary: summarize(ev.type, ev.data) })
  } else if (ev.type === 'citation') {
    activity.value.push({ kind: 'citation', summary: summarize('citation', ev.data) })
  } else if (ev.type === 'token') {
    answer.value += String(ev.data.text || '')
  } else if (ev.type === 'final') {
    pendingHitl.value = null
    answer.value = String(ev.data.answer || answer.value)
    if (ev.data.session_id) sessionId.value = String(ev.data.session_id)
    rememberTrace(ev.data)
  } else if (ev.type === 'error') {
    rememberTrace(ev.data)
    activity.value.push({ kind: 'error', summary: summarize('error', ev.data) })
    showActivity.value = true
  }
}

async function send() {
  if (!message.value.trim() || loading.value) return
  const payload = message.value
    .replace(/(^|\s)\/[^\s]*$/, (_, sp) => (sp === ' ' ? ' ' : ''))
    .trim()
  if (!payload) return
  loading.value = true
  answer.value = ''
  activity.value = []
  langfuseUrl.value = null
  pendingHitl.value = null
  showActivity.value = false
  slashOpen.value = false
  lastQuestion.value = payload
  message.value = ''
  void nextTick(autoResize)
  try {
    await streamChat(
      {
        message: payload,
        strategy: strategy.value,
        enable_rag: enableRag.value,
        skills: selectedSkills.value,
      },
      handleEvent,
    )
  } catch (e) {
    activity.value.push({ kind: 'error', summary: String(e) })
    showActivity.value = true
  } finally {
    loading.value = false
  }
}

async function resume(approved: boolean) {
  if (!sessionId.value || loading.value) return
  loading.value = true
  try {
    await resumeChat(
      {
        session_id: sessionId.value,
        strategy: 'plan_execute',
        approved,
        message: lastQuestion.value,
        enable_rag: enableRag.value,
        plan_steps: planSteps.value.length ? planSteps.value : undefined,
      },
      handleEvent,
    )
  } catch (e) {
    activity.value.push({ kind: 'error', summary: String(e) })
    showActivity.value = true
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  document.addEventListener('click', onDocClick)
  try {
    const res = await fetchSkills()
    skillOptions.value = res.items || []
    skillsLoadError.value = ''
    if (!skillOptions.value.length) {
      skillOptions.value = FALLBACK_SKILLS
      skillsLoadError.value = '未从服务端读到 Skills，已使用本地列表'
    }
  } catch {
    skillOptions.value = FALLBACK_SKILLS
    skillsLoadError.value = '后端未连接，已使用本地 Skill 列表'
  }
  inputRef.value?.focus()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
})
</script>

<style scoped>
.chat {
  --chat-width: 720px;
  display: flex;
  flex-direction: column;
  height: calc(100% + var(--stage-pad-y) * 2);
  margin: calc(var(--stage-pad-y) * -1) calc(var(--stage-pad-x) * -1);
  width: calc(100% + var(--stage-pad-x) * 2);
  max-width: none;
  background: transparent;
}

.thread {
  flex: 1;
  min-height: 0;
  overflow: auto;
  scrollbar-gutter: stable;
}

.welcome {
  max-width: var(--chat-width);
  margin: 0 auto;
  padding: 12vh 24px 40px;
  text-align: center;
}

.welcome-mark {
  width: 52px;
  height: 52px;
  margin: 0 auto 18px;
  display: grid;
  place-items: center;
  border-radius: 14px;
  background: var(--ink);
  color: #f5f5f4;
  font-family: var(--font-display);
  font-size: 1.4rem;
  font-weight: 600;
}

.welcome h1 {
  margin: 0 0 10px;
  font-family: var(--font-display);
  font-size: clamp(1.6rem, 3vw, 2.1rem);
  font-weight: 600;
  letter-spacing: -0.03em;
  color: var(--ink);
}

.welcome p {
  margin: 0 auto 28px;
  max-width: 28rem;
  color: var(--muted);
  line-height: 1.6;
}

.welcome p code {
  font-family: var(--font-mono);
  font-size: 0.9em;
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(28, 25, 23, 0.06);
}

.suggestions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
}

.chip {
  appearance: none;
  border: 1px solid var(--line);
  background: rgba(252, 252, 251, 0.9);
  color: var(--ink-soft);
  border-radius: 12px;
  padding: 10px 14px;
  font: inherit;
  font-size: 0.88rem;
  line-height: 1.35;
  cursor: pointer;
  text-align: left;
  max-width: 280px;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.chip:hover {
  border-color: var(--line-strong);
  background: #fff;
  color: var(--ink);
}

.column {
  max-width: var(--chat-width);
  margin: 0 auto;
  padding: 28px 20px 12px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.msg.user {
  display: flex;
  justify-content: flex-end;
}

.msg.user .bubble {
  max-width: 85%;
  padding: 12px 16px;
  border-radius: 18px 18px 6px 18px;
  background: var(--ink);
  color: #f5f5f4;
  font-size: 0.98rem;
  line-height: 1.55;
  white-space: pre-wrap;
}

.msg.assistant {
  display: grid;
  grid-template-columns: 32px 1fr;
  gap: 12px;
  align-items: start;
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  background: var(--accent-wash);
  color: var(--accent);
  font-family: var(--font-display);
  font-weight: 600;
  font-size: 0.95rem;
}

.prose {
  font-size: 1.02rem;
  line-height: 1.75;
  color: var(--ink);
  white-space: pre-wrap;
  padding-top: 4px;
}

.typing {
  display: flex;
  gap: 5px;
  padding: 12px 0;
}

.typing span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--muted);
  opacity: 0.45;
  animation: blink 1.2s infinite ease-in-out;
}

.typing span:nth-child(2) { animation-delay: 0.15s; }
.typing span:nth-child(3) { animation-delay: 0.3s; }

@keyframes blink {
  0%, 80%, 100% { opacity: 0.25; transform: translateY(0); }
  40% { opacity: 0.9; transform: translateY(-2px); }
}

.activity {
  margin-left: 44px;
}

.activity-toggle {
  appearance: none;
  border: 1px solid var(--line);
  background: transparent;
  border-radius: 8px;
  padding: 6px 12px;
  font: inherit;
  font-size: 0.78rem;
  color: var(--muted);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.activity-toggle:hover {
  color: var(--ink-soft);
  border-color: var(--line-strong);
}

.count {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  opacity: 0.8;
}

.activity-list {
  list-style: none;
  margin: 10px 0 0;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(252, 252, 251, 0.7);
}

.activity-list li {
  display: grid;
  grid-template-columns: 52px 1fr;
  gap: 10px;
  padding: 6px 0;
  font-size: 0.82rem;
  line-height: 1.45;
  color: var(--ink-soft);
}

.activity-list li + li {
  border-top: 1px solid rgba(221, 217, 211, 0.6);
}

.tag {
  font-size: 0.68rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  font-weight: 600;
  color: var(--muted);
}

.activity-list .thought .tag { color: var(--thought); }
.activity-list .tool_start .tag,
.activity-list .tool_end .tag { color: var(--tool); }
.activity-list .skill_start .tag,
.activity-list .skill_end .tag { color: var(--skill); }
.activity-list .error .tag { color: var(--danger); }

.detail {
  min-width: 0;
  word-break: break-word;
}

.detail.link,
.trace-link {
  color: var(--accent, #2f6f5e);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.trace-link-wrap {
  margin: 8px 0 0;
  font-size: 0.8rem;
}

.hitl {
  margin-left: 44px;
  padding: 16px;
  border-radius: 14px;
  border: 1px solid var(--line-strong);
  border-left: 3px solid var(--accent);
  background: var(--accent-wash);
}

.hitl-head {
  font-family: var(--font-display);
  font-weight: 600;
  margin-bottom: 10px;
}

.plan {
  margin: 0 0 12px;
  padding-left: 1.2rem;
  color: var(--ink-soft);
  line-height: 1.55;
}

.hitl pre {
  margin: 0 0 12px;
  white-space: pre-wrap;
  font-family: var(--font-mono);
  font-size: 12px;
}

.hitl-actions {
  display: flex;
  gap: 8px;
}

.btn {
  appearance: none;
  border: 1px solid transparent;
  border-radius: 10px;
  padding: 8px 14px;
  font: inherit;
  font-size: 0.88rem;
  font-weight: 600;
  cursor: pointer;
}

.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.btn.primary {
  background: var(--accent);
  color: #f5f5f4;
}

.btn.primary:hover:not(:disabled) {
  background: var(--accent-hover);
}

.btn.ghost {
  background: transparent;
  border-color: var(--line-strong);
  color: var(--ink-soft);
}

.dock {
  flex-shrink: 0;
  padding: 10px 20px 18px;
  background: linear-gradient(180deg, transparent, rgba(243, 244, 246, 0.9) 28%);
}

.composer {
  max-width: var(--chat-width);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 14px 10px;
  border: 1px solid var(--line);
  border-radius: 22px;
  background: #fff;
  box-shadow: 0 8px 28px rgba(28, 25, 23, 0.06);
  position: relative;
}

.skill-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.skill-chip {
  appearance: none;
  border: 1px solid var(--line);
  background: var(--accent-wash);
  color: var(--accent);
  border-radius: 8px;
  padding: 4px 8px;
  font: inherit;
  font-size: 0.75rem;
  font-weight: 600;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.skill-chip:hover {
  border-color: var(--accent);
}

.skill-chip span {
  opacity: 0.7;
  font-size: 0.85rem;
  line-height: 1;
}

.input-wrap {
  position: relative;
}

.slash-pop {
  position: absolute;
  left: 0;
  right: 0;
  bottom: calc(100% + 10px);
  max-height: 240px;
  overflow: auto;
  padding: 6px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 12px 32px rgba(28, 25, 23, 0.12);
  z-index: 30;
}

.slash-head {
  padding: 6px 10px 8px;
  font-size: 0.7rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 600;
}

.slash-item {
  appearance: none;
  width: 100%;
  border: 0;
  background: transparent;
  text-align: left;
  padding: 8px 10px;
  border-radius: 10px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
  font: inherit;
}

.slash-item:hover,
.slash-item.on {
  background: rgba(26, 77, 62, 0.08);
}

.slash-item strong {
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--ink);
  font-family: var(--font-mono);
}

.slash-item em {
  font-style: normal;
  font-size: 0.75rem;
  color: var(--muted);
  line-height: 1.4;
}

.slash-empty {
  padding: 14px 10px;
  color: var(--muted);
  font-size: 0.84rem;
}

.slash-note {
  padding: 4px 10px 8px;
  color: var(--muted);
  font-size: 0.72rem;
}

.composer textarea {
  width: 100%;
  border: 0;
  outline: none;
  resize: none;
  background: transparent;
  font: inherit;
  font-size: 0.98rem;
  line-height: 1.5;
  max-height: 160px;
  min-height: 28px;
  padding: 2px 4px;
  color: var(--ink);
}

.composer textarea::placeholder {
  color: var(--muted);
}

.composer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding-top: 6px;
  border-top: 1px solid rgba(221, 217, 211, 0.7);
}

.tools {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}

.seg {
  display: inline-flex;
  padding: 2px;
  border-radius: 10px;
  background: rgba(28, 25, 23, 0.04);
}

.seg-item {
  appearance: none;
  border: 0;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.01em;
  padding: 6px 9px;
  border-radius: 8px;
  cursor: pointer;
  line-height: 1;
}

.seg-item.on {
  background: #fff;
  color: var(--ink);
  box-shadow: 0 1px 2px rgba(28, 25, 23, 0.08);
}

.tool-btn {
  appearance: none;
  border: 0;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  padding: 6px 10px;
  border-radius: 8px;
  cursor: pointer;
  line-height: 1;
}

.tool-btn:hover {
  background: rgba(28, 25, 23, 0.04);
  color: var(--ink-soft);
}

.tool-btn.on {
  background: var(--accent-wash);
  color: var(--accent);
}

.send {
  appearance: none;
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 50%;
  background: var(--ink);
  color: #f5f5f4;
  font-size: 1.05rem;
  line-height: 1;
  cursor: pointer;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}

.send:hover:not(:disabled) {
  background: #000;
}

.send:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.hint {
  max-width: var(--chat-width);
  margin: 8px auto 0;
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 0.72rem;
  color: var(--muted);
}

.session-id {
  font-family: var(--font-mono);
}

@media (max-width: 860px) {
  .column,
  .welcome {
    padding-left: 14px;
    padding-right: 14px;
  }
  .activity,
  .hitl {
    margin-left: 0;
  }
  .msg.assistant {
    grid-template-columns: 28px 1fr;
  }
  .dock {
    padding: 8px 12px 14px;
  }
  .hint {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
