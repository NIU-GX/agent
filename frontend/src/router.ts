import { createRouter, createWebHistory } from 'vue-router'
import CapabilitiesView from './views/CapabilitiesView.vue'
import ChatView from './views/ChatView.vue'
import DocsView from './views/DocsView.vue'
import EvalView from './views/EvalView.vue'
import PromptsView from './views/PromptsView.vue'
import UsageView from './views/UsageView.vue'

export const router = createRouter({
  history: createWebHistory(),
  linkActiveClass: 'noop-active',
  linkExactActiveClass: 'noop-exact',
  routes: [
    { path: '/', name: 'chat', component: ChatView },
    { path: '/capabilities', name: 'capabilities', component: CapabilitiesView },
    { path: '/prompts', name: 'prompts', component: PromptsView },
    { path: '/docs', name: 'docs', component: DocsView },
    { path: '/eval', name: 'eval', component: EvalView },
    { path: '/usage', name: 'usage', component: UsageView },
  ],
})
