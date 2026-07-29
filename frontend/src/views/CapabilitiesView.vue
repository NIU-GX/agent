<template>
  <div class="caps page">
    <header class="page-header head">
      <div>
        <h1>能力</h1>
        <p>Tool / Skill / MCP 动态注册与管理；写操作立即热注入运行时。</p>
      </div>
      <div class="head-actions">
        <el-button @click="reload">刷新</el-button>
        <el-button v-if="tab === 'tools'" type="primary" @click="openCreateTool">新建 Webhook</el-button>
        <el-button v-else-if="tab === 'skills'" type="primary" @click="openCreateSkill">新建 Skill</el-button>
        <el-button v-else type="primary" @click="openCreateMcp">新建 MCP</el-button>
      </div>
    </header>

    <div class="tabs-wrap panel">
      <div class="seg" role="tablist">
        <button
          v-for="t in tabs"
          :key="t.id"
          type="button"
          role="tab"
          class="seg-btn"
          :class="{ 'is-active': tab === t.id }"
          :aria-selected="tab === t.id"
          @click="tab = t.id"
        >
          {{ t.label }}
        </button>
      </div>

      <div class="body" :aria-busy="loading">
        <!-- Tools -->
        <div v-show="tab === 'tools'" class="pane layout">
          <aside class="list-panel">
            <div v-if="loading && !tools.length" class="empty">加载中…</div>
            <div v-else-if="!tools.length" class="empty">暂无工具</div>
            <ul v-else class="list">
              <li
                v-for="item in tools"
                :key="item.name"
                class="row"
                :class="{ 'is-active': selectedTool === item.name }"
                @click="selectTool(item.name)"
              >
                <div class="row-top">
                  <strong>{{ item.name }}</strong>
                  <span class="tier">{{ item.source }}</span>
                </div>
                <p>{{ item.description || '—' }}</p>
                <div class="meta">
                  <span>{{ item.tier }}</span>
                  <span :class="item.enabled ? 'on' : 'off'">{{ item.enabled ? '启用' : '禁用' }}</span>
                </div>
              </li>
            </ul>
          </aside>
          <section v-if="toolDetail" class="detail-panel">
            <div class="detail-head">
              <div>
                <h2>{{ toolDetail.name }}</h2>
                <code class="key">{{ toolDetail.source }} · {{ toolDetail.tier }}</code>
              </div>
              <el-switch
                :model-value="toolDetail.enabled"
                :loading="toggling"
                @change="(v: boolean) => toggleTool(toolDetail!.name, v)"
              />
            </div>
            <template v-if="toolDetail.mutable">
              <label class="field-label">描述</label>
              <input v-model="toolForm.description" class="note-input" />
              <label class="field-label">Webhook URL</label>
              <input v-model="toolForm.webhook_url" class="note-input" />
              <label class="field-label">Method</label>
              <input v-model="toolForm.webhook_method" class="note-input" />
              <label class="field-label">Timeout (sec)</label>
              <input v-model.number="toolForm.timeout_sec" type="number" class="note-input" />
              <label class="field-label">Parameters (JSON)</label>
              <textarea v-model="toolForm.parametersJson" class="editor" rows="6" spellcheck="false" />
              <label class="field-label">Headers (JSON)</label>
              <textarea v-model="toolForm.headersJson" class="editor" rows="3" spellcheck="false" />
              <div class="publish-row">
                <el-button type="primary" :loading="saving" @click="saveTool">保存</el-button>
                <el-button type="danger" plain :loading="deleting" @click="removeTool">删除</el-button>
              </div>
            </template>
            <template v-else>
              <p class="desc">内置 / 元工具只读；可通过开关启用或禁用。</p>
              <pre class="code-block">{{ JSON.stringify(toolDetail.parameters || {}, null, 2) }}</pre>
            </template>
          </section>
          <section v-else class="detail-panel empty-detail">
            <div class="empty">选择左侧工具查看详情</div>
          </section>
        </div>

        <!-- Skills -->
        <div v-show="tab === 'skills'" class="pane layout">
          <aside class="list-panel">
            <div v-if="loading && !skills.length" class="empty">加载中…</div>
            <div v-else-if="!skills.length" class="empty">暂无 Skills</div>
            <ul v-else class="list">
              <li
                v-for="item in skills"
                :key="item.name"
                class="row"
                :class="{ 'is-active': selectedSkill === item.name }"
                @click="selectSkill(item.name)"
              >
                <div class="row-top">
                  <strong>{{ item.name }}</strong>
                  <span :class="item.enabled ? 'on' : 'off'">{{ item.enabled ? '启用' : '禁用' }}</span>
                </div>
                <p>{{ item.description || '—' }}</p>
              </li>
            </ul>
          </aside>
          <section v-if="skillDetail" class="detail-panel">
            <div class="detail-head">
              <div>
                <h2>{{ skillDetail.name }}</h2>
              </div>
              <el-switch
                :model-value="skillDetail.enabled"
                :loading="toggling"
                @change="(v: boolean) => toggleSkill(skillDetail!.name, v)"
              />
            </div>
            <label class="field-label">描述</label>
            <input v-model="skillForm.description" class="note-input" />
            <label class="field-label">绑定 tools（逗号分隔）</label>
            <input v-model="skillForm.toolsText" class="note-input" />
            <label class="field-label">绑定 mcp（逗号分隔）</label>
            <input v-model="skillForm.mcpText" class="note-input" />
            <label class="field-label">正文</label>
            <textarea v-model="skillForm.body" class="editor" rows="12" spellcheck="false" />
            <div class="publish-row">
              <el-button type="primary" :loading="saving" @click="saveSkill">保存</el-button>
              <el-button type="danger" plain :loading="deleting" @click="removeSkill">删除</el-button>
            </div>
          </section>
          <section v-else class="detail-panel empty-detail">
            <div class="empty">选择左侧 Skill 编辑</div>
          </section>
        </div>

        <!-- MCP -->
        <div v-show="tab === 'mcp'" class="pane layout">
          <aside class="list-panel">
            <div v-if="loading && !mcp.length" class="empty">加载中…</div>
            <div v-else-if="!mcp.length" class="empty">未配置 MCP Server</div>
            <ul v-else class="list">
              <li
                v-for="item in mcp"
                :key="item.name"
                class="row"
                :class="{ 'is-active': selectedMcp === item.name }"
                @click="selectMcp(item.name)"
              >
                <div class="row-top">
                  <strong>{{ item.name }}</strong>
                  <span :class="item.enabled ? 'on' : 'off'">{{ item.enabled ? '启用' : '禁用' }}</span>
                </div>
                <p>{{ item.command }}</p>
                <p v-if="item.last_error" class="err">{{ item.last_error }}</p>
              </li>
            </ul>
          </aside>
          <section v-if="mcpDetail" class="detail-panel">
            <div class="detail-head">
              <div>
                <h2>{{ mcpDetail.name }}</h2>
              </div>
              <el-switch
                :model-value="mcpDetail.enabled"
                :loading="toggling"
                @change="(v: boolean) => toggleMcp(mcpDetail!.name, v)"
              />
            </div>
            <label class="field-label">Command</label>
            <input v-model="mcpForm.command" class="note-input" />
            <label class="field-label">Args (JSON 数组)</label>
            <textarea v-model="mcpForm.argsJson" class="editor" rows="3" spellcheck="false" />
            <label class="field-label">Env (JSON 对象)</label>
            <textarea v-model="mcpForm.envJson" class="editor" rows="3" spellcheck="false" />
            <p v-if="mcpDetail.last_error" class="err">{{ mcpDetail.last_error }}</p>
            <div v-if="mcpDetail.tools?.length" class="binds">
              已发现：{{ mcpDetail.tools.map((t) => t.name).join(' · ') }}
            </div>
            <div class="publish-row">
              <el-button type="primary" :loading="saving" @click="saveMcp">保存</el-button>
              <el-button :loading="reconnecting" @click="doReconnect">重连</el-button>
              <el-button type="danger" plain :loading="deleting" @click="removeMcp">删除</el-button>
            </div>
          </section>
          <section v-else class="detail-panel empty-detail">
            <div class="empty">选择左侧 MCP Server</div>
          </section>
        </div>
      </div>
    </div>

    <!-- Create dialogs -->
    <el-dialog v-model="createToolOpen" title="新建 Webhook 工具" width="520px">
      <label class="field-label">名称</label>
      <input v-model="newTool.name" class="note-input" />
      <label class="field-label">描述</label>
      <input v-model="newTool.description" class="note-input" />
      <label class="field-label">Webhook URL</label>
      <input v-model="newTool.webhook_url" class="note-input" />
      <label class="field-label">Parameters JSON</label>
      <textarea v-model="newTool.parametersJson" class="editor" rows="4" spellcheck="false" />
      <template #footer>
        <el-button @click="createToolOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="createTool">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createSkillOpen" title="新建 Skill" width="520px">
      <label class="field-label">名称</label>
      <input v-model="newSkill.name" class="note-input" />
      <label class="field-label">描述</label>
      <input v-model="newSkill.description" class="note-input" />
      <label class="field-label">正文</label>
      <textarea v-model="newSkill.body" class="editor" rows="8" spellcheck="false" />
      <template #footer>
        <el-button @click="createSkillOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="createSkillItem">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createMcpOpen" title="新建 MCP Server" width="520px">
      <label class="field-label">名称</label>
      <input v-model="newMcp.name" class="note-input" />
      <label class="field-label">Command</label>
      <input v-model="newMcp.command" class="note-input" />
      <label class="field-label">Args (JSON)</label>
      <textarea v-model="newMcp.argsJson" class="editor" rows="3" spellcheck="false" />
      <template #footer>
        <el-button @click="createMcpOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="createMcpItem">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import {
  createMcpServer,
  deleteMcpServer,
  listAdminMcp,
  reconnectMcp,
  setMcpEnabled,
  updateMcpServer,
  type AdminMcpServer,
} from '../api/mcp'
import {
  createSkill,
  deleteSkill,
  getAdminSkill,
  listAdminSkills,
  setSkillEnabled,
  updateSkill,
  type AdminSkill,
} from '../api/skills'
import {
  createWebhookTool,
  deleteWebhookTool,
  listAdminTools,
  setToolEnabled,
  updateWebhookTool,
  type AdminTool,
} from '../api/tools'

