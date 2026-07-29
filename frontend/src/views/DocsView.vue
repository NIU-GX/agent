<template>
  <div class="page">
    <header class="page-header head">
      <div>
        <h1>知识库</h1>
        <p>上传文档进入异步入库流水线，完成后可被检索与引用。</p>
      </div>
      <el-upload :http-request="upload" :show-file-list="false">
        <el-button type="primary">上传文档</el-button>
      </el-upload>
    </header>

    <div class="panel table-wrap">
      <el-table :data="docs" empty-text="暂无文档">
        <el-table-column prop="filename" label="文件名" min-width="180" />
        <el-table-column prop="status" label="状态" width="120" />
        <el-table-column prop="chunk_count" label="Chunks" width="100" />
        <el-table-column prop="id" label="ID" min-width="240" />
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { UploadRequestOptions } from 'element-plus'
import { listDocuments, uploadDocument, type DocumentItem } from '../api/documents'

const docs = ref<DocumentItem[]>([])

async function refresh() {
  const data = await listDocuments()
  docs.value = data.items || []
}

async function upload(opt: UploadRequestOptions) {
  try {
    const data = await uploadDocument(opt.file as File)
    opt.onSuccess?.(data as any)
    await refresh()
  } catch (e) {
    opt.onError?.(e as any)
  }
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

.table-wrap {
  padding: 8px;
  overflow: hidden;
}
</style>
