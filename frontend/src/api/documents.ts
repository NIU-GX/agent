import { apiGet, apiPostForm } from './http'

export type DocumentItem = {
  id: string
  filename: string
  status: string
  chunk_count: number
}

export async function listDocuments() {
  return apiGet<{ items: DocumentItem[] }>('/documents')
}

export async function uploadDocument(file: File) {
  const form = new FormData()
  form.append('file', file)
  return apiPostForm('/documents', form)
}