const tabs = [
  { id: 'tools', label: 'Tools' },
  { id: 'skills', label: 'Skills' },
  { id: 'mcp', label: 'MCP' },
] as const

const tab = ref<(typeof tabs)[number]['id']>('tools')
const loading = ref(false)
const saving = ref(false)
const deleting = ref(false)
const toggling = ref(false)
const reconnecting = ref(false)

const tools = ref<AdminTool[]>([])
const skills = ref<AdminSkill[]>([])
const mcp = ref<AdminMcpServer[]>([])

const selectedTool = ref('')
const selectedSkill = ref('')
const selectedMcp = ref('')
const toolDetail = ref<AdminTool | null>(null)
const skillDetail = ref<AdminSkill | null>(null)
const mcpDetail = ref<AdminMcpServer | null>(null)

const toolForm = reactive({
  description: '',
  webhook_url: '',
  webhook_method: 'POST',
  timeout_sec: 30,
  parametersJson: '{}',
  headersJson: '{}',
})
const skillForm = reactive({
  description: '',
  body: '',
  toolsText: '',
  mcpText: '',
})
const mcpForm = reactive({
  command: '',
  argsJson: '[]',
  envJson: '{}',
})

const createToolOpen = ref(false)
const createSkillOpen = ref(false)
const createMcpOpen = ref(false)
const newTool = reactive({
  name: '',
  description: '',
  webhook_url: '',
  parametersJson: '{"type":"object","properties":{}}',
})
const newSkill = reactive({ name: '', description: '', body: '' })
const newMcp = reactive({ name: '', command: '', argsJson: '[]' })

