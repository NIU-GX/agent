<template>
  <div class="shell">
    <aside class="rail">
      <div class="brand">
        <span class="brand-mark">A</span>
        <div class="brand-text">
          <strong>Agent</strong>
          <em>Platform</em>
        </div>
      </div>
      <nav class="nav">
        <router-link
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          class="nav-link"
          :class="{ 'is-active': isActive(item.to) }"
        >
          <span class="nav-label">{{ item.label }}</span>
          <span class="nav-hint">{{ item.hint }}</span>
        </router-link>
      </nav>
      <div class="rail-foot">
        <span class="dot" />
        <span>内部工作台</span>
      </div>
    </aside>
    <main class="stage">
      <router-view v-slot="{ Component }">
        <keep-alive>
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </main>
  </div>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()

const nav = [
  { to: '/', label: '对话', hint: 'Chat' },
  { to: '/capabilities', label: '能力', hint: 'CRUD' },
  { to: '/prompts', label: '提示词', hint: 'Prompts' },
  { to: '/docs', label: '知识库', hint: 'Docs' },
  { to: '/eval', label: '评测', hint: 'Eval' },
  { to: '/usage', label: '用量', hint: 'Usage' },
]

function isActive(to: string) {
  if (to === '/') return route.path === '/'
  return route.path === to || route.path.startsWith(`${to}/`)
}
</script>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  height: 100%;
  max-height: 100%;
}

.rail {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 28px 16px 20px;
  border-right: 1px solid var(--line);
  background: rgba(252, 252, 251, 0.78);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 10px 28px;
  flex-shrink: 0;
}

.brand-mark {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: var(--ink);
  color: #f5f5f4;
  font-family: var(--font-display);
  font-size: 1.15rem;
  font-weight: 600;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}

.brand-text strong {
  font-family: var(--font-display);
  font-size: 1.15rem;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.brand-text em {
  font-style: normal;
  font-size: 0.72rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 500;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-height: 0;
}

.nav-link {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--radius);
  color: var(--ink-soft);
  background: transparent;
}

.nav-link:hover {
  background: rgba(28, 25, 23, 0.04);
  color: var(--ink);
}

.nav-link.is-active {
  background: var(--accent-wash);
  color: var(--accent);
}

.nav-label {
  font-weight: 600;
  font-size: 0.95rem;
}

.nav-hint {
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
  opacity: 0.85;
}

.nav-link.is-active .nav-hint {
  color: var(--accent);
  opacity: 0.7;
}

.rail-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 12px 4px;
  font-size: 0.75rem;
  color: var(--muted);
  letter-spacing: 0.04em;
  flex-shrink: 0;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0.7;
}

.stage {
  min-width: 0;
  height: 100%;
  overflow: auto;
  padding: var(--stage-pad-y) var(--stage-pad-x);
  scrollbar-gutter: stable;
}

@media (max-width: 860px) {
  .shell {
    grid-template-columns: 1fr;
    grid-template-rows: auto minmax(0, 1fr);
  }
  .rail {
    border-right: none;
    border-bottom: 1px solid var(--line);
    padding: 16px;
    height: auto;
  }
  .nav {
    flex-direction: row;
    overflow-x: auto;
    gap: 4px;
  }
  .nav-hint,
  .rail-foot {
    display: none;
  }
}
</style>
