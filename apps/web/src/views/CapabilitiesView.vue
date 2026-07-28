<template>
  <div class="caps page">
    <header class="page-header">
      <h1>能力</h1>
      <p>渐进披露目录：先看名称与简述，点开 Skill 再加载完整指令。</p>
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
        <div v-show="tab === 'tools'" class="pane">
          <div v-if="loading && !tools.length" class="empty">加载中…</div>
          <div v-else-if="!tools.length" class="empty">暂无工具</div>
          <ul v-else class="list">
            <li v-for="item in tools" :key="item.name" class="row">
              <div class="row-top">
                <strong>{{ item.name }}</strong>
                <span class="tier">{{ item.tier }}</span>
              </div>
              <p>{{ item.description || '—' }}</p>
            </li>
          </ul>
        </div>

        <div v-show="tab === 'skills'" class="pane">
          <div v-if="loading && !skills.length" class="empty">加载中…</div>
          <div v-else-if="!skills.length" class="empty">暂无 Skills</div>
          <ul v-else class="list">
            <li
              v-for="item in skills"
              :key="item.name"
              class="row is-clickable"
              @click="openSkill(item)"
            >
              <div class="row-top">
                <strong>{{ item.name }}</strong>
                <span class="tier is-soft">查看正文</span>
              </div>
              <p>{{ item.description || '—' }}</p>
              <div v-if="item.tools?.length" class="binds">
                {{ item.tools.join(' · ') }}
              </div>
            </li>
          </ul>
        </div>

        <div v-show="tab === 'mcp'" class="pane">
          <div v-if="loading && !mcp.length" class="empty">加载中…</div>
          <div v-else-if="!mcp.length" class="empty">未配置 MCP Server</div>
          <template v-else>
            <div v-for="server in mcp" :key="server.name" class="mcp-block">
              <h3>{{ server.name }}</h3>
              <p v-if="server.error" class="err">{{ server.error }}</p>
              <ul v-else class="list is-compact">
                <li v-for="tool in server.tools || []" :key="tool.name" class="row">
                  <div class="row-top">
                    <strong>{{ tool.name }}</strong>
                  </div>
                  <p>{{ tool.description || '—' }}</p>
                </li>
              </ul>
            </div>
          </template>
        </div>
      </div>
    </div>

    <el-drawer v-model="drawer" :title="detail?.name || 'Skill'" size="420px">
      <p class="drawer-desc">{{ detail?.description }}</p>
      <pre class="code-block">{{ detail?.body }}</pre>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  fetchMcp,
  fetchSkillDetail,
  fetchSkills,
  fetchTools,
  type McpServerItem,
  type SkillCatalogItem,
  type SkillDetail,
  type ToolCatalogItem,
} from '../api/capabilities'

const tabs = [
  { id: 'tools', label: 'Tools' },
  { id: 'skills', label: 'Skills' },
  { id: 'mcp', label: 'MCP' },
] as const

const tab = ref<(typeof tabs)[number]['id']>('tools')
const loading = ref(false)
const tools = ref<ToolCatalogItem[]>([])
const skills = ref<SkillCatalogItem[]>([])
const mcp = ref<McpServerItem[]>([])
const drawer = ref(false)
const detail = ref<SkillDetail | null>(null)
const loaded = ref(false)

async function loadAll() {
  if (loaded.value) return
  loading.value = true
  try {
    const [t, s, m] = await Promise.allSettled([
      fetchTools(),
      fetchSkills(),
      fetchMcp(),
    ])
    if (t.status === 'fulfilled') tools.value = t.value.items || []
    if (s.status === 'fulfilled') skills.value = s.value.items || []
    if (m.status === 'fulfilled') mcp.value = m.value.items || []
    loaded.value = true
  } finally {
    loading.value = false
  }
}

async function openSkill(row: SkillCatalogItem) {
  const res = await fetchSkillDetail(row.name)
  detail.value = res.item
  drawer.value = true
}

onMounted(loadAll)
</script>

<style scoped>
.tabs-wrap {
  overflow: hidden;
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

.seg-btn:focus {
  outline: none;
}

.seg-btn.is-active {
  background: var(--accent-wash);
  color: var(--accent);
}

.body {
  min-height: 320px;
  padding: 8px 6px 12px;
}

.pane {
  min-height: 304px;
}

.list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.row {
  padding: 14px 18px;
  border-bottom: 1px solid rgba(221, 217, 211, 0.75);
}

.row:last-child {
  border-bottom: none;
}

.row.is-clickable {
  cursor: pointer;
}

.row.is-clickable:hover {
  background: rgba(26, 77, 62, 0.04);
}

.row-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.row-top strong {
  font-family: var(--font-mono);
  font-size: 0.88rem;
  font-weight: 500;
}

.tier {
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent);
  font-weight: 600;
}

.tier.is-soft {
  color: var(--muted);
}

.row p {
  margin: 0;
  color: var(--ink-soft);
  line-height: 1.55;
  font-size: 0.92rem;
}

.binds {
  margin-top: 8px;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--muted);
}

.empty {
  padding: 64px 24px;
  text-align: center;
  color: var(--muted);
}

.mcp-block {
  padding: 8px 12px 16px;
}

.mcp-block h3 {
  margin: 8px 10px 4px;
  font-family: var(--font-display);
  font-weight: 600;
  font-size: 1.15rem;
}

.err {
  color: var(--danger);
  padding: 0 10px;
}

.drawer-desc {
  color: var(--muted);
  line-height: 1.55;
  margin-top: 0;
}

.list.is-compact .row {
  padding: 10px 12px;
}
</style>
