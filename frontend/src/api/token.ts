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

/** 401 统一处理：清 token 跳登录页（避免重复跳转） */
export function handleUnauthorized(): void {
  clearToken()
  if (!location.pathname.startsWith('/login')) {
    location.href = '/login'
  }
}
