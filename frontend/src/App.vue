<script setup lang="ts">
/**
 * 应用根组件：左侧边栏 + 右侧主内容区（经典对话应用布局）
 * - 左侧 AppSidebar：新建对话 + 搜索 + 历史列表 + 设置
 * - 右侧：顶部标题栏（会话标题 + 对话/知识库切换 + 设置）+ RouterView
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SettingsDialog from './components/SettingsDialog.vue'
import AppSidebar from './components/AppSidebar.vue'
import { loadUser } from './store/user'
import { refreshSettingsStatus, settingsStatus } from './store/settings'
import { getToken } from './api/token'

const route = useRoute()
const router = useRouter()

// ---------------- 侧边栏状态 ----------------
const sidebarCollapsed = ref(false)
const currentSessionId = ref<string | null>(null)
const sidebarRef = ref<InstanceType<typeof AppSidebar> | null>(null)

// 移动端检测：< 768px 自动折叠
const isMobile = ref(false)
function checkMobile() {
  isMobile.value = window.innerWidth < 768
  if (isMobile.value) {
    sidebarCollapsed.value = true
  }
}

// ---------------- 主视图切换（对话/知识库） ----------------
const currentView = computed(() => {
  if (route.path === '/knowledge') return 'knowledge'
  return 'chat'
})

function switchView(view: 'chat' | 'knowledge') {
  if (view === 'knowledge') {
    router.push('/knowledge')
  } else {
    router.push('/')
  }
}

// ---------------- 顶部标题栏标题 ----------------
const headerTitle = computed(() => {
  if (route.path === '/knowledge') return '知识库管理'
  return '运维对话'
})

// ---------------- 设置弹窗 ----------------
const showSettings = ref(false)
const isFirstTime = ref(false)
const checkedAuto = ref(false)

async function checkAutoPopup() {
  if (checkedAuto.value) return
  checkedAuto.value = true
  // 拉取当前用户配置状态（存全局 store，侧边栏共用）
  await refreshSettingsStatus()
  const st = settingsStatus.value
  const hasKey = !!st && st.chat_configured && st.dashscope_configured
  const dismissedAt = localStorage.getItem('opsagent_settings_dismissed_at')
  const within24h = dismissedAt && (Date.now() - parseInt(dismissedAt, 10)) < 24 * 60 * 60 * 1000
  if (!hasKey && !within24h) {
    isFirstTime.value = true
    showSettings.value = true
  }
}

function onSaved() {
  showSettings.value = false
  // 保存成功后刷新全局配置状态（侧边栏实时更新）
  void refreshSettingsStatus()
}

function onLater() {
  localStorage.setItem('opsagent_settings_dismissed_at', String(Date.now()))
  showSettings.value = false
}

function openSettingsFromChild(e: Event) {
  const detail = (e as CustomEvent).detail || {}
  isFirstTime.value = !!detail.firstTime
  showSettings.value = true
}

// ---------------- 侧边栏事件处理 ----------------
function onNewChat() {
  currentSessionId.value = null
  // 如果当前在知识库页，切回对话页
  if (route.path !== '/') {
    router.push('/')
  }
  // 通知 ChatView 清空当前会话
  window.dispatchEvent(new CustomEvent('opsagent:new-chat'))
}

function onSelectSession(sessionId: string) {
  currentSessionId.value = sessionId
  // 切到对话页并携带 session 参数
  if (route.path !== '/') {
    router.push(`/?session=${sessionId}`)
  } else {
    // 已在对话页，通过事件通知 ChatView 加载会话
    window.dispatchEvent(new CustomEvent('opsagent:load-session', { detail: { sessionId } }))
  }
  // 移动端自动折叠
  if (window.innerWidth < 768) {
    sidebarCollapsed.value = true
  }
}

function onToggleCollapse() {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

function openSettings() {
  isFirstTime.value = false
  showSettings.value = true
}

// ---------------- 监听 ChatView 会话变化（同步 currentSessionId） ----------------
function onSessionCreated(e: Event) {
  const detail = (e as CustomEvent).detail
  if (detail?.sessionId) {
    currentSessionId.value = detail.sessionId
    // 刷新侧边栏列表
    sidebarRef.value?.refreshSessions()
  }
}

onMounted(() => {
  checkMobile()
  // 已登录时才拉用户信息 / 检查配置（未登录由路由守卫跳 /login）
  if (getToken()) {
    loadUser()
    checkAutoPopup()
  }
  window.addEventListener('opsagent:open-settings', openSettingsFromChild)
  window.addEventListener('opsagent:session-created', onSessionCreated)
  window.addEventListener('resize', checkMobile)
})
onBeforeUnmount(() => {
  window.removeEventListener('opsagent:open-settings', openSettingsFromChild)
  window.removeEventListener('opsagent:session-created', onSessionCreated)
  window.removeEventListener('resize', checkMobile)
})
</script>

<template>
  <!-- 登录/注册页：独立全屏布局（无侧边栏） -->
  <div v-if="route.path === '/login'" class="login-shell">
    <RouterView />
  </div>

  <!-- 管理后台：独立全屏布局（无侧边栏，仅管理员可访问） -->
  <div v-else-if="route.path === '/admin'" class="admin-shell">
    <RouterView />
  </div>

  <div v-else class="app-layout">
    <!-- 移动端遮罩 -->
    <div
      v-if="!sidebarCollapsed && isMobile"
      class="sidebar-overlay"
      @click="sidebarCollapsed = true"
    />

    <!-- 左侧边栏 -->
    <AppSidebar
      ref="sidebarRef"
      :collapsed="sidebarCollapsed"
      :current-session-id="currentSessionId"
      @new-chat="onNewChat"
      @select-session="onSelectSession"
      @toggle-collapse="onToggleCollapse"
      @open-settings="openSettings"
    />

    <!-- 右侧主内容区 -->
    <div class="main-area">
      <!-- 顶部标题栏 -->
      <header class="main-header">
        <div class="header-title">{{ headerTitle }}</div>
        <div class="header-actions">
          <!-- 对话/知识库切换 -->
          <div class="view-switcher">
            <button
              class="switch-btn"
              :class="{ active: currentView === 'chat' }"
              @click="switchView('chat')"
            >对话</button>
            <button
              class="switch-btn"
              :class="{ active: currentView === 'knowledge' }"
              @click="switchView('knowledge')"
            >知识库</button>
          </div>
          <!-- API Key 状态指示 -->
          <span
            v-if="settingsStatus && (!settingsStatus.chat_configured || !settingsStatus.dashscope_configured)"
            class="key-indicator"
            title="API Key 未配置，点击设置"
            @click="() => { isFirstTime = true; showSettings = true }"
          >⚠ 未配置</span>
          <!-- 设置按钮 -->
          <button
            class="settings-btn"
            :title="(settingsStatus?.chat_configured && settingsStatus?.dashscope_configured) ? '设置' : '模型未配置，请点击配置'"
            @click="openSettings"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
          </button>
        </div>
      </header>

      <!-- 主内容 -->
      <main class="main-content">
        <RouterView />
      </main>
    </div>

    <!-- 设置弹窗 -->
    <SettingsDialog
      v-model:visible="showSettings"
      :first-time="isFirstTime"
      @saved="onSaved"
      @update:visible="(v) => { if (!v && isFirstTime) onLater(); }"
    />
  </div>
</template>

<style scoped>
/* 登录页独立布局外壳 */
.login-shell {
  height: 100vh;
  overflow: hidden;
}

