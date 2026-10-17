/**
 * 知识库管理接口封装（阶段 6 前端完善）
 * 对应后端 /api/knowledge/*
 */
import { createAuthHttp } from './request'
import { getToken } from './token'

const http = createAuthHttp({ baseURL: '/api/knowledge', timeout: 60000 })

/** 知识库原图引用（后端回传相对 URL，展示时用 kbImageUrl 拼 token） */
export interface KbImageRef {
  id: number
  /** 相对路径 /api/knowledge/image/{id} */
  url: string
  page: number | null
}

/** 给图片接口 URL 附加鉴权 token（<img> 标签无法带 Authorization 头） */
export function kbImageUrl(url: string): string {
  const token = getToken()
  return token ? `${url}?token=${encodeURIComponent(token)}` : url
}

/** 每用户切分参数（仅模式B文档 guide/bug/other 生效） */
export interface KbSettings {
  chunk_size: number
  chunk_overlap: number
}

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
  /** 起始页码（PDF/Word；纯文本文档为 null） */
  page?: number | null
  /** 所在页页眉 */
  header?: string
  /** 所在页页脚 */
  footer?: string
  /** 命中块内原图 */
  images?: KbImageRef[]
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

/** 上传结果（提交后立即返回，实际入库在后台任务执行，进度走 getProgress 轮询） */
export interface UploadAccepted {
  task: 'accepted'
  message: string
}

/** 上传文档（txt/md/sql/pdf/docx）；提交后后台处理，前端轮询进度 */
export async function uploadDoc(file: File, docType: string, title?: string): Promise<UploadAccepted> {
  const form = new FormData()
  form.append('file', file)
  form.append('doc_type', docType)
  if (title) form.append('title', title)
  const r = await http.post('/upload', form)
  return unwrap<UploadAccepted>(r.data, '上传失败')
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

/** 重建索引：提交后台任务（重新切分并重新向量化全部文档）；进度与结果走 getProgress 轮询 */
export async function reindex(): Promise<{ task: 'accepted'; message: string }> {
  const r = await http.post('/reindex', {})
  return unwrap<{ task: 'accepted'; message: string }>(r.data, '重建索引失败')
}

/** 读取当前用户切分参数（未设置过返回默认 500/50） */
export async function getKbSettings(): Promise<KbSettings> {
  const r = await http.get('/settings')
  return unwrap<KbSettings>(r.data, '获取切分参数失败')
}

/** 入库/重建进度（前端轮询展示进度条；后端任务异步执行，进度单调递增） */
export interface KbProgressResult {
  doc_id?: number
  chunks?: number
  failed_chunks?: number
  failed_sample?: string[]
  docs?: number
  message?: string
}

export interface KbProgress {
  status: 'idle' | 'processing' | 'done' | 'error'
  phase?: 'queued' | 'parsing' | 'splitting' | 'embedding'
  done?: number
  total?: number
  detail?: string
  /** 任务成功结束的结果（doc_id/块数/失败块明细等） */
  result?: KbProgressResult
  /** 任务失败的分类错误信息（API 限流/超时/token 超限/解析失败等） */
  error?: string
}

/** 查询当前用户入库/重建进度 */
export async function getProgress(): Promise<KbProgress> {
  const r = await http.get('/progress', { timeout: 10000 })
  return unwrap<KbProgress>(r.data, '获取进度失败')
}

/** 保存当前用户切分参数（修改后需重建索引才对存量文档生效） */
export async function saveKbSettings(body: KbSettings): Promise<KbSettings> {
  const r = await http.put('/settings', body)
  return unwrap<KbSettings>(r.data, '保存切分参数失败')
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
  /** 是否识别为聊天记录格式；false 时后端将按纯文本文档降级导入 */
  recognized: boolean
  /** 降级导入时的原文字数 */
  text_length?: number
  message?: string
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
