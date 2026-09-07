/**
 * API 接口定义
 * 阶段 0：仅健康检查；对话/知识库等接口随后续阶段补充
 */
import { getData } from './request'

/** 健康检查返回结构 */
export interface HealthInfo {
  status: string
  app: string
  version: string
  sql_dialect: string
  chat_model: string
  embed_model: string
  rerank_model: string
  api_key_configured: boolean
}

/** 健康检查 */
export function getHealth() {
  return getData<HealthInfo>('/health')
}
