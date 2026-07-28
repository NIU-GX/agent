import { createRouter, createWebHistory } from 'vue-router'
import ChatView from './views/ChatView.vue'
import DocsView from './views/DocsView.vue'
import EvalView from './views/EvalView.vue'
import UsageView from './views/UsageView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: ChatView },
    { path: '/docs', component: DocsView },
    { path: '/eval', component: EvalView },
    { path: '/usage', component: UsageView },
  ],
})
