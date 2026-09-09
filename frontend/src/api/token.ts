/**
 * JWT token 存取（独立模块：无第三方依赖，避免与 request.ts 循环引用）
 */

const TOKEN_KEY = 'opsagent_token'

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

/** 过期标记（sessionStorage：整页跳转后仍在，登录页据此弹提示；关闭标签页即清） */
const AUTH_EXPIRED_FLAG = 'opsagent_auth_expired'

/** 幂等标记：同一页面生命周期内并发请求命中 401 只处理一次 */
let _handling401 = false

/**
 * 401 统一处理（token 过期/无效）：
 * 1. 清 token
 * 2. 写过期标记（登录页挂载时读取，弹"登录已过期，请重新登录"）
 * 3. 跳 /login（整页刷新，内存中的用户信息/状态随之清空，避免脏数据）
 * 幂等：并发请求同时 401 时只跳转/提示一次。
 */
export function handleUnauthorized(): void {
  if (_handling401) return
  // 已在登录页（如登录页自身的探测请求 401）：无需跳转，也不弹过期提示
  if (location.pathname.startsWith('/login')) return
  _handling401 = true
  clearToken()
  try {
    sessionStorage.setItem(AUTH_EXPIRED_FLAG, '1')
  } catch { /* 隐私模式等场景忽略 */ }
  location.href = '/login'
}

/**
 * 登录页挂载时调用：若有过期标记则消费（读取并清除），返回 true 表示应弹
 * "登录已过期，请重新登录"提示。
 */
export function consumeAuthExpiredFlag(): boolean {
  try {
    if (sessionStorage.getItem(AUTH_EXPIRED_FLAG)) {
      sessionStorage.removeItem(AUTH_EXPIRED_FLAG)
      return true
    }
  } catch { /* ignore */ }
  return false
}
