<template>
  <div class="chat">
    <div class="toolbar">
      <el-select v-model="strategy" style="width: 220px">
        <el-option label="Auto" value="auto" />
        <el-option label="CoT" value="cot" />
        <el-option label="ReAct" value="react" />
        <el-option label="Plan-and-Execute" value="plan_execute" />
      </el-select>
      <el-switch v-model="enableRag" active-text="RAG" />
    </div>
    <div class="timeline" ref="timelineRef">
      <div v-for="(item, idx) in timeline" :key="idx" class="item" :class="item.kind">
        <strong>{{ item.kind }}</strong>
        <pre>{{ item.text }}</pre>
      </div>
      <div v-if="pendingHitl" class="hitl">
        <h3>待确认计划</h3>
        <pre>{{ JSON.stringify(pendingHitl.plan_steps || pendingHitl.payload, null, 2) }}</pre>
        <div class="hitl-actions">
          <el-button type="primary" :loading="loading" @click="resume(true)">批准并继续</el-button>
          <el-button :loading="loading" @click="resume(false)">拒绝</el-button>
        </div>
      </div>
      <div class="answer" v-if="answer">
        <h3>回答</h3>
        <pre>{{ answer }}</pre>
      </div>
    </div>
    <div class="composer">
      <el-input
        v-model="message"
        type="textarea"
        :rows="3"
        placeholder="输入业务问题，例如：对比两份政策文档的差异并给出结论"
        @keydown.enter.exact.prevent="send"
      />
      <el-button type="primary" :loading="loading" @click="send">发送</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { resumeChat, streamChat, type ChatEvent } from '../api/chat'

const strategy = ref('auto')
const enableRag = ref(true)
const message = ref('')
const loading = ref(false)
const answer = ref('')
const sessionId = ref<string | null>(null)
const lastQuestion = ref('')
const pendingHitl = ref<Record<string, unknown> | null>(null)
const timeline = ref<{ kind: string; text: string }[]>([])

function handleEvent(ev: ChatEvent) {
  if (ev.type === 'strategy') {
    sessionId.value = String(ev.data.session_id || sessionId.value || '')
    timeline.value.push({ kind: 'strategy', text: JSON.stringify(ev.data) })
  } else if (ev.type === 'thought') {
    timeline.value.push({ kind: 'thought', text: String(ev.data.text || '') })
  } else if (ev.type === 'plan') {
    timeline.value.push({ kind: 'plan', text: JSON.stringify(ev.data.steps || []) })
  } else if (ev.type === 'hitl') {
    pendingHitl.value = ev.data
    sessionId.value = String(ev.data.session_id || sessionId.value || '')
    timeline.value.push({ kind: 'hitl', text: JSON.stringify(ev.data) })
  } else if (ev.type === 'tool_start' || ev.type === 'tool_end') {
    timeline.value.push({ kind: ev.type, text: JSON.stringify(ev.data) })
  } else if (ev.type === 'token') {
    answer.value += String(ev.data.text || '')
  } else if (ev.type === 'final') {
    pendingHitl.value = null
    answer.value = String(ev.data.answer || answer.value)
    if (ev.data.session_id) sessionId.value = String(ev.data.session_id)
  } else if (ev.type === 'error') {
    timeline.value.push({ kind: 'error', text: String(ev.data.message || '') })
  }
}

async function send() {
  if (!message.value.trim() || loading.value) return
  loading.value = true
  answer.value = ''
  timeline.value = []
  pendingHitl.value = null
  lastQuestion.value = message.value
  try {
    await streamChat(
      { message: message.value, strategy: strategy.value, enable_rag: enableRag.value },
      handleEvent,
    )
  } catch (e) {
    timeline.value.push({ kind: 'error', text: String(e) })
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
        plan_steps: (pendingHitl.value?.plan_steps as string[] | undefined) || undefined,
      },
      handleEvent,
    )
  } catch (e) {
    timeline.value.push({ kind: 'error', text: String(e) })
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.chat { display: flex; flex-direction: column; gap: 12px; height: calc(100vh - 48px); }
.toolbar { display: flex; gap: 16px; align-items: center; }
.timeline {
  flex: 1;
  overflow: auto;
  background: linear-gradient(180deg, #f7f9fc, #eef3f8);
  border: 1px solid #dbe3ee;
  border-radius: 8px;
  padding: 12px;
}
.item { margin-bottom: 8px; font-size: 13px; }
.item pre, .answer pre, .hitl pre {
  white-space: pre-wrap;
  margin: 4px 0 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.hitl {
  border: 1px solid #c9d7ea;
  background: #f4f8fc;
  padding: 12px;
  border-radius: 8px;
  margin: 8px 0;
}
.hitl-actions { display: flex; gap: 8px; margin-top: 8px; }
.composer { display: flex; gap: 8px; align-items: flex-end; }
.composer .el-input { flex: 1; }
</style>
