/**
 * 知识库管理接口封装（阶段 6 前端完善）
 * 对应后端 /api/knowledge/*
 */
import { createAuthHttp } from './request'

const http = createAuthHttp({ baseURL: '/api/knowledge', timeout: 60000 })

/** 文档信息 */
export interface DocInfo {
  id: number
  title: string
  doc_type: string
  source: string
  chunk_count: number
  created_at: string
}

/** 检索命中 */
export interface SearchHit {
  doc_id: number
  doc_title: string
  section: string
  doc_type: string
  score: number
  text: string
}

/** 文档详情（含全文，供在线查看/编辑） */
export interface DocDetail extends DocInfo {
  text: string
  updated_at?: string
}

/** 统一响应拆包 */
function unwrap<T>(data: { code: number; message: string; data: T }, fallbackMsg: string): T {
  if (data.code !== 0) throw new Error(data.message || fallbackMsg)
  return data.data
}

/** 文档列表 */
export async function listDocs(): Promise<DocInfo[]> {
  const r = await http.get('/docs')
  return unwrap<DocInfo[]>(r.data, '获取文档列表失败')
}

/** 上传文档（txt/md/sql/pdf/docx） */
export async function uploadDoc(file: File, docType: string, title?: string): Promise<{ doc_id: number }> {
  const form = new FormData()
  form.append('file', file)
  form.append('doc_type', docType)
  if (title) form.append('title', title)
  const r = await http.post('/upload', form)
  return unwrap<{ doc_id: number }>(r.data, '上传失败')
}

/** 粘贴文本入库 */
export async function addText(title: string, text: string, docType: string): Promise<{ doc_id: number }> {
  const r = await http.post('/text', { title, text, doc_type: docType })
  return unwrap<{ doc_id: number }>(r.data, '入库失败')
}

/** 导入表结构（DDL 或 DESC 输出） */
export async function importTableSchema(content: string, tableName = ''): Promise<{ count: number }> {
  const r = await http.post('/table_schema', { content, table_name: tableName })
  return unwrap<{ count: number }>(r.data, '导入失败')
}

/** 删除文档 */
export async function deleteDoc(docId: number): Promise<void> {
  const r = await http.delete(`/docs/${docId}`)
  unwrap(r.data, '删除失败')
}

/** 查看文档详情（含全文） */
export async function getDoc(docId: number): Promise<DocDetail> {
  const r = await http.get(`/docs/${docId}`)
  return unwrap<DocDetail>(r.data, '获取文档失败')
}

/** 编辑文档（保留 doc_id，重新切分入库） */
export async function updateDoc(
  docId: number,
  body: { title: string; text: string; doc_type: string },
): Promise<void> {
  const r = await http.put(`/docs/${docId}`, body)
  unwrap(r.data, '保存失败')
}

/** 轻量切换文档类型（不重新切分，仅更新元数据与向量 metadata） */
export async function changeDocType(docId: number, docType: string): Promise<void> {
  const r = await http.patch(`/docs/${docId}/type`, { doc_type: docType })
  unwrap(r.data, '类型更新失败')
}

/** 混合检索测试 */
export async function searchDocs(query: string, docType?: string, topK = 5): Promise<SearchHit[]> {
  const r = await http.post('/search', { query, doc_type: docType || null, top_k: topK })
  return unwrap<SearchHit[]>(r.data, '检索失败')
}

/** 重建索引：按当前分块策略（更大块 + 重叠）重新切分并重新向量化全部文档；文档保留，耗时较长 */
export async function reindex(): Promise<{ docs: number; chunks: number }> {
  const r = await http.post('/reindex', {}, { timeout: 600000 })
  return unwrap<{ docs: number; chunks: number }>(r.data, '重建索引失败')
}

// ---------------- 聊天记录导入 ----------------
/** 聊天记录解析请求体 */
export interface ChatParseBody {
  source: string        // wechat / feishu / auto
  text: string
  split_mode: string    // merge / day / person
  filter_system: boolean
  filter_media: boolean
}

/** 聊天记录解析预览 */
export interface ChatPreview {
  count: number
  participants: string[]
  time_range: string
  preview: { time: string; sender: string; content: string }[]
  format: string
  split_mode: string
}

/** 解析聊天记录预览（不入库） */
export async function parseChat(body: ChatParseBody): Promise<ChatPreview> {
  const r = await http.post('/chat/parse', body)
  return unwrap<ChatPreview>(r.data, '解析失败')
}

/** 导入聊天记录入库（类型固定 other），返回导入文档数与标题 */
export async function importChat(body: ChatParseBody): Promise<{ count: number; titles: string[] }> {
  const r = await http.post('/chat/import', body)
  return unwrap<{ count: number; titles: string[] }>(r.data, '导入失败')
}
