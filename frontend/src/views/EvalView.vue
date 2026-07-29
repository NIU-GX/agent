<template>
  <div class="page">
    <header class="page-header head">
      <div>
        <h1>评测</h1>
        <p>运行 Retrieval 或 Trajectory 评测，结果以 JSON 展示。</p>
      </div>
      <div class="actions">
        <el-button type="primary" :loading="loading" @click="run('retrieval')">
          Retrieval
        </el-button>
        <el-button :loading="loading" @click="run('trajectory')">Trajectory</el-button>
      </div>
    </header>

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

async function run(kind: string) {
  loading.value = true
  try {
    result.value = JSON.stringify(await runEval(kind), null, 2)
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
