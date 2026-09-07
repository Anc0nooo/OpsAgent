/**
 * axios 统一封装
 * - createAuthHttp：带鉴权的实例工厂（所有模块统一使用）
 *   · 请求拦截：自动附加 Authorization: Bearer <token>
 *   · 响应拦截：统一解析 {code, message, data}；401 清 token 跳登录页
 * - request：默认实例（baseURL /api）
 */
import axios from 'axios'
import type { AxiosInstance, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { getToken, handleUnauthorized } from './token'

/** 后端统一响应结构 */
export interface ApiResponse<T = unknown> {
  code: number
  message: string
  data: T
}

/** 从错误响应提取友好消息（后端 {message} / FastAPI {detail} / axios 默认文案） */
function extractErrorMessage(error: { response?: { status?: number; data?: { message?: string; detail?: string } }; message?: string }): string {
  const status = error?.response?.status
  const data = error?.response?.data
  return (
    data?.message ||
    data?.detail ||
    (status === 401 ? '登录已过期，请重新登录' : undefined) ||
    error?.message ||
    '网络请求失败'
  )
}

/**
 * 带鉴权的 axios 实例工厂（所有模块统一入口）
 * - 自动附加 Bearer token
 * - code !== 0 / HTTP 错误统一 reject Error(友好消息)
 * - 401 清 token 跳登录页
 */
export function createAuthHttp(config: { baseURL: string; timeout?: number }): AxiosInstance {
  const instance = axios.create(config)

  // 请求拦截：自动附加 JWT
  instance.interceptors.request.use((cfg: InternalAxiosRequestConfig) => {
    const token = getToken()
    if (token) {
      cfg.headers.Authorization = `Bearer ${token}`
    }
    return cfg
  })

  // 响应拦截：统一拆包与错误处理
  instance.interceptors.response.use(
    (response: AxiosResponse<ApiResponse>) => {
      const body = response.data
      // 非统一结构的响应直接返回（如文件下载）
      if (typeof body !== 'object' || body === null || !('code' in body)) {
        return response
      }
      if (body.code !== 0) {
        console.error('[OpsAgent] 业务错误:', body.message)
        return Promise.reject(new Error(body.message || '业务处理失败'))
      }
      return response
    },
    (error) => {
      // 401：token 无效/过期 → 清 token 跳登录
      if (error?.response?.status === 401) {
        handleUnauthorized()
      }
      console.error('[OpsAgent] 请求异常:', extractErrorMessage(error))
      return Promise.reject(new Error(extractErrorMessage(error)))
    },
  )

  return instance
}

// 默认实例（baseURL /api）
const request: AxiosInstance = createAuthHttp({ baseURL: '/api', timeout: 30000 })

/** 便捷方法：请求成功时直接返回 data 字段 */
export async function getData<T>(url: string, params?: object): Promise<T> {
  const resp = await request.get<ApiResponse<T>>(url, { params })
  return resp.data.data
}

export async function postData<T>(url: string, body?: object): Promise<T> {
  const resp = await request.post<ApiResponse<T>>(url, body)
  return resp.data.data
}

export default request
