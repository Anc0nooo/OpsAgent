/**
 * 版本更新 API 封装
 * - getVersion：任意已登录用户拉取当前版本号 + 更新日志
 * - updateVersion：管理员保存版本号 / 更新日志
 */
import { createAuthHttp } from './request'

const http = createAuthHttp({ baseURL: '/api', timeout: 30000 })

/** 版本信息 */
export interface VersionInfo {
  version: string
  changelog: string
  updated_at: string
}

/** 获取当前版本与更新日志 */
export async function getVersion(): Promise<VersionInfo> {
  const resp = await http.get('/version')
  return resp.data.data
}

/** 管理员更新版本号 / 更新日志 */
export async function updateVersion(payload: {
  version: string
  changelog: string
}): Promise<VersionInfo> {
  const resp = await http.put('/version', payload)
  return resp.data.data
}
