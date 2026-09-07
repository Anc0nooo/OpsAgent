/**
 * 对话接口封装（阶段 4）
 * - streamChat: POST /api/chat/stream 的 SSE 流式解析（fetch + ReadableStream）
 * - 会话历史恢复/新会话
 */
import { createAuthHttp } from './request'
import { getToken, handleUnauthorized } from './token'

/** SSE 事件结构 */
export interface ChatEvent {
  type: 'status' | 'delta' | 'done' | 'error'
  stage?: string
  text?: string
  content?: string
  session_id?: string
  state?: string
  need_query?: boolean
  query_round?: number
  pending_query?: PendingQuery
  message?: string
  /** error 事件附加：后端分类错误码（invalid_key / quota_exceeded / forbidden / network / model_not_found） */
  error_code?: string
  /** error 事件附加：原始错误详情（仅供调试） */
  detail?: string
  /** done 事件附加：四段总结卡片 / 引用来源（阶段 5） */
  sections?: SummarySections
  sources?: SourceRef[]
  /** done 事件附加：回答质量自评（仅工作类问题产出；闲聊无此字段） */
  evaluation?: AnswerEvaluation
}

/** 回答质量评估（LLM 自评，随工作类回答的 done 事件返回） */
export interface AnswerEvaluation {
  /** 知识库命中度：高 / 中 / 低 */
  source_coverage: string
  /** 回答准确度百分比字符串，如 "85%" */
  accuracy: string
  /** 幻觉风险：低 / 中 / 高 */
  hallucination_risk: string
}

/** 引用来源文档（含 doc_id，可点击查看） */
export interface SourceRef {
  doc_id: number
  doc_title: string
}

/** 四段总结卡片结构 */
export interface SummarySections {
  conclusion?: string | null
  analysis?: string | null
  steps?: string | null
  risks?: string | null
  raw?: string
}

/** 挂起的只读查询（SQL 卡片） */
export interface PendingQuery {
  sql: string
  purpose: string
  round: number
}

/** 会话详情（历史恢复用） */
export interface SessionDetail {
  id: string
  title: string
  state: string
  pending_query: string | null
  query_round: number
  messages: { role: 'user' | 'assistant'; content: string }[]
}

const http = createAuthHttp({ baseURL: '/api', timeout: 30000 })

/** 带 Authorization 的 fetch 请求头（SSE / 文件导出用；axios 实例已由工厂统一附加） */
function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}`, ...extra } : { ...extra }
}

/**
 * 流式对话：POST SSE 逐事件回调
 * 返回 done 事件（含 session_id / state / pending_query）
 */
export async function streamChat(
  text: string,
  sessionId: string | null,
  handlers: {
    onStatus?: (stage: string, text: string) => void
    onDelta?: (content: string) => void
  },
): Promise<ChatEvent> {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ text, session_id: sessionId }),
  })
  if (!resp.ok || !resp.body) {
    if (resp.status === 401) handleUnauthorized()
    throw new Error(`服务异常（HTTP ${resp.status}）`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let doneEvent: ChatEvent | null = null

  try {
    // 逐块解析 SSE：以空行分隔事件，data: 前缀取 JSON
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, idx).trim()
        buf = buf.slice(idx + 2)
        if (!block.startsWith('data: ')) continue
        const payload = block.slice(6)
        if (payload === '[DONE]') continue
        try {
          const ev = JSON.parse(payload) as ChatEvent
          if (ev.type === 'status') handlers.onStatus?.(ev.stage || '', ev.text || '')
          else if (ev.type === 'delta') handlers.onDelta?.(ev.content || '')
          else if (ev.type === 'done') doneEvent = ev
          else if (ev.type === 'error') {
            // error 事件：把 error_code 和 message 挂到 Error 上抛出，ChatView 可分类处理
            const err = new Error(ev.message || '处理失败') as Error & { error_code?: string; detail?: string }
            err.error_code = ev.error_code
            err.detail = ev.detail
            throw err
          }
        } catch (e) {
          if (e instanceof SyntaxError) continue // 非 JSON 块忽略
          throw e
        }
      }
    }
  } finally {
    // 提前退出（异常/超时）时取消读取并释放连接，避免浏览器连接池被占用
    // （连接泄漏会导致后续请求全部挂起，表现为一直"连接服务…"）
    try { await reader.cancel() } catch { /* 流已结束，忽略 */ }
  }
  return doneEvent || { type: 'done' }
}

/** 会话摘要（列表用） */
export interface SessionItem {
  id: string
  title: string
  state: string
  query_round: number
  created_at: string
  updated_at: string
  is_pinned: boolean
}

/** 会话列表 */
export async function listSessions(): Promise<SessionItem[]> {
  try {
    const r = await http.get('/agent/sessions')
    return (r.data?.data ?? []) as SessionItem[]
  } catch {
    return []
  }
}

/** 加载会话历史（刷新后恢复） */
export async function loadSession(sessionId: string): Promise<SessionDetail | null> {
  try {
    const r = await http.get(`/agent/sessions/${sessionId}`)
    return r.data?.data ?? null
  } catch {
    return null
  }
}

/** 删除会话 */
export async function deleteSession(sessionId: string): Promise<void> {
  await http.delete(`/agent/sessions/${sessionId}`).catch(() => undefined)
}

/** 置顶 / 取消置顶会话 */
export async function pinSession(sessionId: string, pinned: boolean): Promise<void> {
  await http.patch(`/agent/sessions/${sessionId}/pin`, { pinned })
}

/** 批量删除会话 */
export async function batchDeleteSessions(ids: string[]): Promise<number> {
  const r = await http.delete('/agent/sessions/batch', { data: { ids } })
  return (r.data?.data?.deleted as number) ?? 0
}

/**
 * 存为知识：把会话最后一条方案存入知识库（后端归为 bug记录 类型）
 * 返回入库文档 ID
 */
export async function saveSessionKnowledge(sessionId: string): Promise<{ doc_id: number; title: string }> {
  const r = await http.post('/output/save_knowledge', { session_id: sessionId })
  if (r.data?.code !== 0) throw new Error(r.data?.message || '存为知识失败')
  return r.data.data as { doc_id: number; title: string }
}

/**
 * 导出文件下载（md / sql / csv）
 * POST 接口返回文件文本，前端 Blob 触发浏览器下载
 */
export async function exportFile(
  kind: 'markdown' | 'sql' | 'csv',
  body?: Record<string, unknown>,
): Promise<void> {
  const resp = await fetch(`/api/output/export/${kind}`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!resp.ok) {
    if (resp.status === 401) handleUnauthorized()
    let msg = `导出失败（HTTP ${resp.status}）`
    try {
      const j = (await resp.json()) as { detail?: string }
      if (j.detail) msg = j.detail
    } catch { /* 非 JSON 响应，保留默认提示 */ }
    throw new Error(msg)
  }
  // 从 Content-Disposition 取文件名（回退默认）
  const disp = resp.headers.get('Content-Disposition') || ''
  const m = /filename="?([^";]+)"?/.exec(disp)
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = m?.[1] || `opsagent_export.${kind}`
  a.click()
  URL.revokeObjectURL(url)
}