function parseJson<T>(text: string, fallback: T): T {
  try {
    return JSON.parse(text) as T
  } catch {
    throw new Error('JSON 格式无效')
  }
}

function splitCsv(text: string): string[] {
  return text
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

async function reload() {
  loading.value = true
  try {
    const [t, s, m] = await Promise.allSettled([
      listAdminTools(),
      listAdminSkills(),
      listAdminMcp(),
    ])
    if (t.status === 'fulfilled') tools.value = t.value.items || []
    if (s.status === 'fulfilled') skills.value = s.value.items || []
    if (m.status === 'fulfilled') mcp.value = m.value.items || []
    if (selectedTool.value) await selectTool(selectedTool.value)
    if (selectedSkill.value) await selectSkill(selectedSkill.value)
    if (selectedMcp.value) selectMcp(selectedMcp.value)
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function selectTool(name: string) {
  selectedTool.value = name
  const item = tools.value.find((x) => x.name === name) || null
  toolDetail.value = item
  if (!item) return
  toolForm.description = item.description || ''
  toolForm.webhook_url = item.webhook_url || ''
  toolForm.webhook_method = item.webhook_method || 'POST'
  toolForm.timeout_sec = item.timeout_sec || 30
  toolForm.parametersJson = JSON.stringify(item.parameters || {}, null, 2)
  toolForm.headersJson = JSON.stringify(item.webhook_headers || {}, null, 2)
}

async function selectSkill(name: string) {
  selectedSkill.value = name
  try {
    const item = await getAdminSkill(name)
    skillDetail.value = item
    skillForm.description = item.description || ''
    skillForm.body = item.body || ''
    skillForm.toolsText = (item.tools || []).join(', ')
    skillForm.mcpText = (item.mcp || []).join(', ')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '加载 Skill 失败')
  }
}

function selectMcp(name: string) {
  selectedMcp.value = name
  const item = mcp.value.find((x) => x.name === name) || null
  mcpDetail.value = item
  if (!item) return
  mcpForm.command = item.command || ''
  mcpForm.argsJson = JSON.stringify(item.args || [], null, 2)
  mcpForm.envJson = JSON.stringify(item.env || {}, null, 2)
}

async function toggleTool(name: string, enabled: boolean) {
  toggling.value = true
  try {
    await setToolEnabled(name, enabled)
    ElMessage.success(enabled ? '已启用' : '已禁用')
    await reload()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '操作失败')
  } finally {
    toggling.value = false
  }
}

async function toggleSkill(name: string, enabled: boolean) {
  toggling.value = true
  try {
    await setSkillEnabled(name, enabled)
    ElMessage.success(enabled ? '已启用' : '已禁用')
    await reload()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '操作失败')
  } finally {
    toggling.value = false
  }
}

