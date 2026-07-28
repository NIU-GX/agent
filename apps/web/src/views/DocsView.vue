<template>
  <div>
    <h2>知识库</h2>
    <el-upload :http-request="upload" :show-file-list="false">
      <el-button type="primary">上传文档</el-button>
    </el-upload>
    <el-table :data="docs" style="margin-top: 16px">
      <el-table-column prop="id" label="ID" width="280" />
      <el-table-column prop="filename" label="文件名" />
      <el-table-column prop="status" label="状态" width="120" />
      <el-table-column prop="chunk_count" label="Chunks" width="100" />
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { UploadRequestOptions } from 'element-plus'

const docs = ref<any[]>([])
const apiKey = () => localStorage.getItem('apiKey') || 'dev-api-key-change-me'

async function refresh() {
  const resp = await fetch('/api/v1/documents', { headers: { 'X-API-Key': apiKey() } })
  const data = await resp.json()
  docs.value = data.items || []
}

async function upload(opt: UploadRequestOptions) {
  const form = new FormData()
  form.append('file', opt.file)
  const resp = await fetch('/api/v1/documents', {
    method: 'POST',
    headers: { 'X-API-Key': apiKey() },
    body: form,
  })
  if (!resp.ok) {
    opt.onError?.(new Error(await resp.text()) as any)
    return
  }
  opt.onSuccess?.(await resp.json() as any)
  await refresh()
}

onMounted(refresh)
</script>
