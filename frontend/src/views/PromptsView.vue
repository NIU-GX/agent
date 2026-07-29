<template>
  <div class="page prompts">
    <header class="page-header head">
      <div>
        <h1>提示词</h1>
        <p>版本管理与回退：发版后立即生效，可随时切回历史版本。</p>
      </div>
      <el-button @click="reload">刷新</el-button>
    </header>

    <div class="layout">
      <aside class="panel list-panel">
        <div v-if="loading && !items.length" class="empty">加载中…</div>
        <div v-else-if="!items.length" class="empty">暂无提示词</div>
        <ul v-else class="list">
          <li
            v-for="item in items"
            :key="item.key"
            class="row"
            :class="{ 'is-active': selectedKey === item.key }"
            @click="select(item.key)"
          >
            <div class="row-top">
              <strong>{{ item.name }}</strong>
              <span class="ver">v{{ item.active_version }}</span>
            </div>
            <code class="key">{{ item.key }}</code>
            <p>{{ item.description || item.content_preview || '—' }}</p>
          </li>
        </ul>
      </aside>

      <section class="panel detail-panel" v-if="detail">
        <div class="detail-head">
          <div>
            <h2>{{ detail.name }}</h2>
            <code class="key">{{ detail.key }}</code>
          </div>
          <span class="badge">当前 v{{ detail.active_version }}</span>
        </div>
        <p class="desc">{{ detail.description }}</p>

        <label class="field-label">编辑正文（发布为新版本）</label>
        <textarea v-model="draft" class="editor" rows="12" spellcheck="false" />
        <div class="publish-row">
          <input v-model="changeNote" class="note-input" placeholder="变更说明（可选）" />
          <el-button type="primary" :loading="publishing" @click="publish">发布新版本</el-button>
        </div>

        <h3 class="hist-title">版本历史</h3>
        <ul class="hist">
          <li v-for="v in detail.versions" :key="v.id" class="hist-row">
            <div class="hist-meta">
              <strong>v{{ v.version }}</strong>
              <span v-if="v.is_active" class="badge is-soft">激活中</span>
              <span class="muted">{{ formatTime(v.created_at) }}</span>
              <span v-if="v.change_note" class="note">{{ v.change_note }}</span>
            </div>
            <div class="hist-actions">
              <el-button size="small" text @click="preview(v)">查看</el-button>
              <el-button
                v-if="!v.is_active"
                size="small"
                :loading="rolling === v.version"
                @click="doRollback(v.version)"
              >
                回退到此版本
              </el-button>
            </div>
          </li>
        </ul>
      </section>

      <section v-else class="panel detail-panel empty-detail">
        <div class="empty">选择左侧提示词查看版本</div>
      </section>
    </div>

    <el-drawer v-model="drawer" :title="previewTitle" size="480px">
      <pre class="code-block">{{ previewContent }}</pre>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, ref } from 'vue'
import {
  createPromptVersion,
  fetchPrompt,
  fetchPrompts,
  rollbackPrompt,
  type PromptDetail,
  type PromptListItem,
  type PromptVersion,
} from '../api/prompts'

const items = ref<PromptListItem[]>([])
const detail = ref<PromptDetail | null>(null)
const selectedKey = ref('')
const draft = ref('')
const changeNote = ref('')
const loading = ref(false)
const publishing = ref(false)
const rolling = ref<number | null>(null)
const drawer = ref(false)
const previewContent = ref('')
const previewTitle = ref('')

function formatTime(iso: string | null) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

async function reload() {
  loading.value = true
  try {
    const data = await fetchPrompts()
    items.value = data.items || []
    if (selectedKey.value) {
      await select(selectedKey.value)
    } else if (items.value[0]) {
      await select(items.value[0].key)
    }
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function select(key: string) {
  selectedKey.value = key
  try {
    detail.value = await fetchPrompt(key)
    const active = detail.value.versions.find((v) => v.is_active)
    draft.value = active?.content || detail.value.versions[0]?.content || ''
    changeNote.value = ''
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '加载详情失败')
  }
}

async function publish() {
  if (!selectedKey.value || !draft.value.trim()) {
    ElMessage.warning('正文不能为空')
    return
  }
  publishing.value = true
  try {
    await createPromptVersion(selectedKey.value, {
      content: draft.value,
      change_note: changeNote.value || undefined,
      activate: true,
    })
    ElMessage.success('已发布并激活新版本')
    await reload()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '发布失败')
  } finally {
    publishing.value = false
  }
}

async function doRollback(version: number) {
  try {
    await ElMessageBox.confirm(
      `确认将「${selectedKey.value}」回退到 v${version}？运行中对话的下一轮请求将使用该版本。`,
      '回退确认',
      { type: 'warning', confirmButtonText: '回退', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  rolling.value = version
  try {
    const res = await rollbackPrompt(selectedKey.value, version)
    ElMessage.success(`已从 v${res.from_version} 回退到 v${res.active_version}`)
    await reload()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '回退失败')
  } finally {
    rolling.value = null
  }
}

function preview(v: PromptVersion) {
  previewTitle.value = `${detail.value?.name || ''} · v${v.version}`
  previewContent.value = v.content
  drawer.value = true
}

onMounted(reload)
</script>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
}

.layout {
  display: grid;
  grid-template-columns: minmax(240px, 300px) minmax(0, 1fr);
  gap: 16px;
  min-height: 0;
  flex: 1;
  align-items: stretch;
}

.prompts {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.list-panel,
.detail-panel {
  min-height: 420px;
  max-height: calc(100vh - 160px);
  overflow: auto;
  padding: 0;
}

.list {
  list-style: none;
  margin: 0;
  padding: 8px;
}

.row {
  padding: 12px 14px;
  border-radius: var(--radius);
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.18s var(--ease), border-color 0.18s var(--ease);
}

.row:hover {
  background: var(--accent-wash);
}

.row.is-active {
  background: var(--accent-wash);
  border-color: rgba(26, 77, 62, 0.28);
}

.row-top {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: baseline;
}

.row p,
.desc {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.45;
}

.key {
  display: inline-block;
  margin-top: 4px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-soft);
}

.ver {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--accent);
}

.detail-panel {
  padding: 20px 22px 28px;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.detail-head h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: 1.35rem;
  font-weight: 600;
}

.badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--accent);
  background: var(--accent-wash);
  border-radius: 4px;
}

.badge.is-soft {
  color: var(--ink-soft);
  background: rgba(28, 25, 23, 0.06);
}

.field-label,
.hist-title {
  display: block;
  margin: 20px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-soft);
}

.editor {
  width: 100%;
  resize: vertical;
  min-height: 200px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.55;
}

.editor:focus {
  outline: 2px solid rgba(26, 77, 62, 0.25);
  border-color: var(--accent);
}

.publish-row {
  display: flex;
  gap: 10px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.note-input {
  flex: 1;
  min-width: 180px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface);
}

.hist {
  list-style: none;
  margin: 0;
  padding: 0;
}

.hist-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 12px 0;
  border-top: 1px solid var(--line);
  flex-wrap: wrap;
}

.hist-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.muted {
  color: var(--muted);
  font-size: 12px;
}

.note {
  font-size: 13px;
  color: var(--ink-soft);
}

.empty,
.empty-detail .empty {
  padding: 64px 24px;
  text-align: center;
  color: var(--muted);
}

@media (max-width: 860px) {
  .layout {
    grid-template-columns: 1fr;
  }

  .list-panel,
  .detail-panel {
    max-height: none;
  }
}
</style>