/* 管理后台独立布局外壳 */
.admin-shell {
  height: 100vh;
  overflow: auto;
  background: #f5f5f7;
}

.app-layout {
  height: 100vh;
  display: flex;
  overflow: hidden;
}

/* 移动端遮罩 */
.sidebar-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.3);
  z-index: 99;
}

/* 右侧主区 */
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

/* 顶部标题栏 */
.main-header {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  height: 52px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border);
  z-index: 10;
}
.header-title {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--text-main);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
}

/* 对话/知识库切换器 */
.view-switcher {
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.04);
}
.switch-btn {
  padding: 5px 14px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-sub);
  font-size: 12.5px;
  cursor: pointer;
  transition: all 0.15s;
}
.switch-btn:hover {
  color: var(--text-main);
}
.switch-btn.active {
  background: #fff;
  color: var(--primary);
  font-weight: 500;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

/* API Key 状态指示 */
.key-indicator {
  padding: 4px 10px;
  border-radius: 6px;
  background: rgba(245, 63, 45, 0.08);
  color: #f53f2d;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.key-indicator:hover {
  background: rgba(245, 63, 45, 0.15);
}

/* 设置按钮 */
.settings-btn {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-sub);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.settings-btn:hover {
  color: var(--primary);
  background: var(--primary-weak);
}

/* 主内容 */
.main-content {
  flex: 1;
  overflow: hidden;
}

/* 移动端：顶部标题栏左侧加菜单按钮（由侧边栏折叠按钮替代，这里不需要额外） */
@media (max-width: 768px) {
  .main-header {
    padding: 0 12px;
  }
  .header-title {
    font-size: 13.5px;
  }
}
</style>
