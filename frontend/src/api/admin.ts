/**
 * 管理员 API 封装（role == 'ancon' 才可调用；后端 require_admin 校验）
 * - 用户管理：列表/启用禁用/改角色/重置密码/删除
 * - 操作日志：列表/按用户查/导出 CSV
 */
import { createAuthHttp } from './request'
import { getToken } from './token'

const http = createAuthHttp({ baseURL: '/api/admin', timeout: 30000 })

/** 用户列表项 */
export interface AdminUserItem {
  id: number
  username: string
  role: string
  status: number
  avatar: string
  created_at: string
}

/** 日志列表项 */
export interface LogItem {
  id: number
  user_id: number
  username: string
  action: string
  detail: string
  ip: string
  created_at: string
}

interface PageResult<T> {
  total: number
  page: number
  page_size: number
  items: T[]
}

/** 用户列表（分页 + 关键词） */
export async function listUsers(params: {
  page?: number
  page_size?: number
  keyword?: string
}): Promise<PageResult<AdminUserItem>> {
  const resp = await http.get('/users', { params })
  return resp.data.data
}

/** 启用/禁用用户 */
export async function updateStatus(userId: number, status: number): Promise<void> {
  await http.patch(`/users/${userId}/status`, { status })
}

/** 修改角色 */
export async function updateRole(userId: number, role: string): Promise<void> {
  await http.patch(`/users/${userId}/role`, { role })
}

/** 重置密码 */
export async function resetPassword(userId: number, password: string): Promise<void> {
  await http.post(`/users/${userId}/reset-password`, { password })
}

/** 删除用户（级联清理） */
export async function deleteUser(userId: number): Promise<void> {
  await http.delete(`/users/${userId}`)
}

/** 全部日志（分页 + 筛选） */
export async function listLogs(params: {
  page?: number
  page_size?: number
  user_id?: number
  action?: string
  start_time?: string
  end_time?: string
}): Promise<PageResult<LogItem>> {
  const resp = await http.get('/logs', { params })
  return resp.data.data
}

/** 指定用户日志 */
export async function userLogs(userId: number, params: {
  page?: number
  page_size?: number
}): Promise<PageResult<LogItem>> {
  const resp = await http.get(`/logs/user/${userId}`, { params })
  return resp.data.data
}

/** 导出日志 CSV（浏览器下载） */
export async function exportLogs(params: {
  user_id?: number
  action?: string
  start_time?: string
  end_time?: string
}): Promise<void> {
  const token = getToken()
  const qs = new URLSearchParams()
  if (params.user_id) qs.set('user_id', String(params.user_id))
  if (params.action) qs.set('action', params.action)
  if (params.start_time) qs.set('start_time', params.start_time)
  if (params.end_time) qs.set('end_time', params.end_time)
  const url = `/api/admin/logs/export?${qs.toString()}`
  const resp = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!resp.ok) throw new Error('导出失败')
  const blob = await resp.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'operation_logs.csv'
  a.click()
  URL.revokeObjectURL(a.href)
}
