<template>
  <div>
    <h2>Eval</h2>
    <el-space>
      <el-button type="primary" :loading="loading" @click="run('retrieval')">跑 Retrieval Eval</el-button>
      <el-button :loading="loading" @click="run('trajectory')">跑 Trajectory Eval</el-button>
    </el-space>
    <pre class="result">{{ result }}</pre>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const loading = ref(false)
const result = ref('')

async function run(kind: string) {
  loading.value = true
  try {
    const resp = await fetch('/api/v1/eval/runs', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': localStorage.getItem('apiKey') || 'dev-api-key-change-me',
      },
      body: JSON.stringify({ kind }),
    })
    result.value = JSON.stringify(await resp.json(), null, 2)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.result {
  margin-top: 16px;
  background: #0f172a;
  color: #e2e8f0;
  padding: 12px;
  border-radius: 8px;
  max-height: 60vh;
  overflow: auto;
}
</style>
