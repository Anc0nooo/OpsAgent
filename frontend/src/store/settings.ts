/**
 * 模型配置状态（全局共享，多用户架构）
 * - settingsStatus 响应式共享：侧边栏（API 状态显示）、App.vue（首次弹窗判断）共用
 * - refreshSettingsStatus：登录后/需要时拉取 GET /api/settings/status（后端读当前用户 user_configs 表）
 * - 保存配置成功后调用刷新，侧边栏状态实时更新
 */
import { ref } from 'vue'
import { getSettingsStatus, type SettingsStatus } from '../api/settings'
import { getToken } from '../api/token'

/** 当前用户的模型配置状态（null = 未拉取/未登录） */
export const settingsStatus = ref<SettingsStatus | null>(null)

/** 拉取当前用户配置状态（未登录则清空） */
export async function refreshSettingsStatus(): Promise<void> {
  if (!getToken()) {
    settingsStatus.value = null
    return
  }
  try {
    settingsStatus.value = await getSettingsStatus()
  } catch {
    // 后端未启动/网络异常时静默，保持旧值
  }
}

/** 保存配置成功后的即时更新（不等接口，侧边栏立刻变绿） */
export function markConfigured(): void {
  if (settingsStatus.value) {
    settingsStatus.value = {
      ...settingsStatus.value,
      dashscope_configured: true,
      has_api_key: true,
    }
  } else {
    void refreshSettingsStatus()
  }
}
