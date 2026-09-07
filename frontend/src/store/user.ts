/**
 * 当前用户全局状态（轻量 store，无 pinia 依赖）
 * - username / avatar 响应式共享（侧边栏等组件直接导入使用）
 * - loadUser：启动时按 token 拉取当前用户
 */
import { ref } from 'vue'
import { fetchMe, logout as apiLogout, type UserInfo } from '../api/auth'
import { getToken } from '../api/token'

export const username = ref('')
export const avatar = ref('') // data URL；空 = 用首字符兜底

/** 已登录（有 token 且已拉到用户名） */
export const isLoggedIn = ref(false)

/** 启动/登录后调用：拉取当前用户信息 */
export async function loadUser(): Promise<UserInfo | null> {
  if (!getToken()) {
    resetUser()
    return null
  }
  try {
    const me = await fetchMe()
    if (me) {
      username.value = me.username
      avatar.value = me.avatar || ''
      isLoggedIn.value = true
      return me
    }
  } catch {
    // 401 由拦截器统一跳登录；这里静默
  }
  resetUser()
  return null
}

/** 退出登录：清服务端状态 + 本地 */
export function doLogout(): void {
  apiLogout()
  resetUser()
}

function resetUser(): void {
  username.value = ''
  avatar.value = ''
  isLoggedIn.value = false
}

/** 头像兜底：用户名首字符（中文原样，英文大写） */
export function avatarInitial(): string {
  const name = username.value.trim()
  if (!name) return '?'
  return name.charAt(0).toUpperCase()
}

/** 按用户名 hash 取固定背景色（柔和色板，DeepSeek 风格低饱和） */
export function avatarColor(): string {
  const palette = ['#6b8afd', '#5cad8f', '#e6935c', '#b58de0', '#5cabd6', '#d98a9a']
  let h = 0
  for (const ch of username.value) h = (h * 31 + ch.codePointAt(0)!) >>> 0
  return palette[h % palette.length]
}
