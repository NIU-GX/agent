<template>
  <div>
    <h2>用量</h2>
    <el-button @click="refresh">刷新</el-button>
    <pre class="result">{{ result }}</pre>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

const result = ref('')

async function refresh() {
  const resp = await fetch('/api/v1/metrics/usage', {
    headers: { 'X-API-Key': localStorage.getItem('apiKey') || 'dev-api-key-change-me' },
  })
  result.value = JSON.stringify(await resp.json(), null, 2)
}

onMounted(refresh)
</script>

<style scoped>
.result {
  margin-top: 16px;
  background: #0f172a;
  color: #e2e8f0;
  padding: 12px;
  border-radius: 8px;
}
</style>
