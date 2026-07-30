<template>
  <div class="page">
    <header class="page-header head">
      <div>
        <h1>评测</h1>
        <p>Retrieval / Generation（DeepEval）/ Trajectory，展示关键指标与原始 JSON。</p>
      </div>
      <div class="actions">
        <el-button type="primary" :loading="loading" @click="run('retrieval')">
          Retrieval
        </el-button>
        <el-button :loading="loading" @click="run('generation')">Generation</el-button>
        <el-button :loading="loading" @click="run('trajectory')">Trajectory</el-button>
      </div>
    </header>

    <div v-if="metrics.length" class="metrics">
      <div v-for="m in metrics" :key="m.key" class="metric">
        <span class="metric-key">{{ m.key }}</span>
        <strong class="metric-val">{{ m.value }}</strong>
      </div>
    </div>

    <div class="panel result-wrap">
      <pre v-if="result" class="code-block">{{ result }}</pre>
      <div v-else class="empty">选择一种评测后在此查看输出</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { runEval } from '../api/eval'

const loading = ref(false)
const result = ref('')
const metrics = ref<Array<{ key: string; value: string }>>([])

function extractMetrics(data: Record<string, unknown>) {
  const skip = new Set(['kind', 'details', 'n'])
  return Object.entries(data)
    .filter(([k, v]) => !skip.has(k) && (typeof v === 'number' || typeof v === 'string'))
    .map(([key, value]) => ({
      key,
      value: typeof value === 'number' ? value.toFixed(3) : String(value),
    }))
}

async function run(kind: string) {
  loading.value = true
  try {
    const data = (await runEval(kind)) as Record<string, unknown>
    metrics.value = extractMetrics(data)
    if (typeof data.n === 'number') {
      metrics.value.unshift({ key: 'n', value: String(data.n) })
    }
    result.value = JSON.stringify(data, null, 2)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
}

.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
}

.metric {
  min-width: 120px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(252, 252, 251, 0.8);
}

.metric-key {
  display: block;
  font-size: 0.72rem;
  color: var(--muted);
  margin-bottom: 4px;
}

.metric-val {
  font-size: 1.1rem;
  font-variant-numeric: tabular-nums;
}

.result-wrap {
  min-height: 280px;
  padding: 12px;
}

.empty {
  padding: 64px 24px;
  text-align: center;
  color: var(--muted);
}
</style>
