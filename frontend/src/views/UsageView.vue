<template>
  <div class="page">
    <header class="page-header head">
      <div>
        <h1>用量</h1>
        <p>网关侧模型调用与 Token 汇总。</p>
      </div>
      <el-button @click="refresh">刷新</el-button>
    </header>

    <div class="panel result-wrap">
      <pre v-if="result" class="code-block">{{ result }}</pre>
      <div v-else class="empty">暂无数据</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchUsage } from '../api/usage'

const result = ref('')

async function refresh() {
  result.value = JSON.stringify(await fetchUsage(), null, 2)
}

onMounted(refresh)
</script>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
}

.result-wrap {
  min-height: 240px;
  padding: 12px;
}

.empty {
  padding: 64px 24px;
  text-align: center;
  color: var(--muted);
}
</style>