async function toggleMcp(name: string, enabled: boolean) {
  toggling.value = true
  try {
    await setMcpEnabled(name, enabled)
    ElMessage.success(enabled ? '已启用' : '已禁用')
    await reload()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '操作失败')
  } finally {
    toggling.value = false
  }
}

async function saveTool() {
  if (!toolDetail.value?.mutable) return
  saving.value = true
  try {
    const parameters = parseJson(toolForm.parametersJson, {})
    const headers = parseJson(toolForm.headersJson, {})
    await updateWebhookTool(toolDetail.value.name, {
      description: toolForm.description,
      webhook_url: toolForm.webhook_url,
      webhook_method: toolForm.webhook_method,
      timeout_sec: toolForm.timeout_sec,
      parameters,
      webhook_headers: headers,
    })
    ElMessage.success('已保存')
    await reload()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeTool() {
  if (!toolDetail.value?.mutable) return
  try {
    await ElMessageBox.confirm(`删除 Webhook 工具「${toolDetail.value.name}」？`, '确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  deleting.value = true
  try {
    await deleteWebhookTool(toolDetail.value.name)
    selectedTool.value = ''
    toolDetail.value = null
    ElMessage.success('已删除')
    await reload()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '删除失败')
  } finally {
    deleting.value = false
  }
}

async function saveSkill() {
  if (!skillDetail.value) return
  saving.value = true
  try {
    await updateSkill(skillDetail.value.name, {
      description: skillForm.description,
      body: skillForm.body,
      tools: splitCsv(skillForm.toolsText),
      mcp: splitCsv(skillForm.mcpText),
    })
    ElMessage.success('已保存')
    await reload()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeSkill() {
  if (!skillDetail.value) return
  try {
    await ElMessageBox.confirm(`删除 Skill「${skillDetail.value.name}」？`, '确认', { type: 'warning' })
  } catch {
    return
  }
  deleting.value = true
  try {
    await deleteSkill(skillDetail.value.name)
    selectedSkill.value = ''
    skillDetail.value = null
    ElMessage.success('已删除')
    await reload()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '删除失败')
  } finally {
    deleting.value = false
  }
}

async function saveMcp() {
  if (!mcpDetail.value) return
  saving.value = true
  try {
    const args = parseJson<unknown[]>(mcpForm.argsJson, [])
    const env = parseJson<Record<string, unknown>>(mcpForm.envJson, {})
    await updateMcpServer(mcpDetail.value.name, {
      command: mcpForm.command,
      args,
      env,
    })
    ElMessage.success('已保存并重连')
    await reload()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeMcp() {
  if (!mcpDetail.value) return
  try {
    await ElMessageBox.confirm(`删除 MCP「${mcpDetail.value.name}」？`, '确认', { type: 'warning' })
  } catch {
    return
  }
  deleting.value = true
  try {
    await deleteMcpServer(mcpDetail.value.name)
    selectedMcp.value = ''
    mcpDetail.value = null
    ElMessage.success('已删除')
    await reload()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '删除失败')
  } finally {
    deleting.value = false
  }
}

async function doReconnect() {
  if (!mcpDetail.value) return
  reconnecting.value = true
  try {
    const res = await reconnectMcp(mcpDetail.value.name)
    if (res.ok) ElMessage.success('重连成功')
    else ElMessage.warning(res.error || '重连失败')
    await reload()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '重连失败')
  } finally {
    reconnecting.value = false
  }
}

function openCreateTool() {
  newTool.name = ''
  newTool.description = ''
  newTool.webhook_url = ''
  newTool.parametersJson = '{"type":"object","properties":{}}'
  createToolOpen.value = true
}

function openCreateSkill() {
  newSkill.name = ''
  newSkill.description = ''
  newSkill.body = ''
  createSkillOpen.value = true
}

function openCreateMcp() {
  newMcp.name = ''
  newMcp.command = ''
  newMcp.argsJson = '[]'
  createMcpOpen.value = true
}

async function createTool() {
  if (!newTool.name.trim() || !newTool.webhook_url.trim()) {
    ElMessage.warning('名称与 URL 必填')
    return
  }
  saving.value = true
  try {
    const parameters = parseJson(newTool.parametersJson, {
      type: 'object',
      properties: {},
    })
    await createWebhookTool({
      name: newTool.name.trim(),
      description: newTool.description,
      webhook_url: newTool.webhook_url.trim(),
      parameters,
    })
    createToolOpen.value = false
    ElMessage.success('已创建')
    await reload()
    await selectTool(newTool.name.trim())
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '创建失败')
  } finally {
    saving.value = false
  }
}

async function createSkillItem() {
  if (!newSkill.name.trim()) {
    ElMessage.warning('名称必填')
    return
  }
  saving.value = true
  try {
    await createSkill({
      name: newSkill.name.trim(),
      description: newSkill.description,
      body: newSkill.body,
    })
    createSkillOpen.value = false
    ElMessage.success('已创建')
    await reload()
    await selectSkill(newSkill.name.trim())
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '创建失败')
  } finally {
    saving.value = false
  }
}

async function createMcpItem() {
  if (!newMcp.name.trim() || !newMcp.command.trim()) {
    ElMessage.warning('名称与 command 必填')
    return
  }
  saving.value = true
  try {
    const args = parseJson<unknown[]>(newMcp.argsJson, [])
    await createMcpServer({
      name: newMcp.name.trim(),
      command: newMcp.command.trim(),
      args,
    })
    createMcpOpen.value = false
    ElMessage.success('已创建')
    await reload()
    selectMcp(newMcp.name.trim())
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '创建失败')
  } finally {
    saving.value = false
  }
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

.head-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.tabs-wrap {
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}

.seg {
  display: flex;
  gap: 4px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
  background: rgba(28, 25, 23, 0.015);
}

.seg-btn {
  appearance: none;
  -webkit-appearance: none;
  border: 0;
  margin: 0;
  background: transparent;
  padding: 8px 14px;
  border-radius: var(--radius);
  color: var(--muted);
  font-weight: 600;
  font-family: inherit;
  font-size: 0.9rem;
  line-height: 1.2;
  cursor: pointer;
}

.seg-btn:hover {
  color: var(--ink);
  background: rgba(28, 25, 23, 0.04);
}

.seg-btn.is-active {
  background: var(--accent-wash);
  color: var(--accent);
}

.body {
  min-height: 420px;
  padding: 0;
  flex: 1;
}

.pane.layout {
  display: grid;
  grid-template-columns: minmax(240px, 300px) minmax(0, 1fr);
  min-height: 420px;
  max-height: calc(100vh - 220px);
}

.list-panel,
.detail-panel {
  overflow: auto;
  min-height: 420px;
}

.list-panel {
  border-right: 1px solid var(--line);
  padding: 8px;
}

.detail-panel {
  padding: 20px 22px 28px;
}

.list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.row {
  padding: 12px 14px;
  border-radius: var(--radius);
  cursor: pointer;
  border: 1px solid transparent;
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
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.row-top strong {
  font-family: var(--font-mono);
  font-size: 0.88rem;
  font-weight: 500;
}

.row p,
.desc {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.45;
}

.meta {
  margin-top: 8px;
  display: flex;
  gap: 10px;
  font-size: 12px;
  color: var(--muted);
  font-family: var(--font-mono);
}

.tier {
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent);
  font-weight: 600;
}

.on {
  color: var(--accent);
  font-size: 12px;
}

.off {
  color: var(--danger);
  font-size: 12px;
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

.key {
  display: inline-block;
  margin-top: 4px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-soft);
}

.field-label {
  display: block;
  margin: 16px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-soft);
}

.note-input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface);
  box-sizing: border-box;
}

.editor {
  width: 100%;
  resize: vertical;
  min-height: 80px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.55;
  box-sizing: border-box;
}

.publish-row {
  display: flex;
  gap: 10px;
  margin-top: 16px;
  flex-wrap: wrap;
}

.empty,
.empty-detail .empty {
  padding: 64px 24px;
  text-align: center;
  color: var(--muted);
}

.err {
  color: var(--danger);
  margin-top: 8px;
  font-size: 13px;
}

.binds {
  margin-top: 12px;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--muted);
}

.caps {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

@media (max-width: 860px) {
  .pane.layout {
    grid-template-columns: 1fr;
    max-height: none;
  }

  .list-panel {
    border-right: none;
    border-bottom: 1px solid var(--line);
    max-height: 280px;
  }
}
</style>
