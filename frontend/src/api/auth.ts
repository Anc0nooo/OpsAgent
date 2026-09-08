/**
 * 认证接口封装
 * - login / register：用户名密码登录与注册
 * - fetchMe：当前用户信息（含头像 data URL）
 * - uploadAvatar / removeAvatar：头像上传与移除
 */
import request from './request'
import { clearToken, getToken, setToken } from './token'

/** 当前用户信息 */
export interface UserInfo {
  user_id: number
  username: string
  status: number
  /** 角色：ancon=管理员 / user=普通用户 */
  role: string
  /** 头像 data URL；空串 = 未上传（前端用用户名首字符兜底） */
  avatar: string
}

/** 登录：成功后自动保存 token */
export async function login(username: string, password: string): Promise<UserInfo> {
  const resp = await request.post('/auth/login', { username, password })
  const data = resp.data?.data as { token: string; user_id: number; username: string; role: string }
  setToken(data.token)
  // 拉取完整信息（含头像 + role）
  const me = await fetchMe()
  return me ?? { user_id: data.user_id, username: data.username, status: 1, role: data.role, avatar: '' }
}

/** 注册 */
export async function register(username: string, password: string): Promise<void> {
  await request.post('/auth/register', { username, password })
}

/** 当前用户信息（未登录返回 null） */
export async function fetchMe(): Promise<UserInfo | null> {
  if (!getToken()) return null
  try {
    const resp = await request.get('/auth/me')
    return resp.data?.data as UserInfo
  } catch (e) {
    // 401 已由拦截器处理（跳登录）；其他错误静默
    throw e
  }
}

/** 上传头像（后端限制 2MB；建议前端先压缩），返回 data URL */
export async function uploadAvatar(file: Blob, filename = 'avatar.jpg'): Promise<string> {
  const fd = new FormData()
  fd.append('file', file, filename)
  const resp = await request.post('/auth/avatar', fd)
  return (resp.data?.data?.avatar as string) || ''
}

/** 移除头像（恢复默认用户名首字符） */
export async function removeAvatar(): Promise<void> {
  await request.delete('/auth/avatar')
}

/** 退出登录：清 token */
export function logout(): void {
  clearToken()
}
