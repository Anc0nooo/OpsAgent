/**
 * 模型配置接口（单一阿里百炼）
 * - 对话 / 向量 / 重排 统一阿里百炼（DASHSCOPE_API_KEY + DASHSCOPE_BASE_URL）
 * - GET /status 返回 has_api_key + api_key_masked（不返回明文 key）
 */
import { createAuthHttp } from './request'

const http = createAuthHttp({ baseURL: '/api/settings', timeout: 20000 })

/** 当前配置状态（不返回 key 明文；has_api_key + api_key_masked 供回填提示） */
export interface SettingsStatus {
  chat_provider: string
  chat_provider_name: string
  chat_base_url: string
  chat_api_key_masked: string
  has_api_key: boolean
  api_key_masked: string
  chat_configured: boolean
  chat_model: string
  dashscope_configured: boolean
  dashscope_key_masked: string
  embed_model: string
  rerank_model: string
  first_time?: boolean
}

const EMPTY_STATUS: SettingsStatus = {
  chat_provider: 'dashscope',
  chat_provider_name: '阿里百炼',
  chat_base_url: '',
  chat_api_key_masked: '',
  has_api_key: false,
  api_key_masked: '',
  chat_configured: false,
  chat_model: '',
  dashscope_configured: false,
  dashscope_key_masked: '',
  embed_model: '',
  rerank_model: '',
}

/** 查询当前配置状态 */
export async function getSettingsStatus(): Promise<SettingsStatus> {
  const r = await http.get('/status')
  return r.data?.data ?? EMPTY_STATUS
}

/** 保存配置（仅百炼 Key），热重载（base_url/模型名后端固定）；字段映射为后端 ConfigIn.api_key */
export async function saveConfig(body: {
  dashscope_api_key?: string
}): Promise<{ ok: boolean; message?: string }> {
  const r = await http.post('/keys', { api_key: body.dashscope_api_key ?? '' })
  return { ok: r.data?.code === 0, message: r.data?.message }
}

/** 测试百炼连通性（仅传 API Key；base_url/模型名后端固定）；字段映射为后端 TestIn.api_key */
export async function testConfig(body: {
  dashscope_api_key: string
}): Promise<{ ok: boolean; message: string }> {
  try {
    const r = await http.post('/test', { api_key: body.dashscope_api_key })
    return { ok: r.data?.code === 0, message: r.data?.message || '测试完成' }
  } catch (e: unknown) {
    const err = e as { response?: { data?: { message?: string } } }
    const msg = err?.response?.data?.message || (e instanceof Error ? e.message : '测试请求失败')
    return { ok: false, message: msg }
  }
}

/* ============================================================
   百炼 API Key 本地存储（localStorage）
   - 后端 /status 不回显 key 明文（安全设计）
   - 前端本地保存真实 key，供设置弹窗回填密文圆点显示
   ============================================================ */
const DASHSCOPE_KEY_STORAGE = 'opsagent_dashscope_key'

/** 读取本地百炼 key（无则空串） */
export function getDashscopeKey(): string {
  return localStorage.getItem(DASHSCOPE_KEY_STORAGE) || ''
}

/** 保存百炼 key 到本地（空串则清除） */
export function saveDashscopeKey(key: string): void {
  if (key) localStorage.setItem(DASHSCOPE_KEY_STORAGE, key)
  else localStorage.removeItem(DASHSCOPE_KEY_STORAGE)
}
